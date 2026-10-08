# -*- coding: utf-8 -*-
"""视频生成 Skill（复用 AI 现有 video_service）"""
import logging
import time

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("VideoSkill")

# 每个会话最近一条视频的 Omni id，用来在它上面接着改；生成的文件 48 小时后过期，记录也跟着失效
_LAST_VIDEO: dict[str, tuple[str, float]] = {}
_LAST_VIDEO_TTL = 48 * 3600

MAX_SECONDS = 40


@register_skill({
    "type": "function",
    "notify": [
        "视频生成需要几分钟，本魔法师正在努力施法中，请耐心等待哦～🎬",
        "收到！正在为你制作视频，这个比较慢，要等几分钟哦～🎥",
        "嗯嗯！视频正在生成中，大概要几分钟，稍微等我久一点点哦～✨",
    ],
    # 30 秒的视频要生成 3 轮，每轮半分钟到两分钟
    "timeout": 900,
    "function": {
        "name": "generate_video",
        "description": (
            "根据文字描述生成短视频，自带配音。"
            "当用户说'生成视频...','帮我做一个视频...'时使用；"
            "用户要在刚生成的视频上修改（'让它跳高一点'、'背景换成雪地'、'改成...'）时也用它，并把 edit_last 设为 true。"
            "下面的可选参数用户没提就不要传，用默认值。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "视频画面和情节描述；修改时写要怎么改"
                },
                "seconds": {
                    "type": "integer",
                    "description": f"视频时长（秒），默认 30，最长 {MAX_SECONDS}。用户说了时长才传"
                },
                "orientation": {
                    "type": "string",
                    "enum": ["portrait", "landscape"],
                    "description": "画面方向，默认竖屏 portrait；用户明确要横屏才传 landscape"
                },
                "resolution": {
                    "type": "string",
                    "enum": ["360p", "720p", "1080p", "4k"],
                    "description": "清晰度，默认 720p；用户明确要求才传"
                },
                "dialogue": {
                    "type": "string",
                    "description": "角色要说的台词，按用户的语言原样写；用户没给台词时可以按情节自己编一两句"
                },
                "edit_last": {
                    "type": "boolean",
                    "description": "在这个聊天里刚生成的那条视频上修改，而不是重新生成"
                },
            },
            "required": ["prompt"]
        }
    }
})
async def generate_video(params: dict, ctx: dict) -> str:
    prompt = params.get("prompt", "")
    receiver = ctx.get("receiver", "")
    session_id = ctx.get("session_id", "")

    if not prompt:
        return "诶嘿~请告诉我你想要什么样的视频哦~"

    from true_love_ai.services.video_service import GEN_VIDEO_DIR, VideoService
    options = _options(params)
    if params.get("edit_last"):
        last = _LAST_VIDEO.get(session_id)
        if not last or time.time() - last[1] > _LAST_VIDEO_TTL:
            return "这个聊天里最近没有我生成的视频（或者已经过期了），没法在上面改，要不直接重新生成一个？"
        options.previous_id = last[0]

    try:
        result = await VideoService().generate_video(content=prompt, options=options)
    except Exception as e:
        raise SkillFailed("呜呜~视频生成出错了捏~") from e
    if not result or not result.video_id:
        raise SkillFailed("呜呜~视频生成失败了捏，稍后再试试吧~")
    if result.interaction_id:
        _LAST_VIDEO[session_id] = (result.interaction_id, time.time())

    from true_love_ai.agent.server_client import send_file
    if not await send_file(receiver, f"{GEN_VIDEO_DIR.name}/{result.video_id}.mp4"):
        raise SkillFailed("呜呜~视频生成好了但是发送失败了捏，稍后再试试吧~")
    return "好耶~视频已生成并发送！"


def _options(params: dict):
    """模型给了就用，没给或给错了用默认值"""
    from true_love_ai.services.video_service import VideoOptions
    opts = VideoOptions()
    try:
        seconds = int(params.get("seconds") or opts.seconds)
    except (TypeError, ValueError):
        seconds = opts.seconds
    opts.seconds = max(1, min(seconds, MAX_SECONDS))
    if params.get("orientation") == "landscape":
        opts.aspect_ratio = "16:9"
    if params.get("resolution") in ("360p", "720p", "1080p", "4k"):
        opts.resolution = params["resolution"]
    opts.dialogue = (params.get("dialogue") or "").strip()
    return opts
