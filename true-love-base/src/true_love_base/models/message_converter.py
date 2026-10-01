# -*- coding: utf-8 -*-
import logging
from typing import Any, Optional

from true_love_common.chat_msg import ChatMsg, ImageMsg, VoiceMsg, VideoMsg, FileMsg, LinkMsg, ResourceRef
from true_love_base.utils.path_resolver import to_server_path

LOG = logging.getLogger("MessageConverter")

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
    try:
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

        msg = ChatMsg(
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
            file_path = _download(raw_msg, "image")
            if file_path:
                msg.image_msg = ImageMsg(resource=ResourceRef(ref=file_path))

        elif msg_type == 'voice':
            text_content = _to_text(raw_msg)
            msg.voice_msg = VoiceMsg(text_content=text_content)
            if text_content:
                msg.content = text_content

        elif msg_type == 'video':
            file_path = _download(raw_msg, "video")
            if file_path:
                msg.video_msg = VideoMsg(resource=ResourceRef(ref=file_path))

        elif msg_type == 'file':
            file_path = _download(raw_msg, "file")
            file_name = getattr(raw_msg, 'file_name', None) or getattr(raw_msg, 'filename', None)
            msg.file_msg = FileMsg(
                file_name=file_name,
                resource=ResourceRef(ref=file_path) if file_path else None,
            )

        elif msg_type == 'link':
            msg.link_msg = LinkMsg(url=_get_url(raw_msg))

        elif msg_type == 'quote':
            msg.refer_msg = _build_refer_msg(raw_msg, chat_name, is_group)

        return msg

    except Exception as e:
        LOG.error(f"Failed to convert message: {e}")
        return ChatMsg(
            platform="wechat",
            msg_type="text",
            sender_id=chat_name,
            sender_name=chat_name,
            chat_id=chat_name,
            chat_name=chat_name,
            content=str(raw_msg),
            bot_id=bot_id,
            bot_name=bot_name,
        )


def _download(raw_msg: Any, media_type: str) -> Optional[str]:
    if not hasattr(raw_msg, 'download'):
        return None
    try:
        full_path = raw_msg.download()
        if not full_path:
            return None
        relative_path = to_server_path(str(full_path))
        LOG.debug(f"Downloaded {media_type}: {full_path} -> {relative_path}")
        return relative_path
    except Exception as e:
        LOG.warning(f"Failed to download {media_type}: {e}")
        return None


def _download_quote_media(raw_msg: Any, media_type: str) -> Optional[str]:
    try:
        full_path = raw_msg.download_quote_image()
        if not full_path:
            return None
        relative_path = to_server_path(str(full_path))
        LOG.debug(f"Downloaded quote {media_type}: {full_path} -> {relative_path}")
        return relative_path
    except Exception as e:
        LOG.warning(f"Failed to download quote {media_type}: {e}")
        return None


def _to_text(raw_msg: Any) -> Optional[str]:
    if not hasattr(raw_msg, 'to_text'):
        return None
    try:
        return raw_msg.to_text()
    except Exception as e:
        LOG.warning(f"Voice to_text failed: {e}")
        return None


def _get_url(raw_msg: Any) -> Optional[str]:
    """SDK 要点开文章、在内置浏览器里找菜单按钮复制链接，经常超时；失败就不带 url，消息照常按群/私聊转出去"""
    if not hasattr(raw_msg, 'get_url'):
        return None
    try:
        return raw_msg.get_url()
    except Exception as e:
        LOG.warning(f"Link get_url failed: {e}")
        return None


def _build_refer_msg(raw_msg: Any, chat_name: str, is_group: bool) -> ChatMsg:
    quote_content = getattr(raw_msg, 'quote_content', '')
    quote_sender = getattr(raw_msg, 'quote_nickname', '') or 'unknown'

    refer_type = QUOTE_TYPE_MAP.get(quote_content, 'text')
    if quote_content.endswith('.pdf'):
        refer_type = 'file'

    LOG.info(f"Building refer_msg: type={refer_type}, content={quote_content}, sender={quote_sender}")

    refer = ChatMsg(
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
        file_path = _download_quote_media(raw_msg, refer_type)
        if file_path:
            if refer_type == 'image':
                refer.image_msg = ImageMsg(resource=ResourceRef(ref=file_path))
            else:
                refer.video_msg = VideoMsg(resource=ResourceRef(ref=file_path))

    elif refer_type == 'file':
        refer.file_msg = FileMsg(
            file_name=quote_content,
            resource=ResourceRef(ref=f"wx_imgs/{quote_content}"),
        )

    return refer
