# -*- coding: utf-8 -*-
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any, Optional

from true_love_common.chat_msg import ChatMsg, ImageMsg, VoiceMsg, VideoMsg, FileMsg, LinkMsg, ResourceRef
from true_love_base.utils.path_resolver import get_wx_imgs_dir, to_server_path

LOG = logging.getLogger("MessageConverter")


@dataclass
class WxChatMsg(ChatMsg):
    """base 转出去的微信消息；多出的字段随 to_dict 带给 server，server 不认识的字段会忽略"""
    # 图片、视频、文件没下载下来
    media_failed: bool = False

QUOTE_TYPE_MAP = {
    '图片': 'image', '[图片]': 'image',
    '视频': 'video', '[视频]': 'video',
    '语音': 'voice', '[语音]': 'voice',
    '文件': 'file', '[文件]': 'file',
}


def _find_mention(content: str, msg_type: str, bot_name: str) -> str:
    """
    找出正文里叫到机器人的那段文字，没叫到返回空串

    只认账号昵称：机器人在群里改过群昵称、或者被好友改了备注时，@ 出来的文字
    不是账号昵称，这里认不出来。

    Args:
        content: 消息正文
        msg_type: 消息类型
        bot_name: 机器人的账号昵称
    """
    if not bot_name:
        return ""
    # 语音转出来的文字里没有 @
    call = bot_name if msg_type == 'voice' else f"@{bot_name}"
    return call if call in content else ""


def convert_message(raw_msg: Any, chat_name: str, *, bot_id: str = "", bot_name: str = "") -> ChatMsg:
    """把 SDK 消息转成 ChatMsg；转不了时抛异常，由调用方记日志并提示用户"""
    msg_type = getattr(raw_msg, 'type', 'text')
    msg_id = getattr(raw_msg, 'id', '')
    msg_hash = getattr(raw_msg, 'hash', '')
    content = getattr(raw_msg, 'content', str(raw_msg))

    # 使用 chat_info.chat_type 判断群聊（更可靠）
    chat_info = getattr(raw_msg, 'chat_info', {}) or {}
    is_group = chat_info.get('chat_type') == 'group'

    mention = _find_mention(content, msg_type, bot_name)
    is_at_me = is_group and bool(mention)
    sender = getattr(raw_msg, 'sender', chat_name) if is_group else chat_name

    msg = WxChatMsg(
        platform="wechat",
        msg_type=msg_type if msg_type != 'quote' else 'refer',
        msg_id=msg_id,
        msg_hash=msg_hash,
        sender_id=sender,
        sender_name=sender,
        chat_id=chat_name,
        chat_name=chat_name,
        is_group=is_group,
        is_at_me=is_at_me,
        content=content,
        bot_id=bot_id,
        bot_name=bot_name,
        mention=mention,
    )

    if msg_type == 'image':
        file_path = _download(raw_msg, "image", chat_name)
        if file_path:
            msg.image_msg = ImageMsg(resource=ResourceRef(ref=file_path))
        else:
            msg.media_failed = True

    elif msg_type == 'voice':
        text_content = _to_text(raw_msg)
        msg.voice_msg = VoiceMsg(text_content=text_content)
        if text_content:
            msg.content = text_content

    elif msg_type == 'video':
        file_path = _download(raw_msg, "video", chat_name)
        if file_path:
            msg.video_msg = VideoMsg(resource=ResourceRef(ref=file_path))
        else:
            msg.media_failed = True

    elif msg_type == 'file':
        file_path = _download(raw_msg, "file", chat_name)
        msg.media_failed = not file_path
        file_name = getattr(raw_msg, 'file_name', None) or getattr(raw_msg, 'filename', None)
        msg.file_msg = FileMsg(
            file_name=file_name,
            resource=ResourceRef(ref=file_path) if file_path else None,
        )

    elif msg_type == 'link':
        msg.link_msg = LinkMsg(url=_get_url(raw_msg))

    elif msg_type == 'quote':
        msg.refer_msg = _build_refer_msg(raw_msg, chat_name, is_group)

    elif msg_type == 'note' and not is_group:
        # 群里的笔记带不了 @，不会交给 AI，不值得点开窗口
        _expand_note(msg, raw_msg)

    return msg


def _failure_message(result: Any) -> Any:
    """SDK 失败时返回的 WxResponse 里的原因，不是 WxResponse 时原样返回"""
    return result.get('message') if isinstance(result, dict) else result


def _download(raw_msg: Any, media_type: str, chat_name: str) -> Optional[str]:
    if not hasattr(raw_msg, 'download'):
        LOG.warning("Cannot download %s from [%s]: the message has no download method", media_type, chat_name)
        return None
    try:
        full_path = raw_msg.download()
    except Exception:
        LOG.warning("Failed to download %s from [%s]", media_type, chat_name, exc_info=True)
        return None
    # 下载失败时 SDK 不抛异常，返回一个"失败"的 WxResponse
    if not full_path:
        LOG.warning("Failed to download %s from [%s]: %s", media_type, chat_name, _failure_message(full_path))
        return None
    relative_path = to_server_path(str(full_path))
    LOG.debug("Downloaded %s: %s -> %s", media_type, full_path, relative_path)
    return relative_path


