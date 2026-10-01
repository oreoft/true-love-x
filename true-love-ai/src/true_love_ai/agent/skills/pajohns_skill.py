# -*- coding: utf-8 -*-
"""Papa John's 点披萨 Skill"""
import asyncio
import logging
import os
import re

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("PajohnsSkill")


def _strip_md(text: str) -> str:
    """去除微信不支持的 Markdown 符号"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # *italic*
    text = re.sub(r'_(.+?)_', r'\1', text)            # _italic_
    text = re.sub(r'`(.+?)`', r'\1', text)            # `code`
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headers
    return text

_PAJOHNS_DIR = os.path.expanduser("~/.pajohns")
_AUTH_FILE = os.path.join(_PAJOHNS_DIR, "auth_cookie.txt")
_SESSION_DIR = os.path.join(_PAJOHNS_DIR, "sessions")

# 全局认证 cookie（内存缓存 + 磁盘持久化，登录一次所有 session 共用）
_global_cookie_str: str = ""
# 每个 session 独立的 PajohnsClient（独立购物车 + 独立点单状态）
_session_clients: dict = {}


def _load_persisted_cookie() -> str:
    if os.path.exists(_AUTH_FILE):
        try:
            with open(_AUTH_FILE, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def _persist_cookie(cookie_str: str):
    os.makedirs(_PAJOHNS_DIR, exist_ok=True)
    with open(_AUTH_FILE, "w") as f:
        f.write(cookie_str)


def _get_client(session_id: str):
    global _global_cookie_str

    if session_id not in _session_clients:
        try:
            from pajohns import PajohnsClient
        except ImportError:
            return None

        os.makedirs(_SESSION_DIR, exist_ok=True)
        safe_id = session_id.replace("/", "_").replace("\\", "_")[:64]
        session_file = os.path.join(_SESSION_DIR, f"{safe_id}.json")
        client = PajohnsClient(session_file=session_file)

        # 新 session 未认证时，尝试从全局 cookie 自动补入
        if not client.ordering._is_authenticated():
            if not _global_cookie_str:
                _global_cookie_str = _load_persisted_cookie()
            if _global_cookie_str:
                try:
                    client.login(_global_cookie_str)
                except Exception as e:
                    LOG.warning("自动恢复认证失败: %s", e)

        _session_clients[session_id] = client

    return _session_clients[session_id]


@register_skill({
    "type": "function",
    "function": {
        "name": "pajohns_order",
        "description": (
            "帮助用户在 Papa John's 点披萨。支持查询门店、选择优惠套餐、配置披萨、加入购物车、生成支付链接。"
            "当用户提到'点披萨'、'Papa Johns'、'PJ'、'papajohn'、'Order pizza' 时使用。"
            "完整下单流程：start → login（首次使用需要）→ search_stores → set_store"
            " → get_deals → select_deal → configure_pizza → add_to_cart → payment"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "start",
                        "login",
                        "search_stores",
                        "set_store",
                        "get_deals",
                        "select_deal",
                        "configure_pizza",
                        "add_to_cart",
                        "payment",
                    ],
                    "description": (
                        "start=检查登录状态与已记录门店；"
                        "login=导入浏览器 Cookie 完成登录；"
                        "search_stores=根据邮编搜索附近门店；"
                        "set_store=确认选择门店；"
                        "get_deals=查看门店当前优惠列表；"
                        "select_deal=选择优惠套餐；"
                        "configure_pizza=配置披萨（不传 pizza_config 则显示当前可选项）；"
                        "add_to_cart=加入购物车并显示价格；"
                        "payment=生成 PayPal 支付链接"
                    )
                },
                "cookie_str": {
                    "type": "string",
                    "description": "浏览器完整 Cookie 字符串（仅 login 使用）"
                },
                "zip_code": {
                    "type": "string",
                    "description": "邮政编码（search_stores 使用）"
                },
                "store_id": {
                    "type": "integer",
                    "description": "门店 ID（set_store 使用）"
                },
                "order_type": {
                    "type": "string",
                    "enum": ["CARRYOUT", "DELIVERY"],
                    "description": "取餐方式，默认 CARRYOUT 自取"
                },
                "deal_id": {
                    "type": "integer",
                    "description": "优惠套餐的真实 ID（select_deal 使用）。必须来自 get_deals 返回列表中方括号 [] 内的数字，例如列表显示 '[62397] The Works' 则传 62397，不能传列表序号"
                },
                "pizza_index": {
                    "type": "integer",
                    "description": "要配置的披萨序号，从 0 开始（configure_pizza 使用）"
                },
                "pizza_config": {
                    "type": "object",
                    "description": (
                        "披萨配置（configure_pizza 使用），支持字段："
                        "style/product_group_id（披萨风格 ID）、"
                        "crust_type_id（饼底类型 ID）、"
                        "sauce_id（酱汁 ID）、"
                        "topping_ids（配料 ID 列表）、"
                        "instructions（[{groupId, detailId}] 列表）、"
                        "crust_mod_code（付费饼底升级码）、"
                        "crust_flavor_code（饼底调味升级码）"
                    )
                },
            },
            "required": ["action"]
        }
    },
    "notify": [
        "正在帮您查询 Papa John's，请稍候~",
        "披萨快来了，稍等一下~",
        "正在处理您的订单，请稍候~",
    ]
})
async def pajohns_order(params: dict, ctx: dict) -> str:
    global _global_cookie_str

    session_id = ctx.get("session_id", "default")
    action = params.get("action", "start")

    client = _get_client(session_id)
    if client is None:
        return "❌ pajohns 包未安装，请联系管理员执行：pip install pajohns"

    skill = client.ordering

    try:
        if action == "start":
            return await asyncio.to_thread(skill.get_or_confirm_store)

        elif action == "login":
            cookie_str = params.get("cookie_str", "").strip()
            if not cookie_str:
                return (
                    "请将您在 papajohns.com 的完整 Cookie 字符串粘贴给我。\n\n"
                    "获取方法：\n"
                    "1. 用浏览器打开并登录 papajohns.com\n"
                    "2. 按 F12 打开开发者工具\n"
                    "3. Application → Cookies → https://www.papajohns.com\n"
                    "4. 复制所有 Cookie 为 Header 字符串格式粘贴过来"
                )
            result = await asyncio.to_thread(skill.login, cookie_str)
            # 登录成功后，持久化 cookie 并同步到其他已有 session
            if skill._is_authenticated():
                _global_cookie_str = cookie_str
                _persist_cookie(cookie_str)
                for sid, other_client in _session_clients.items():
                    if sid != session_id and not other_client.ordering._is_authenticated():
                        try:
                            await asyncio.to_thread(other_client.login, cookie_str)
                        except Exception as e:
                            LOG.warning("同步认证到 session %s 失败: %s", sid, e)
            return result

        elif action == "search_stores":
            zip_code = params.get("zip_code", "").strip()
            if not zip_code:
                return "请提供您的邮政编码（ZIP Code）以搜索附近的 Papa John's 门店。"
            return await asyncio.to_thread(skill.search_stores, zip_code)

        elif action == "set_store":
            store_id = params.get("store_id")
            if store_id is None:
                return "请告诉我您要选择的门店 ID（Store ID）。"
            order_type = params.get("order_type", "CARRYOUT")
            return await asyncio.to_thread(skill.set_store, int(store_id), order_type)

        elif action == "get_deals":
            result = await asyncio.to_thread(skill.get_all_deals)
            # 在结果末尾加一行提示，确保 LLM 明确知道用括号里的数字作为 deal_id
            result += "\n\n[系统提示] 调用 select_deal 时，deal_id 必须使用上方列表中方括号 [] 内的数字，不是前面的序号。"
            return result

        elif action == "select_deal":
            deal_id = params.get("deal_id")
            if deal_id is None:
                return "请告诉我您要选择的优惠套餐 ID（Deal ID）。"
            result = await asyncio.to_thread(skill.select_deal, int(deal_id))
            return "[系统提示] 以下披萨配置菜单必须原文完整转发给用户，禁止省略、总结或改写任何选项。\n\n" + _strip_md(result)

        elif action == "configure_pizza":
            pizza_index = int(params.get("pizza_index", 0))
            pizza_config = params.get("pizza_config")
            if not pizza_config:
                result = await asyncio.to_thread(skill.get_pizza_options, pizza_index)
                return "[系统提示] 以下披萨配置菜单必须原文完整转发给用户，禁止省略、总结或改写任何选项。\n\n" + _strip_md(result)
            result = await asyncio.to_thread(skill.configure_pizza, pizza_index, pizza_config)
            return _strip_md(result)

        elif action == "add_to_cart":
            return await asyncio.to_thread(skill.add_to_cart)

        elif action == "payment":
            return await asyncio.to_thread(skill.get_payment_link)

        else:
            return f"❌ 未知操作: {action}"

    except Exception as e:
        raise SkillFailed(f"❌ 操作失败（{action}）：{e}") from e
