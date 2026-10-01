# -*- coding: utf-8 -*-
"""
聊天服务模块
  - extract_memory_facts: 从分析报告中提取结构化记忆（供 analyze_speech_skill 内部调用）
"""
import json
import logging

from true_love_ai.llm.router import get_llm_router

LOG = logging.getLogger("ChatService")


class ChatService:

    def __init__(self):
        self.llm_router = get_llm_router()

    async def extract_memory_facts(self, text: str, sender_id: str) -> list[dict]:
        """
        从发言分析报告中提取结构化用户事实，写入记忆库。

        Returns:
            [{key, value}] 列表，异常时返回空列表。
        """
        extract_prompt = (
            f"请从下面这段关于用户「{sender_id}」的分析报告中，提取关键个人事实。\n"
            "以 JSON 数组格式返回，每项包含 key 和 value 两个字段。\n"
            "key 只能是以下之一：personality（性格）/ occupation（职业）/ preference（爱好偏好）/ fact（其他事实）/ timezone（所在时区）\n"
            "要求：\n"
            "1. 只提取确定性较高的信息，不要猜测，不超过 8 条。\n"
            "2. 除了 timezone 外，其他 value 用中文简洁描述。\n"
            "3. 如果提取到了 timezone，其 value 【必须】转换为标准的 IANA 时区名称（格式为 Region/City，例如 America/New_York, Asia/Shanghai, Europe/London 等）。如果用户描述的是简称（如\"美中时区\"、\"PST\"等），请自行推导换算为其代表性的 IANA 标准城市名称。绝对不要输出 UTC-5 或中文。\n"
            "只返回 JSON 数组，不要其他文字。\n\n"
            f"分析报告：\n{text}"
        )

        try:
            raw = await self.llm_router.chat(
                messages=[{"role": "user", "content": extract_prompt}],
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]

            facts = json.loads(raw.strip())
            if not isinstance(facts, list):
                raise ValueError(f"期望 list，实际: {type(facts)}")

            allowed = {"personality", "occupation", "preference", "fact", "timezone"}
            facts = [f for f in facts if isinstance(f, dict) and f.get("key") in allowed and f.get("value")]
            LOG.info("extract_memory_facts: sender_id=%s, 提取到 %d 条", sender_id, len(facts))
            return facts
        except Exception as e:
            LOG.warning("extract_memory_facts 失败，返回空列表: %s", e, exc_info=True)
            return []
