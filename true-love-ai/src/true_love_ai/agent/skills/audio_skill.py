# -*- coding: utf-8 -*-
"""语音合成 Skill（复用 AI 现有 audio_service）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("AudioSkill")


@register_skill({
    "type": "function",
    "notify": [
        "正在张嘴练习发声中，请稍等一下下哦～🎤",
        "收到！马上帮你把文字变成声音，稍等哦～🔊",
        "嗯嗯！语音生成中，请耐心等我哦～🎶",
    ],
    "function": {
        "name": "generate_audio",
        "description": (
            "把文字转换成语音发送给用户。"
            "当用户说'说一段语音...','念给我听...','发语音...'时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要转换成语音的文字内容"
                }
            },
            "required": ["text"]
        }
    }
})
async def generate_audio(params: dict, ctx: dict) -> str:
    text = params.get("text", "")
    receiver = ctx.get("receiver", "")

    if not text:
        return "诶嘿~请告诉我你想让我说什么哦~"

    from true_love_ai.memory import persona_service
    from true_love_ai.services.audio_service import AudioService, GEN_AUDIO_DIR
    # 语音风格跟着人设走：receiver 就是这次回复的群或私聊对象
    style = persona_service.resolve(ctx.get("bot_id", ""), receiver).voice_style
    try:
        result = await AudioService().text_to_speech(text=text, style=style)
    except Exception as e:
        raise SkillFailed("呜呜~语音生成出错了捏~") from e
    if not result or not result.audio_id:
        raise SkillFailed("呜呜~语音生成失败了捏，稍后再试试吧~")

    from true_love_ai.agent.server_client import send_file
    if not await send_file(receiver, f"{GEN_AUDIO_DIR.name}/{result.audio_id}.wav"):
        raise SkillFailed("呜呜~语音生成好了但是发送失败了捏，稍后再试试吧~")
    return "好耶~语音已生成并发送！"
