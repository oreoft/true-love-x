# -*- coding: utf-8 -*-
"""
base 调 server 时报上的机器人信息

每个 base 对应一个机器人（一个平台上的一个账号），每次调 server 都在请求体的 bot 字段里带上这份信息，
server 据此登记机器人，并用 callback 回调这个 base。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class BotInfo:
    bot_id: str               # 机器人的标识：微信是 wxid
    platform: str             # "wechat" | "lark" ...
    callback: str             # server 回调这个 base 的地址，如 http://100.x.x.x:5000
    name: str = ""            # 账号昵称，只用来显示

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> BotInfo:
        """缺少 bot_id、platform 或 callback 时抛 ValueError"""
        data = data if isinstance(data, dict) else {}
        info = cls(
            bot_id=str(data.get("bot_id") or "").strip(),
            platform=str(data.get("platform") or "").strip(),
            callback=str(data.get("callback") or "").strip().rstrip("/"),
            name=str(data.get("name") or "").strip(),
        )
        missing = [field for field in ("bot_id", "platform", "callback") if not getattr(info, field)]
        if missing:
            raise ValueError(f"bot 缺少字段: {', '.join(missing)}")
        return info
