# -*- coding: utf-8 -*-
"""
Outcome - 一条消息交给 AI 之后的结局

AgentLoop 处理一条消息，最后一定落到下面某一种结局，发不发、发什么只在 text_to_send 里决定，
日志只在 log_outcome 里打（一行"回复结局: outcome=..."）。排查"为什么没回"时搜"回复结局"就够了，
别在流程中间另外发兜底话。

    replied           模型正常给出回复                       → 发模型的回复
    skipped           自动触发时模型觉得没什么可说，回了 SKIP_MARKER → 不发
    empty_fallback    模型回了空                             → 发技能结果，没有就发兜底话
    llm_error         调模型报错                             → 发兜底话
    too_many_rounds   技能调用轮次超上限                       → 发兜底话
    unreadable        消息里没有能交给模型的内容                 → 发兜底话
    crashed           处理过程中抛了没预料到的异常                → 发兜底话

自动触发（群里没人 @，见 agent_loop.is_auto_triggered）时没人在等它，除了 replied 一律不发，只记日志。
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

LOG = logging.getLogger("Outcome")

# 自动触发时，模型觉得没什么值得说的就只回这个
SKIP_MARKER = "[不回复]"


class Outcome(str, Enum):
    REPLIED = "replied"
    SKIPPED = "skipped"
    EMPTY_FALLBACK = "empty_fallback"
    LLM_ERROR = "llm_error"
    TOO_MANY_ROUNDS = "too_many_rounds"
    UNREADABLE = "unreadable"
    CRASHED = "crashed"


FALLBACK_TEXT = {
    Outcome.EMPTY_FALLBACK: "嗯嗯，收到啦~",
    Outcome.LLM_ERROR: "呜呜~出了点小状况，稍后再试试吧~",
    Outcome.TOO_MANY_ROUNDS: "处理超时了，稍后再试试吧~",
    Outcome.UNREADABLE: "抱歉，这种消息我暂时还不太看得懂呢~",
    Outcome.CRASHED: "啊哦~处理消息时出了点问题，稍后再试试捏~",
}


@dataclass
class Ending:
    outcome: Outcome
    # replied 是模型的回复；empty_fallback 是最后一次技能的结果（可能为空）
    text: str = ""
    detail: str = ""


def from_model_reply(reply: Optional[str], last_tool_result: str, auto: bool) -> Ending:
    """模型最后给出的文字落到哪种结局"""
    text = (reply or "").strip()
    if auto and SKIP_MARKER in text:
        return Ending(Outcome.SKIPPED, detail=text[:100])
    if not text:
        return Ending(Outcome.EMPTY_FALLBACK, text=last_tool_result)
    return Ending(Outcome.REPLIED, text=reply)


def text_to_send(ending: Ending, auto: bool) -> Optional[str]:
    """这个结局要发出去的话；None 表示不发"""
    if ending.outcome is Outcome.REPLIED:
        return ending.text
    if ending.outcome is Outcome.SKIPPED or auto:
        return None
    if ending.outcome is Outcome.EMPTY_FALLBACK and ending.text:
        return ending.text
    return FALLBACK_TEXT[ending.outcome]


def log_outcome(ending: Ending, *, bot_id: str, chat_id: str, sender_id: str, msg_type: str, auto: bool,
                sent: bool) -> None:
    # 正常回复和模型选择不回是业务结果，其余都是没按预期走完，用 WARNING 方便筛
    level = logging.INFO if ending.outcome in (Outcome.REPLIED, Outcome.SKIPPED) else logging.WARNING
    LOG.log(level, "回复结局: outcome=%s sent=%s auto=%s bot=%s chat=%s sender=%s type=%s detail=%s",
            ending.outcome.value, sent, auto, bot_id, chat_id, sender_id, msg_type, ending.detail)
