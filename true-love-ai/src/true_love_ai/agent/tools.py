# -*- coding: utf-8 -*-
"""
把 skill_registry 里的技能交给 pydantic_ai 当工具用

技能还是原来的写法（OpenAI function schema + async handler(params, ctx)），这里只做适配：
- 每一步按权限点现筛：没权限的技能模型看不到（执行时 skill_registry 再查一遍）
- 调用轮次到了上限就不再给工具，模型只能拿已有的结果直接回答
- skill_run 的说明里列出当前这个人能用的动态技能
- 执行前发预通知、按技能声明的超时执行、失败转成回给模型的话，并记下哪些技能失败了
- 参数必须是 JSON 对象，不是的话 pydantic_ai 把错误回给模型让它重新调，技能不会拿着空参数执行
"""

import asyncio
import copy
import logging
import random
from dataclasses import dataclass, field, replace

from pydantic_ai import RunContext, Tool
from pydantic_ai._function_schema import FunctionSchema
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset
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

# 参数只要求是 JSON 对象，字段由技能自己检查（和改造前一样）
_ARGS_VALIDATOR = SchemaValidator(core_schema.dict_schema(keys_schema=core_schema.str_schema()))


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
    # 前几步可以调技能，之后不再给工具
    max_tool_rounds: int = 6
    # 执行过的技能和是否失败，按执行顺序
    calls: list[tuple[str, bool]] = field(default_factory=list)

    def unrecovered_failures(self) -> list[str]:
        """失败了、之后也没再成功过的技能；模型重试成功了的不算"""
        last: dict[str, bool] = {}
        for name, failed in self.calls:
            last.pop(name, None)
            last[name] = failed
        return [name for name, failed in last.items() if failed]


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


async def _prepare(ctx: RunContext[AgentDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
    deps = ctx.deps
    if ctx.run_step > deps.max_tool_rounds or not check_permission(tool_def.name, deps.access):
        return None
    if tool_def.name == DYNAMIC_SKILL_RUNNER:
        hints = dynamic_skill_hints(deps.access)
        if hints:
            return replace(tool_def, description=f"{tool_def.description}\n可用的动态技能：\n{hints}")
    return tool_def


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


def _parameters(schema: dict) -> dict:
    params = copy.deepcopy(schema.get("function", {}).get("parameters") or {"type": "object"})
    if isinstance(params.get("properties"), dict) and not params["properties"]:
        params.pop("properties", None)
        params.pop("required", None)
    params.setdefault("type", "object")
    return params


def _tool(name: str, schema: dict) -> Tool[AgentDeps]:
    description = schema["function"].get("description", "")

    async def run(ctx: RunContext[AgentDeps], **args) -> str:
        result, failed = await execute_skill(name, args, ctx.deps)
        ctx.deps.calls.append((name, failed))
        return result

    function_schema = FunctionSchema(function=run, name=name, description=description, validator=_ARGS_VALIDATOR,
                                     json_schema=_parameters(schema), takes_ctx=True, is_async=True)
    # strict=False：pydantic_ai 遇到 OpenAI 模型会自动开 strict 并改写 schema（加 additionalProperties: false 等），
    # 和改造前发给模型的工具定义不一样，Gemini 等经 LiteLLM 转发时也不一定认，保持原样
    return Tool(run, takes_ctx=True, name=name, description=description, function_schema=function_schema,
                max_retries=TOOL_ARG_RETRIES, prepare=_prepare, strict=False)


def build_toolset() -> FunctionToolset[AgentDeps]:
    """所有内置技能；每一步给不给模型看由 _prepare 决定"""
    return FunctionToolset([_tool(name, schema) for name, schema in skill_registry.schemas().items()])