def _download_quote_media(raw_msg: Any, media_type: str, chat_name: str) -> Optional[str]:
    try:
        full_path = raw_msg.download_quote_image()
    except Exception:
        LOG.warning("Failed to download quoted %s from [%s]", media_type, chat_name, exc_info=True)
        return None
    if not full_path:
        LOG.warning("Failed to download quoted %s from [%s]: %s",
                    media_type, chat_name, _failure_message(full_path))
        return None
    relative_path = to_server_path(str(full_path))
    LOG.debug("Downloaded quoted %s: %s -> %s", media_type, full_path, relative_path)
    return relative_path


def _to_text(raw_msg: Any) -> Optional[str]:
    if not hasattr(raw_msg, 'to_text'):
        return None
    try:
        return raw_msg.to_text()
    except Exception:
        LOG.warning("Voice to_text failed", exc_info=True)
        return None


def _get_url(raw_msg: Any) -> Optional[str]:
    """SDK 要点开文章、在内置浏览器里找菜单按钮复制链接，经常超时；失败就不带 url，消息照常按群/私聊转出去"""
    if not hasattr(raw_msg, 'get_url'):
        return None
    try:
        return raw_msg.get_url()
    except Exception as e:
        # 经常超时，原因看异常就够了
        LOG.warning("Link get_url failed: %s: %s", type(e).__name__, e)
        return None


def _expand_note(msg: ChatMsg, raw_msg: Any) -> None:
    """
    点开笔记读出全文，换成 AI 认识的类型：纯文字是 text，带图的是 image（只带第一张图，文字放 content）

    content 里只有卡片上的文字，图片笔记只有"笔记"两个字；读失败就保持原样。
    """
    lines = _note_lines(raw_msg)
    if not lines:
        return
    texts, images = [], []
    for line in lines:
        line = str(line).strip()
        if not line:
            continue
        # 笔记里的图片 SDK 会先下载好，返回的是本地路径
        (images if os.path.isfile(line) else texts).append(line)
    body = "\n".join(texts)
    msg.content = f"[笔记]\n{body}" if body else "[笔记]"
    msg.msg_type = 'text'
    if images:
        ref = _to_media(images[0])
        if ref:
            msg.msg_type = 'image'
            msg.image_msg = ImageMsg(resource=ResourceRef(ref=ref))
        else:
            msg.media_failed = True


def _note_lines(raw_msg: Any) -> Optional[list]:
    if not hasattr(raw_msg, 'get_content'):
        return None
    try:
        result = raw_msg.get_content(wait=3)
    except Exception:
        LOG.warning("Note get_content failed", exc_info=True)
        return None
    # 读不出来时 SDK 不抛异常，返回一个"失败"的 WxResponse
    if not isinstance(result, list):
        LOG.warning("Note get_content failed: %s", result)
        return None
    return result


def _to_media(path: str) -> Optional[str]:
    """把笔记里的图片挪进 wx_imgs，别的服务才能通过 /media 下载"""
    try:
        target = os.path.join(get_wx_imgs_dir(), os.path.basename(path))
        if os.path.abspath(path) != os.path.abspath(target):
            shutil.move(path, target)
        return to_server_path(target)
    except Exception:
        LOG.warning("Failed to move note image %s", path, exc_info=True)
        return None


def _build_refer_msg(raw_msg: Any, chat_name: str, is_group: bool) -> WxChatMsg:
    quote_content = getattr(raw_msg, 'quote_content', '')
    quote_sender = getattr(raw_msg, 'quote_nickname', '') or 'unknown'

    refer_type = QUOTE_TYPE_MAP.get(quote_content, 'text')
    if quote_content.endswith('.pdf'):
        refer_type = 'file'

    LOG.info("Building refer_msg: type=%s, content=%s, sender=%s", refer_type, quote_content, quote_sender)

    refer = WxChatMsg(
        platform="wechat",
        msg_type=refer_type,
        sender_id=quote_sender,
        sender_name=quote_sender,
        chat_id=chat_name,
        chat_name=chat_name,
        is_group=is_group,
        content=quote_content if refer_type == 'text' else '',
    )

    if refer_type in ('image', 'video') and hasattr(raw_msg, 'download_quote_image'):
        file_path = _download_quote_media(raw_msg, refer_type, chat_name)
        if not file_path:
            refer.media_failed = True
        elif refer_type == 'image':
            refer.image_msg = ImageMsg(resource=ResourceRef(ref=file_path))
        else:
            refer.video_msg = VideoMsg(resource=ResourceRef(ref=file_path))

    elif refer_type == 'file':
        # 只有之前收到过、下载到 wx_imgs 里的文件才带得上
        local_path = os.path.join(get_wx_imgs_dir(), os.path.basename(quote_content))
        if os.path.isfile(local_path):
            resource = ResourceRef(ref=to_server_path(local_path))
        else:
            LOG.warning("Quoted file %s from [%s] is not in %s; forwarding it without the file",
                        quote_content, chat_name, get_wx_imgs_dir())
            resource = None
            refer.media_failed = True
        refer.file_msg = FileMsg(file_name=quote_content, resource=resource)

    return refer
