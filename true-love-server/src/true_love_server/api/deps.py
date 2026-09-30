# -*- coding: utf-8 -*-
"""
Dependencies - 各路由共用的校验

- verify_token：base、AI、外部调用方都要带 token；tl-admin 的接口不校验
- bot / wechat_bot：按 bot_id 取登记过的机器人，取不到或平台不对时返回业务错误
"""

from .exception_handlers import AuthException, ValidationException
from ..services import bot_registry
from ..services.bot_registry import BotRecord


def verify_token(token: str) -> bool:
    """验证 token"""
    from ..core import Config
    valid_tokens = Config().HTTP_TOKEN or []
    if token not in valid_tokens:
        raise AuthException("failed token check")
    return True


def bot(bot_id: str | None) -> BotRecord:
    """登记过的机器人；bot_id 为空时用默认机器人"""
    try:
        return bot_registry.resolve(bot_id)
    except bot_registry.UnknownBot as e:
        raise ValidationException(str(e))


def wechat_bot(bot_id: str | None) -> BotRecord:
    """登记过的微信机器人，监听这类微信专属功能用"""
    record = bot(bot_id)
    if not record.can("listen"):
        raise ValidationException(f"机器人 {record.bot_id} 是 {record.platform}，没有监听功能")
    return record
