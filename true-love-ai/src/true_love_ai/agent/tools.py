# -*- coding: utf-8 -*-
"""
把 skill_registry 里的技能交给 pydantic_ai 当工具用

技能还是原来的写法（OpenAI function schema + async handler(params, ctx)），SkillToolset 只做适配：
- 每一步按权限点现筛：没权限的技能模型看不到（执行时 skill_registry 再查一遍）
- skill_run 的说明里列出当前这个人能用的动态技能
- 执行前发预通知、按技能声明的超时执行、失败转成回给模型的话，并记下每次调用（见 SkillCall）
- 参数必须是 JSON 对象，不是的话 pydantic_ai 把错误回给模型让它重新调，技能不会拿着空参数执行
- 技能轮次到了上限：AgentLoop 让模型别再调（tool_choice=none），模型硬要调的也不执行，只回它一句"直接回答"
"""

import asyncio
import copy
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from true_love_ai.agent import server_client, skill_registry
from true_love_ai.agent.skills.permission import PermissionDenied, check_permission

# 技能执行的日志沿用 AgentLoop 这个名字，排查时和"回复结局"一起搜
LOG = logging.getLogger("AgentLoop")

# 单个 skill 执行超时（秒），skill 可以在 schema 里用 timeout 自己声明
SKILL_TIMEOUT_SECONDS = 300
# 同一个技能参数连续出错几次后放弃（交给 AgentLoop 当模型出错处理）
TOOL_ARG_RETRIES = 3
DYNAMIC_SKILL_RUNNER = "skill_run"
ROUNDS_USED_UP = "[技能调用次数已用完] 这个技能没有执行，请根据已经拿到的结果直接回答用户"

# 参数只要求是 JSON 对象，字段由技能自己检查（和改造前一样）
_ARGS_VALIDATOR = SchemaValidator(core_schema.dict_schema(keys_schema=core_schema.str_schema()))


@dataclass
class SkillCall:
    """执行过的一次技能调用"""
    name: str
    args: dict
    call_id: str
    result: str
    failed: bool


@dataclass
class AgentDeps:
    """一条消息的处理过程中，技能要用到的上下文"""
    # 交给技能的 ctx（session_id、sender_id、sender_name、is_group、receiver、at_user、platform、bot_id、chat）
    skill_ctx: dict
    # 谁在哪里问的，按权限点筛技能用（platform、bot_id、sender_id、is_group、chat）
    access: dict
    # 这次给模型的 instructions（人设、规则、摘要），见 prompt.instructions
    instructions: str
    # 执行技能前发不发"正在…"的预通知；自动触发时不发
    notify: bool = True
    # 前几步可以调技能，之后不再执行
    max_tool_rounds: int = 6
    # 执行过的技能调用，按完成顺序
    calls: list[SkillCall] = field(default_factory=list)
    # 这条消息里能用的动态技能说明，第一次用到时查一次库
    _dynamic_hints: Optional[str] = None

    def rounds_used_up(self, run_step: int) -> bool:
        return run_step > self.max_tool_rounds

    def dynamic_hints(self) -> str:
        if self._dynamic_hints is None:
            self._dynamic_hints = dynamic_skill_hints(self.access)
        return self._dynamic_hints

    def unrecovered_failures(self) -> list[str]:
        """失败了、之后也没再成功过的技能；模型重试成功了的不算"""
        last: dict[str, bool] = {}
        for call in self.calls:
            last.pop(call.name, None)
            last[call.name] = call.failed
        return [name for name, failed in last.items() if failed]

    def executed_steps(self) -> list[ModelMessage]:
        """
        执行过的技能调用，写成模型的调用和技能的结果；模型半路出错时用它记历史，
        免得下一轮模型以为没办过（比如提醒已经设上了又设一遍）
        """
        if not self.calls:
            return []
        return [
            ModelResponse(parts=[ToolCallPart(c.name, c.args, tool_call_id=c.call_id) for c in self.calls]),
            ModelRequest(parts=[ToolReturnPart(c.name, c.result, tool_call_id=c.call_id) for c in self.calls]),
        ]


