# -*- coding: utf-8 -*-
"""
API Models - HTTP API 请求/响应模型

定义与外部服务通信的数据模型。
"""

import json
from dataclasses import dataclass
from typing import Any

from true_love_common.bot import BotInfo
from true_love_common.chat_msg import ChatMsg
from true_love_common.http.response import ApiResponse, BizCode


@dataclass
class ChatRequest:
    """发送到 server 的聊天请求，包装 ChatMsg 并附加认证 token 和这个 base 的机器人信息。"""
    token: str
    bot: BotInfo
    message: ChatMsg

    def to_dict(self) -> dict[str, Any]:
        return {"token": self.token, "bot": self.bot.to_dict(), "msg": self.message.to_dict()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ChatResponse:
    """server /base/on-message 的响应，base 只看 code 判断是否接收成功；回复由 server 异步回调发送"""
    code: int  # 状态码，0 表示成功

    @classmethod
    def from_dict(cls, d: dict) -> "ChatResponse":
        """从字典创建"""
        return cls(code=d.get("code", -1))

    @property
    def is_success(self) -> bool:
        """是否成功"""
        return self.code == 0


# 预定义的错误响应
class ApiErrors:
    """API 错误定义"""
    ROBOT_NOT_READY = ApiResponse.error(BizCode.ROBOT_NOT_READY, "Robot not ready")
    WECHAT_OFFLINE = ApiResponse.error(BizCode.ROBOT_NOT_READY, "WeChat offline")
    NO_MASTER = ApiResponse.error(BizCode.BAD_REQUEST, "No master is configured for this bot")
    SEND_FAILED = ApiResponse.error(BizCode.SEND_FAILED, "Send failed, please retry")
    INVALID_PARAMS = ApiResponse.error(BizCode.TOKEN_ERROR, "Invalid parameters")
