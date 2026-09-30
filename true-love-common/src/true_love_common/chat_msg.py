# -*- coding: utf-8 -*-
"""
ChatMsg 消息协议定义

各服务（server / ai / lark-agent-base / true-love-base）之间传递消息时
统一使用此模块中的类。字段变更只需改此文件，各服务按提示同步。
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Any


@dataclass
class ResourceRef:
    ref: str
    source: str = "local"  # "local" | "http"

    @classmethod
    def from_dict(cls, d: dict) -> ResourceRef:
        source = d.get("source", "local")
        return cls(ref=d.get("ref", ""), source="http" if source == "url" else source)


@dataclass
class ImageMsg:
    resource: Optional[ResourceRef] = None

    @classmethod
    def from_dict(cls, d: dict) -> ImageMsg:
        resource = ResourceRef.from_dict(d["resource"]) if d.get("resource") else None
        return cls(resource=resource)


@dataclass
class VoiceMsg:
    text_content: Optional[str] = None
    resource: Optional[ResourceRef] = None

    @classmethod
    def from_dict(cls, d: dict) -> VoiceMsg:
        resource = ResourceRef.from_dict(d["resource"]) if d.get("resource") else None
        return cls(text_content=d.get("text_content"), resource=resource)


@dataclass
class VideoMsg:
    resource: Optional[ResourceRef] = None

    @classmethod
    def from_dict(cls, d: dict) -> VideoMsg:
        resource = ResourceRef.from_dict(d["resource"]) if d.get("resource") else None
        return cls(resource=resource)


@dataclass
class FileMsg:
    file_name: Optional[str] = None
    resource: Optional[ResourceRef] = None

    @classmethod
    def from_dict(cls, d: dict) -> FileMsg:
        resource = ResourceRef.from_dict(d["resource"]) if d.get("resource") else None
        return cls(file_name=d.get("file_name"), resource=resource)


@dataclass
class LinkMsg:
    url: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> LinkMsg:
        return cls(url=d.get("url"))


@dataclass
class ChatMsg:
    """通用消息模型，支持多平台。"""

    # 平台
    platform: str = "wechat"        # "wechat" | "lark"

    # 消息元信息
    msg_type: str = "text"          # text/image/voice/video/file/link/refer
    msg_id: str = ""
    msg_hash: str = ""

    # 发送者
    sender_id: str = ""             # 平台唯一 ID；微信暂用昵称填充
    sender_name: str = ""           # 显示名，仅用于展示

    # 会话
    chat_id: str = ""
    chat_name: str = ""

    # 标志位
    is_group: bool = False
    is_at_me: bool = False

    # 消息内容
    content: str = ""
    image_msg: Optional[ImageMsg] = None
    voice_msg: Optional[VoiceMsg] = None
    video_msg: Optional[VideoMsg] = None
    file_msg: Optional[FileMsg] = None
    link_msg: Optional[LinkMsg] = None
    refer_msg: Optional[ChatMsg] = None

    # 收到这条消息的机器人（微信是它的 wxid），server 和 AI 按它区分数据、决定从哪个号回复
    bot_id: str = ""
    # 正文里叫到机器人的那段文字（如 "@真爱粉"），没叫到为空
    mention: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ChatMsg:
        chat_id = data.get("chat_id", "")
        return cls(
            platform=data.get("platform", "wechat"),
            msg_type=data.get("msg_type", "text"),
            msg_id=data.get("msg_id", ""),
            msg_hash=data.get("msg_hash", ""),
            sender_id=data.get("sender_id", ""),
            sender_name=data.get("sender_name", ""),
            chat_id=chat_id,
            chat_name=data.get("chat_name") or chat_id,
            is_group=data.get("is_group", False),
            is_at_me=data.get("is_at_me", False),
            content=data.get("content", ""),
            image_msg=ImageMsg.from_dict(data["image_msg"]) if data.get("image_msg") else None,
            voice_msg=VoiceMsg.from_dict(data["voice_msg"]) if data.get("voice_msg") else None,
            video_msg=VideoMsg.from_dict(data["video_msg"]) if data.get("video_msg") else None,
            file_msg=FileMsg.from_dict(data["file_msg"]) if data.get("file_msg") else None,
            link_msg=LinkMsg.from_dict(data["link_msg"]) if data.get("link_msg") else None,
            refer_msg=cls.from_dict(data["refer_msg"]) if data.get("refer_msg") else None,
            bot_id=data.get("bot_id", ""),
            mention=data.get("mention", ""),
        )
