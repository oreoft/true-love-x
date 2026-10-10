# -*- coding: utf-8 -*-
"""
动态技能管理：skill_save + skill_run

skill_save: 管理员将 shell 命令保存为可复用技能
skill_run:  执行已保存的动态技能
"""

import asyncio
import json
import logging
import os
import re

from true_love_ai.agent.skill_registry import SkillFailed, register_skill
from true_love_ai.agent.skills.permission import DENIED_TEXT, check_permission
from true_love_ai.memory import dynamic_skill_service as _ss
from true_love_ai.memory import skill_access_service

LOG = logging.getLogger("DynamicSkillManage")

_EXEC_TIMEOUT = 30
_OUTPUT_LIMIT = 2000


def _build_command(command: str, param_defs: dict, overrides: dict) -> tuple[str, dict[str, str]]:
    """
    命令模板里的 {name} 换成对环境变量的引用，参数值放进环境变量：返回 (命令, 环境变量)

    参数值不拼进命令文本，shell 不会把它当命令解析，换行、引号、$()、反引号都只是普通字符。
    以 - 开头的值会被命令当成选项（比如 curl -o 写文件），直接拒绝。Raises: ValueError
    """
    values = {k: str(v.get("default", "")) if isinstance(v, dict) else "" for k, v in param_defs.items()}
    values.update({k: str(v) for k, v in overrides.items()})
    env: dict[str, str] = {}
    var_of: dict[str, str] = {}
    for i, (name, value) in enumerate(values.items()):
        if value.startswith("-"):
            raise ValueError(f"参数 '{name}' 不能以 - 开头")
        var_of[name] = f"TL_ARG_{i}"
        env[var_of[name]] = value
    return _reference_vars(command, var_of), env


def _reference_vars(command: str, var_of: dict[str, str]) -> str:
    """
    把模板里的 {name} 换成 ${变量}，按它在 shell 里所处的引号决定怎么写，保证展开后还是一个参数、不再被解析：
    引号外写 "${V}"，双引号里写 ${V}，单引号里先关掉单引号再写 "${V}" 再打开（'a{x}b' → 'a'"${V}"'b'）
    """
    if not var_of:
        return command
    placeholder = re.compile("|".join(r"\{%s\}" % re.escape(name) for name in var_of))
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        m = placeholder.match(command, i)
        if m:
            ref = "${%s}" % var_of[m.group()[1:-1]]
            out.append({"": f'"{ref}"', '"': ref, "'": f"'\"{ref}\"'"}[quote])
            i = m.end()
            continue
        ch = command[i]
        if ch == "\\" and quote != "'":
            out.append(command[i:i + 2])
            i += 2
            continue
        if ch in "'\"" and quote in ("", ch):
            quote = "" if quote else ch
        out.append(ch)
        i += 1
    return "".join(out)


@register_skill({
    "type": "function",
    "function": {
        "name": "skill_save",
        "description": (
            "将一段 shell 命令保存为可复用的动态技能，方便以后直接调用。"
            "当用户说【把这个命令保存下来】【以后直接用这个查询】等意图时调用。"
            "仅管理员可以保存。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "技能唯一ID，英文小写+下划线，如 query_pypi_version",
                },
                "name": {
                    "type": "string",
                    "description": "技能名称，如「查询PyPI包最新版本」",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "触发场景描述，会注入进 LLM 上下文供识别触发时机使用，"
                        "示例：「查询某Python包在PyPI的最新版本，用户说查询xxx版本时调用」"
                    ),
                },
                "command": {
                    "type": "string",
                    "description": "shell 命令，可用 {param_name} 作为参数占位符，如 curl https://pypi.org/pypi/{package}/json",
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "参数定义（可选），格式：{参数名: {default: 默认值, desc: 描述}}，"
                        "如 {\"package\": {\"default\": \"wxautox4\", \"desc\": \"包名\"}}"
                    ),
                },
            },
            "required": ["id", "name", "description", "command"],
        },
    },
})
async def skill_save(params: dict, ctx: dict) -> str:
    skill_id = params.get("id", "").strip()
    name = params.get("name", "").strip()
    description = params.get("description", "").strip()
    command = params.get("command", "").strip()
    param_defs = params.get("parameters") or {}
    creator = ctx.get("sender_id", "")

    # 谁能用：群里装的只在这个群，私聊装的在这个号；要改去 tl-admin 的技能页
    try:
        result = _ss.save_skill(skill_id, name, description, command, param_defs, creator,
                                default_points=skill_access_service.install_points(ctx))
    except ValueError as e:
        # 参数校验没过，原因回给模型
        return str(e)
    except RuntimeError as e:
        # 写库失败，堆栈在 repository 里记了
        raise SkillFailed(str(e)) from e

    action = "更新" if result["is_update"] else "保存"
    LOG.info("dynamic skill %s: id=%s creator=%s", action, skill_id, creator)
    where = "这个群里" if ctx.get("is_group") else "这个号上"
    scope = "" if result["is_update"] else f"默认只有{where}能用，要改范围去后台的技能页。"
    return f"技能「{name}」已{action}（ID: {skill_id}）。{scope}下次直接说触发词就能用了～"


@register_skill({
    "type": "function",
    "function": {
        "name": "skill_run",
        "description": (
            "执行一个已保存的动态技能。"
            "当识别到用户意图与某个已保存动态技能匹配时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "要执行的技能ID",
                },
                "params": {
                    "type": "object",
                    "description": "覆盖技能默认参数的键值对（可选），如 {\"package\": \"requests\"}",
                },
            },
            "required": ["id"],
        },
    },
    "notify": [
        "正在执行技能，请稍候～",
        "命令执行中，马上好～",
    ],
})
async def skill_run(params: dict, ctx: dict) -> str:
    skill_id = params.get("id", "").strip()
    overrides = params.get("params") or {}

    skill = _ss.get_skill(skill_id)
    if not skill:
        return f"未找到技能 '{skill_id}'，请检查 ID 是否正确"

    if not check_permission(skill_id, ctx):
        return DENIED_TEXT

    # 执行前命令安全校验（防止历史数据绕过）
    blocked_reason = _ss.validate_command(skill["command"])
    if blocked_reason:
        LOG.warning("skill_run 拦截: id=%s reason=%s", skill_id, blocked_reason)
        return f"技能执行被拒绝：{blocked_reason}"

    param_defs = json.loads(skill["parameters"]) if skill["parameters"] else {}
    try:
        command, args_env = _build_command(skill["command"], param_defs, overrides)
    except ValueError as e:
        return f"参数错误：{e}"

    LOG.info("执行动态技能: id=%s command=%s args=%s", skill_id, command[:200], str(args_env)[:200])

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, **args_env},
            ),
            timeout=5,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_EXEC_TIMEOUT)
    except asyncio.TimeoutError as e:
        raise SkillFailed(f"技能「{skill['name']}」执行超时（>{_EXEC_TIMEOUT}s）") from e
    except Exception as e:
        raise SkillFailed(f"执行失败：{e}") from e

    output = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        # 命令失败不计使用次数，把输出带给模型说明原因（AgentLoop 记日志）
        raise SkillFailed(f"技能「{skill['name']}」执行失败（退出码 {proc.returncode}）：{output[:_OUTPUT_LIMIT]}")

    _ss.increment_skill_usage(skill_id)

    if not output:
        return f"技能「{skill['name']}」执行完成，无输出"
    if len(output) > _OUTPUT_LIMIT:
        output = output[:_OUTPUT_LIMIT] + f"\n...（输出已截断，共 {len(output)} 字符）"
    return output
