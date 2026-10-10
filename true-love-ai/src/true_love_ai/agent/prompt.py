# -*- coding: utf-8 -*-
"""
给模型看的提示词

为了让模型服务端的前缀缓存命中，一次请求里按"越不常变越靠前"排：
    instructions（system）：人设、回复格式、工作方式、早期对话摘要。同一个会话里基本不变
    历史消息：已经发生的对话，只会在后面追加
    最新一条用户消息：当前时间、发送者画像、这次的话、自动触发时的规则。每次都不一样，放最后

最新一条消息存进历史时只存"谁说了什么"（stored_text），时间、画像、规则只给这一次调用。
"""

import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from true_love_ai.agent import outcome

DEFAULT_TZ = "Asia/Shanghai"

FORMAT_RULE = (
    "## 回复格式\n"
    "微信不支持Markdown渲染，回复只能用纯文本和换行符，不能出现**加粗**、#标题、`代码块`、- 列表、> 引用等任何Markdown符号。"
)

AGENT_RULES = (
    "## 工作方式\n"
    "你已接入多模态 Agent 系统，能够执行特定的技能任务，需要时直接调用提供给你的工具。\n"
    "群聊里每条用户消息开头是说话人的名字（如「张三：…」），回答时分清是谁在说话、是谁在问你。\n"
    "【重要】在群聊中，当用户使用「这个/那个/他说的/之前提到的/刚才讲的」等指代词，"
    "且当前对话上下文中找不到对应内容时，必须先调用 fetch_group_context 补充群聊记录后再回答，不得猜测或编造。\n"
    "最新一条用户消息前面附有【当前时间】和【发送者】。"
    "【极其重要】：如果用户要求你进行「x分钟后」、「明天几点」等时间推算，请**直接以【该用户当地时间】为起点**进行相加减，"
    "计算出的结果绝对不要再额外进行时差加减偏移！最后务必将你的结果转化为标准 ISO-8601 带时区的格式输出"
    "（例如：2026-04-13T10:30:00-05:00）。"
)

# 自动触发时附在这条消息后面（只给这一次调用，不进会话历史），让模型没话说时可以不回
AUTO_REPLY_RULE = (
    "（系统提示：上面这条没人叫你，是你在群里自己刷到的，群里的人并不期待你说话。"
    f"默认只回复 {outcome.SKIP_MARKER}。"
    "只有你真的能说出让人眼前一亮的东西才开口：好笑的吐槽或梗、对方可能不知道的有用信息、指出明显的错误或谣言。"
    "复述或描述内容（比如“图上是一个人在健身”“这张图讲的是…”）、泛泛的建议和提醒、为了接话而接话，都不算，"
    f"这些情况一律只回复 {outcome.SKIP_MARKER}，不要加别的字。）"
)


def instructions(persona_prompt: str, summary: Optional[str] = None) -> str:
    parts = [persona_prompt, FORMAT_RULE, AGENT_RULES]
    if summary:
        parts.append(f"## 早期对话摘要\n以下是本次会话早期对话的压缩记录，供参考上下文：\n{summary}")
    return "\n\n".join(parts)


def stored_text(content: str, sender_name: str, is_group: bool) -> str:
    """存进历史的那条用户消息：群里带上是谁说的"""
    return f"{sender_name}：{content}" if is_group else content


def user_timezone(user_ctx: Optional[str]) -> str:
    """画像里记了时区（"时区：America/Chicago"）就用它，没有或写错了用北京时间"""
    match = re.search(r"时区[：:]\s*([^\s|]+)", user_ctx or "")
    tz = match.group(1).strip() if match else DEFAULT_TZ
    try:
        ZoneInfo(tz)
    except Exception:
        return DEFAULT_TZ
    return tz


def time_context(user_ctx: Optional[str], now: Optional[datetime] = None) -> str:
    tz = user_timezone(user_ctx)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(tz))
    return (f"【当前时间】世界标准时间(UTC) {now_utc:%Y-%m-%d %H:%M:%S}；"
            f"该用户当地时间(时区={tz}) {now_local:%Y-%m-%d %H:%M:%S}")


def live_prompt(text: str, *, sender_name: str, user_ctx: Optional[str], auto: bool,
                now: Optional[datetime] = None) -> str:
    """这次发给模型的最新一条用户消息；text 是 stored_text 的结果"""
    sender = f"【发送者】{sender_name}" + (f"，已知信息：{user_ctx}" if user_ctx else "")
    lines = [time_context(user_ctx, now), sender, "", text]
    if auto:
        lines += ["", AUTO_REPLY_RULE]
    return "\n".join(lines)
