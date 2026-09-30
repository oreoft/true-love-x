# -*- coding: utf-8 -*-
"""文件分析 Skill - 把文件传给 LLM 原生分析"""
import logging

from true_love_ai.agent.skill_registry import register_skill

LOG = logging.getLogger("FileSkill")

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _detect_mime(file_path: str) -> str:
    suffix = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return _MIME_MAP.get(suffix, "")


@register_skill({
    "type": "function",
    "notify": [
        "正在认真阅读文件中，请耐心等我一下哦～📖",
        "收到文件啦！正在仔细研究，稍等一下哦～🔍",
        "嗯嗯！马上帮你解读文件，请稍候哦～✨",
    ],
    "function": {
        "name": "read_file",
        "description": (
                "分析文件内容，支持 PDF、图片等格式，由 LLM 直接理解文件内容。"
                "当用户发送文件（消息中含 [文件:...] 或 [引用文件:...]）并要求分析时使用。"
                "从消息中提取 [文件:xxx] 或 [引用文件:xxx] 中的路径传入 file_path。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径，原样从消息中提取，如 http://h-m8s:5000/media/wx_imgs/document.pdf"
                },
                "question": {
                    "type": "string",
                    "description": "关于文件内容的问题或分析请求"
                }
            },
            "required": ["file_path"]
        }
    }
})
async def read_file(params: dict, ctx: dict) -> str:
    file_path = params.get("file_path", "")
    question = params.get("question", "请分析这份文件的内容")
    platform = ctx.get("platform", "wechat")

    if not file_path:
        return "诶嘿~请提供文件路径哦~"

    mime_type = _detect_mime(file_path)
    if not mime_type:
        return f"呜呜~暂不支持该文件格式：{file_path}"

    try:
        import base64
        from true_love_ai.agent.server_client import fetch_media_bytes
        data = await fetch_media_bytes(file_path, platform=platform)
        if not data:
            return "呜呜~文件获取失败了捏，可能文件不存在~"

        b64 = base64.b64encode(data).decode()
        from true_love_ai.llm.router import get_llm_router
        router = get_llm_router()

        if mime_type.startswith("image/"):
            result = await router.vision(prompt=question, image_data=b64, mime_type=mime_type)
        else:
            result = await router.document(prompt=question, file_data=b64, mime_type=mime_type)

        return result or "呜呜~文件分析失败了捏~"

    except Exception as e:
        LOG.error("read_file error: path=%s err=%s", file_path, e)
        return f"呜呜~文件分析出错了捏：{e}"
