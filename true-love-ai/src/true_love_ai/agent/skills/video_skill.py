# -*- coding: utf-8 -*-
"""视频生成 Skill（复用 AI 现有 video_service）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("VideoSkill")


@register_skill({
    "type": "function",
    "notify": [
        "视频生成需要一点时间，本魔法师正在努力施法中，请耐心等待哦～🎬",
        "收到！正在为你制作视频，这个比较慢，请耐心等我哦～🎥",
        "嗯嗯！视频正在生成中，稍微等我久一点点哦～✨",
    ],
    "function": {
        "name": "generate_video",
        "description": (
            "根据文字描述或图片生成短视频。"
            "当用户说'生成视频...','帮我做一个视频...'时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "视频描述（英文 prompt 效果更好）"
                }
            },
            "required": ["prompt"]
        }
    }
})
async def generate_video(params: dict, ctx: dict) -> str:
    prompt = params.get("prompt", "")
    receiver = ctx.get("receiver", "")

    if not prompt:
        return "诶嘿~请告诉我你想要什么样的视频哦~"

    from true_love_ai.services.video_service import GEN_VIDEO_DIR, VideoService
    try:
        result = await VideoService().generate_video(content=prompt)
    except Exception as e:
        raise SkillFailed("呜呜~视频生成出错了捏~") from e
    if not result or not result.video_id:
        raise SkillFailed("呜呜~视频生成失败了捏，稍后再试试吧~")

    from true_love_ai.agent.server_client import send_file
    if not await send_file(receiver, f"{GEN_VIDEO_DIR.name}/{result.video_id}.mp4"):
        raise SkillFailed("呜呜~视频生成好了但是发送失败了捏，稍后再试试吧~")
    return "好耶~视频已生成并发送！"