async def execute_skill(name: str, args: dict, deps: AgentDeps) -> tuple[str, bool]:
    """执行单个技能，返回 (回给模型的结果, 是否失败)；notify 为 False 时不发技能的预通知"""
    LOG.info("执行 tool: %s, args=%s", name, str(args)[:200])
    timeout = skill_registry.get_timeout(name, SKILL_TIMEOUT_SECONDS)
    try:
        notice = skill_registry.get_notify(name) if deps.notify else None
        if notice:
            text = random.choice(notice) if isinstance(notice, list) else notice
            if not await server_client.send_text(deps.skill_ctx["receiver"], text, deps.skill_ctx["at_user"]):
                LOG.warning("tool %s 的预通知没发出去: receiver=%s", name, deps.skill_ctx["receiver"])

        result = await asyncio.wait_for(skill_registry.execute(name, args, deps.skill_ctx), timeout=timeout)
        LOG.info("tool %s 执行结果: %s", name, str(result)[:200])
        return str(result), False
    except asyncio.TimeoutError:
        LOG.error("tool %s 执行超时 (%ds)", name, timeout)
        return f"[执行超时] 技能 {name} 处理时间过长，请稍后重试", True
    except skill_registry.SkillFailed as e:
        # 包了原异常的带上堆栈；技能自己判定的失败（比如发送接口返回失败）原因已经在话里了
        LOG.error("tool %s 失败: %s", name, e, exc_info=e.__cause__ is not None)
        return str(e), True
    except PermissionDenied as e:
        return str(e), False
    except Exception as e:
        LOG.exception("tool %s 执行异常: %s", name, e)
        return f"[执行失败] {e}", True


def dynamic_skill_hints(access: dict) -> str:
    """当前这个人在这里能用的动态技能，一行一个"""
    from true_love_ai.memory import dynamic_skill_service
    try:
        skills = dynamic_skill_service.list_skills()
    except Exception as e:
        LOG.warning("加载动态技能列表失败: %s", e, exc_info=True)
        return ""
    return "\n".join(f"- {s['id']}（{s['name']}）: {s['description']}" for s in skills
                     if check_permission(s["id"], access))


def tool_definition(schema: dict) -> ToolDefinition:
    """技能的 OpenAI function schema → pydantic_ai 的工具定义；没有参数的不带空的 properties（和改造前发的一样）"""
    function = schema["function"]
    params = copy.deepcopy(function.get("parameters") or {"type": "object"})
    if isinstance(params.get("properties"), dict) and not params["properties"]:
        params.pop("properties", None)
        params.pop("required", None)
    params.setdefault("type", "object")
    # strict=False：pydantic_ai 遇到 OpenAI 模型会自动开 strict 并改写 schema，Gemini 等经 LiteLLM 转发时也不一定认
    return ToolDefinition(name=function["name"], description=function.get("description", ""),
                          parameters_json_schema=params, strict=False)


class SkillToolset(AbstractToolset[AgentDeps]):
    """所有内置技能；每一步给模型看哪些、调了怎么执行，都按这条消息的 AgentDeps 来"""

    @property
    def id(self) -> str:
        return "tl-ai-skills"

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        deps = ctx.deps
        tools = {}
        for name, schema in skill_registry.schemas().items():
            if not check_permission(name, deps.access):
                continue
            tool_def = tool_definition(schema)
            if name == DYNAMIC_SKILL_RUNNER and deps.dynamic_hints():
                tool_def.description = f"{tool_def.description}\n可用的动态技能：\n{deps.dynamic_hints()}"
            tools[name] = ToolsetTool(toolset=self, tool_def=tool_def, max_retries=TOOL_ARG_RETRIES,
                                      args_validator=_ARGS_VALIDATOR)
        return tools

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentDeps],
                        tool: ToolsetTool[AgentDeps]) -> str:
        deps = ctx.deps
        if deps.rounds_used_up(ctx.run_step):
            LOG.warning("技能轮次已用完，模型还在调 %s，不执行", name)
            return ROUNDS_USED_UP
        result, failed = await execute_skill(name, tool_args, deps)
        deps.calls.append(SkillCall(name, tool_args, ctx.tool_call_id or "", result, failed))
        return result


def has_any_skill(access: dict) -> bool:
    """这个人在这里有没有能用的技能"""
    return any(check_permission(name, access) for name in skill_registry.names())
