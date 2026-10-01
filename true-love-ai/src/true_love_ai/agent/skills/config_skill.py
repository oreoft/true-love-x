# -*- coding: utf-8 -*-
"""配置重载 Skill（重载 AI 配置）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("ConfigSkill")


@register_skill({
    "type": "function",
    "function": {
        "name": "reload_config",
        "description": (
            "重新加载 AI 服务配置文件，使配置修改立即生效，无需重启服务。"
            "当用户说'更新配置'、'重载配置'、'reload config'时使用。"
        ),
        "parameters": {"type": "object", "properties": {}}
    }
})
async def reload_config(params: dict, ctx: dict) -> str:
    from true_love_ai.core.config import reload_config as _reload
    try:
        _reload()
        LOG.info("AI 配置已重载, 操作者: %s", ctx.get("sender_id", ""))
        return "好耶~AI 服务配置已重新加载成功！"
    except Exception as e:
        raise SkillFailed(f"呜呜~配置重载失败了: {e}") from e
