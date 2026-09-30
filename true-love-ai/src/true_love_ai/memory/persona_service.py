# -*- coding: utf-8 -*-
"""
人设：回复用的 system prompt 和语音风格，存在 AI 库里，tl-admin 经 server 管理

按范围从小到大找，每一项各自取第一个不为空的：
  这个机器人里的这个群或人 → 这个机器人的默认 → 所有机器人共用的默认（bot_id="*"）→ 代码里的兜底
prompt 里的 {name} 换成机器人的昵称（base 从微信读到后随消息带来）。
"""
import logging
from dataclasses import dataclass

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.persona import Persona

LOG = logging.getLogger("PersonaService")

ALL_BOTS = "*"
FALLBACK_PROMPT = "你是智能聊天机器人，你的名字叫{name}。"
FALLBACK_NAME = "机器人"


@dataclass
class ResolvedPersona:
    prompt: str
    voice_style: str


def render(prompt: str, bot_name: str) -> str:
    """把 prompt 里的 {name} 换成昵称；用 replace 不用 format，prompt 里可以随便写花括号"""
    return prompt.replace("{name}", bot_name or FALLBACK_NAME)


def resolve(bot_id: str, chat: str, bot_name: str = "") -> ResolvedPersona:
    scopes = [(bot_id, chat), (bot_id, ""), (ALL_BOTS, "")] if chat else [(bot_id, ""), (ALL_BOTS, "")]
    prompt, voice_style = "", ""
    with SessionLocal() as db:
        for scope in scopes:
            row = db.get(Persona, scope)
            if not row:
                continue
            prompt = prompt or row.prompt
            voice_style = voice_style or row.voice_style
            if prompt and voice_style:
                break
    return ResolvedPersona(prompt=render(prompt or FALLBACK_PROMPT, bot_name), voice_style=voice_style)


def list_personas(bot_id: str) -> list[dict]:
    """这个机器人的人设，加上所有机器人共用的默认"""
    with SessionLocal() as db:
        rows = (db.query(Persona)
                .filter(Persona.bot_id.in_([bot_id, ALL_BOTS]))
                .order_by(Persona.bot_id, Persona.chat)
                .all())
        return [row.to_dict() for row in rows]


def save_persona(bot_id: str, chat: str, prompt: str, voice_style: str) -> dict:
    """新增或覆盖一条人设。Raises: ValueError"""
    bot_id, chat = (bot_id or "").strip(), (chat or "").strip()
    prompt, voice_style = (prompt or "").strip(), (voice_style or "").strip()
    if not bot_id:
        raise ValueError("bot_id 不能为空")
    if bot_id == ALL_BOTS and chat:
        raise ValueError("按群或人指定的人设只能属于某个机器人")
    if not prompt and not voice_style:
        raise ValueError("prompt 和语音风格不能都为空")
    with SessionLocal() as db:
        row = db.get(Persona, (bot_id, chat))
        if row is None:
            row = Persona(bot_id=bot_id, chat=chat)
            db.add(row)
        row.prompt = prompt
        row.voice_style = voice_style
        db.commit()
        LOG.info("人设已保存: bot=%s chat=%s", bot_id, chat or "(默认)")
        return row.to_dict()


def delete_persona(bot_id: str, chat: str) -> bool:
    with SessionLocal() as db:
        row = db.get(Persona, ((bot_id or "").strip(), (chat or "").strip()))
        if not row:
            return False
        db.delete(row)
        db.commit()
        LOG.info("人设已删除: bot=%s chat=%s", bot_id, chat or "(默认)")
        return True
