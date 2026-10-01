# -*- coding: utf-8 -*-
"""
Data Routes - 数据查询接口

供 Server 侧定时 Jobs 调用（早报里的汇率、金价）。
查到了 text 是数据，查不到 text 是空串，调用方只看有没有内容，不认文案。
"""

import logging

from fastapi import APIRouter

from true_love_ai.api.deps import verify_token
from true_love_ai.models.response import APIResponse

LOG = logging.getLogger("DataRoutes")

data_router = APIRouter(prefix="/data")


@data_router.get("/currency")
async def get_currency(currency: str, token: str = ""):
    """
    查询汇率数据

    Query Parameters:
        - currency: 货币名称（美元/澳币/日元/USD/AUD/JPY）
        - token: 鉴权 token
    """
    if not verify_token(token):
        return APIResponse.token_error()

    from true_love_ai.agent.skills.currency_skill import CURRENCY_MAP, fetch_currency
    currency_cn = CURRENCY_MAP.get(currency.strip().lower())
    result = await fetch_currency(currency_cn) if currency_cn else None
    return APIResponse.success({"text": result or ""})


@data_router.get("/gold")
async def get_gold(token: str = ""):
    """
    查询黄金价格

    Query Parameters:
        - token: 鉴权 token
    """
    if not verify_token(token):
        return APIResponse.token_error()

    from true_love_ai.agent.skills.gold_skill import fetch_gold
    result = await fetch_gold()
    return APIResponse.success({"text": result or ""})
