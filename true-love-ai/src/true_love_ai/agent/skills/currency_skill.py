# -*- coding: utf-8 -*-
"""汇率查询 Skill（中国银行外汇牌价）"""
import logging
from typing import Optional

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("CurrencySkill")

CURRENCY_MAP = {
    "美元": "美元", "usd": "美元",
    "澳币": "澳大利亚元", "澳元": "澳大利亚元", "aud": "澳大利亚元",
    "日元": "日元", "jpy": "日元",
}


@register_skill({
    "type": "function",
    "function": {
        "name": "currency_query",
        "description": (
            "查询中国银行外汇牌价（现汇买入价、卖出价、中行折算价）。"
            "支持美元(USD)、澳币/澳元(AUD)、日元(JPY)。"
            "当用户询问汇率、外汇价格时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "currency": {
                    "type": "string",
                    "description": "货币名称，如：美元、澳币、日元、USD、AUD、JPY"
                }
            },
            "required": ["currency"]
        }
    }
})
async def currency_query(params: dict, ctx: dict) -> str:
    raw = params.get("currency", "").strip().lower()
    currency_cn = CURRENCY_MAP.get(raw) or CURRENCY_MAP.get(raw.replace("汇率", "").strip())
    if not currency_cn:
        return "诶嘿~暂时只支持美元、澳币、日元的汇率查询哦~"
    text = await fetch_currency(currency_cn)
    if not text:
        raise SkillFailed("呜呜~查询汇率失败了捏，稍后再试试吧~")
    return text


async def fetch_currency(currency_cn: str) -> Optional[str]:
    """查一种货币的中行牌价（currency_cn 是牌价表里的中文名）；查不到返回 None（原因记 warning）"""
    from true_love_common.http.client import async_get
    url = "https://www.boc.cn/sourcedb/whpj"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"}
    resp = await async_get(url, headers=headers, timeout=10, follow_redirects=True)
    if not resp.ok:
        # HTTP 层失败 common 已经记了
        return None
    from bs4 import BeautifulSoup
    table = BeautifulSoup(resp.text or "", "html.parser").find("table", {"align": "left"})
    if table:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if cells and currency_cn in cells[0].text:
                if len(cells) < 7:
                    break
                return (
                    f"货币名称: 中银{cells[0].text.strip()}\n"
                    f"现汇买入价: {cells[1].text.strip()}\n"
                    f"现汇卖出价: {cells[3].text.strip()}\n"
                    f"中银折算价: {cells[5].text.strip()}\n"
                    f"发布日期: {cells[6].text.strip()}"
                )
    LOG.warning("牌价页面里没找到 %s，页面格式可能变了: status=%s body=%s",
                currency_cn, resp.status_code, (resp.text or "")[:200])
    return None
