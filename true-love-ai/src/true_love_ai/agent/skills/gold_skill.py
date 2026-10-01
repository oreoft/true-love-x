# -*- coding: utf-8 -*-
"""中银积利金（黄金）价格查询 Skill"""
import logging
from typing import Optional

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("GoldSkill")


@register_skill({
    "type": "function",
    "function": {
        "name": "gold_price",
        "description": (
            "查询中国银行积利金（黄金）实时价格，包含买入价、卖出价、涨跌幅。"
            "当用户询问黄金价格、积利金、金价时使用。"
        ),
        "parameters": {"type": "object", "properties": {}}
    }
})
async def gold_price(params: dict, ctx: dict) -> str:
    text = await fetch_gold()
    if not text:
        raise SkillFailed("呜呜~查询黄金价格失败了捏，稍后再试试吧~")
    return text


async def fetch_gold() -> Optional[str]:
    """查中银积利金的实时价格；查不到返回 None（原因记 warning）"""
    from true_love_common.http.client import async_post
    url = "https://openapi.boc.cn/unlogin/finance/query_market_price"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
        ),
        "clentid": "540",
    }
    resp = await async_post(url, headers=headers, json={"rateCode": "AUA/CNY"}, timeout=10)
    if not resp.ok:
        # HTTP 层失败 common 已经记了
        return None
    info = resp.data.get("xpadgjlInfo") if isinstance(resp.data, dict) else None
    if not isinstance(info, dict) or not info:
        LOG.warning("金价接口返回格式变了: status=%s body=%s", resp.status_code, (resp.text or "")[:200])
        return None
    try:
        bid = info.get("bid1", "--")
        ask = info.get("ask1", "--")
        up_val = info.get("upDownValue", 0)
        up_rate = info.get("upDownRate", 0)
        trend = "↑" if up_val > 0 else ("↓" if up_val < 0 else "-")
        qd = info.get("quoteDate", "")
        qt = info.get("quoteTime", "")
        date_str = f"{qd[:4]}-{qd[4:6]}-{qd[6:]}" if len(qd) == 8 else qd
        time_str = f"{qt[:2]}:{qt[2:4]}:{qt[4:]}" if len(qt) == 6 else qt
    except (TypeError, ValueError) as e:
        LOG.warning("金价接口字段解析失败: err=%r body=%s", e, (resp.text or "")[:200])
        return None
    return (
        f"品种: 中银积利金\n"
        f"实时买入价: {bid} 元/克\n"
        f"实时卖出价: {ask} 元/克\n"
        f"当日涨跌: {trend} {abs(up_val)} ({up_rate}%)\n"
        f"报价时间: {date_str} {time_str}"
    )
