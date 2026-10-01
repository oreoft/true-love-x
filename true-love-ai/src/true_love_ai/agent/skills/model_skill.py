# -*- coding: utf-8 -*-
"""模型管理 Skill（管理员专用）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill
from true_love_ai.core.model_registry import CATEGORIES

LOG = logging.getLogger("ModelSkill")


@register_skill({
    "type": "function",
    "function": {
        "name": "list_models",
        "description": "查看当前所有类别的模型配置。当用户问'现在用的什么模型'、'模型配置是什么'时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
})
async def list_models(params: dict, ctx: dict) -> str:
    from true_love_ai.core.model_registry import get_model_registry
    models = get_model_registry().all()
    lines = ["当前模型配置："]
    for category, entries in models.items():
        default = entries.get("default", "")
        fallback = entries.get("fallback", "")
        if fallback:
            lines.append(f"  {category}: {default}  (备用: {fallback})")
        else:
            lines.append(f"  {category}: {default}")
    return "\n".join(lines)


@register_skill({
    "type": "function",
    "function": {
        "name": "set_model",
        "description": (
            "修改指定类别的模型，所有机器人共用，修改后立即生效并存进库里，重启后仍保留。"
            "当用户说'把聊天模型换成xxx'、'图片生成改用xxx'时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": CATEGORIES,
                    "description": "模型类别",
                },
                "key": {
                    "type": "string",
                    "enum": ["default", "fallback"],
                    "description": "default=主力模型，fallback=主力失败时的备用",
                },
                "value": {
                    "type": "string",
                    "description": "完整的 LiteLLM 模型字符串，如 openai/gpt-5.5 或 openai/claude/claude-4",
                },
            },
            "required": ["category", "key", "value"],
        },
    }
})
async def set_model(params: dict, ctx: dict) -> str:
    category = params.get("category", "").strip()
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()

    if not category or not key or not value:
        return "参数不完整，需要 category、key、value 三个参数。"

    try:
        from true_love_ai.core.model_registry import get_model_registry
        registry = get_model_registry()
        old = registry.get(category, key) if key in registry.all().get(category, {}) else "（未配置）"
        registry.set(category, key, value)
        LOG.info("模型已更新: %s.%s: %s → %s", category, key, old, value)
        return f"好的，已将 {category}.{key} 从 {old} 更新为 {value}，已保存。"
    except ValueError as e:
        # 类别或模型名不对，原因在话里
        return f"更新失败: {e}"
    except Exception as e:
        raise SkillFailed(f"更新失败: {e}") from e
