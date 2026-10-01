# -*- coding: utf-8 -*-
"""
Listen Manager - 监听管理器

负责微信监听的管理，通过 Base 的 /execute/* 接口操作底层 SDK。
所有连接管理逻辑集中在此模块，Base 端只提供底层能力。监听是微信渠道专属功能，
每个微信机器人一个 ListenManager，只操作自己的 base 和自己库里的监听列表。

职责：
- 在机器人的库里管理监听列表（单一数据源）
- 通过 Base 的 execute 接口操作 SDK
- 提供监听状态查询、增删、刷新、重置等功能
"""

import asyncio
import logging
from . import base_client, listen_store

LOG = logging.getLogger("ListenManager")


class ListenManager:
    """
    一个微信机器人的监听管理器

    - 在机器人的库里管理监听列表（单一数据源）
    - 通过这个机器人的 base 的 execute 接口操作 SDK
    """

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        # 机器人不是微信时这里就抛 base_client.NotSupported
        self.base = base_client.wechat(bot_id)

    # ==================== 查询接口 ====================

    async def get_listener_status(self) -> dict:
        """
        获取监听状态

        base 只看每个监听注册时弹出的聊天窗口还在不在、标题对不对，不碰界面，很快。

        状态定义：
        - healthy: 聊天窗口还在
        - unhealthy: base 没注册过它（not_listening），或者窗口已经没了（window_not_found）

        Returns:
            状态结果，包含 listeners 和 summary
        """
        db_chats = listen_store.list_all(self.bot_id)

        if not db_chats:
            return {"listeners": [], "summary": {"healthy": 0, "unhealthy": 0}}

        result = await self.base.listen_status(db_chats)
        if not result.get("success"):
            LOG.error(f"listen_status failed: {result.get('message')}")
            reasons = {c: "status_failed" for c in db_chats}
        else:
            reasons = (result.get("data") or {}).get("results", {})

        listeners = []
        summary = {"healthy": 0, "unhealthy": 0}
        for chat_name in db_chats:
            reason = reasons.get(chat_name, "not_listening")
            if reason is None:
                listeners.append({"chat": chat_name, "status": "healthy", "reason": None})
                summary["healthy"] += 1
            else:
                listeners.append({"chat": chat_name, "status": "unhealthy", "reason": reason})
                summary["unhealthy"] += 1

        LOG.info(f"Listener status: {summary}")
        return {"listeners": listeners, "summary": summary}

    # ==================== 增删接口 ====================

    async def add_listen(self, chat_name: str, skip_store: bool = False) -> dict:
        """
        添加监听
        
        流程：
        1. 切换 ChatWith（打开聊天窗口）
        2. 调用 Base 添加 SDK 监听
        3. 成功后写入数据库（除非 skip_store=True）
        
        Args:
            chat_name: 聊天对象名称
            skip_store: 是否跳过数据库操作（用于 reset 场景，已有记录无需重复写入）
            
        Returns:
            {"success": bool, "message": str}
        """
        # 检查是否已存在（仅在非 skip_store 模式下检查）
        if not skip_store and listen_store.exists(self.bot_id, chat_name):
            LOG.info(f"[{chat_name}] already in listen list")
            return {"success": True, "message": f"[{chat_name}] already exists"}

        # Step 1: 切换到聊天窗口
        chat_with_result = await self.base.execute_wx("ChatWith", {"who": chat_name})
        if not chat_with_result.get("success"):
            LOG.error(f"ChatWith failed for [{chat_name}]: {chat_with_result.get('message')}")
            return {"success": False, "message": f"ChatWith failed: {chat_with_result.get('message')}"}

        # Step 2: 调用 Base 的 /listen/add 添加监听
        result = await self.base.add_listen_chat(chat_name)

        if result.get("success"):
            # SDK 添加成功，写入数据库（除非 skip_store）
            if not skip_store:
                listen_store.add(self.bot_id, chat_name)
            LOG.info(f"Added listener for [{chat_name}]")
            return {"success": True, "message": f"Added listener for [{chat_name}]"}
        else:
            LOG.error(f"Failed to add listener for [{chat_name}]: {result.get('message')}")
            return {"success": False, "message": result.get("message", "Unknown error")}

    async def remove_listen(self, chat_name: str, skip_store: bool = False) -> dict:
        """
        移除监听
        
        流程：
        1. 调用 Base 移除 SDK 监听
        2. 从数据库删除（除非 skip_store=True）
        
        Args:
            chat_name: 聊天对象名称
            skip_store: 是否跳过数据库操作（用于 reset 场景，不需要删除本地记录）
            
        Returns:
            {"success": bool, "message": str}
        """
        # 调用 Base 的 RemoveListenChat
        result = await self.base.execute_wx("RemoveListenChat", {"nickname": chat_name})

        if not result.get("success"):
            LOG.warning(f"SDK remove failed for [{chat_name}]: {result.get('message')}")

        if not skip_store:
            listen_store.remove(self.bot_id, chat_name)

        if result.get("success"):
            LOG.info(f"Removed listener for [{chat_name}]")
            return {"success": True, "message": f"Removed listener for [{chat_name}]"}
        else:
            return {"success": True, "message": f"Removed from listen list (SDK: {result.get('message', 'failed')})"}

    # ==================== 刷新/重置接口 ====================

    async def refresh_listen(self) -> dict:
        """
        智能刷新监听
        
        流程：
        1. 获取监听状态
        2. healthy 的跳过，unhealthy 的执行 reset
        
        Returns:
            刷新结果
        """
        status = await self.get_listener_status()
        listeners = status.get("listeners", [])

        if not listeners:
            return {
                "total": 0,
                "success_count": 0,
                "fail_count": 0,
                "listeners": []
            }

        result_listeners = []
        success_count = 0
        fail_count = 0

        for item in listeners:
            chat_name = item["chat"]
            before_status = item["status"]

            listener_info = {
                "chat": chat_name,
                "before": before_status,
                "action": None,
                "after": None,
                "success": None
            }

            if before_status == "healthy":
                # 健康的不处理
                listener_info["action"] = "skip"
                listener_info["after"] = "healthy"
                listener_info["success"] = True
                success_count += 1
            else:
                # unhealthy: 执行 reset
                listener_info["action"] = "reset"
                reset_result = await self.reset_listener(chat_name)
                success = reset_result.get("success", False)
                listener_info["success"] = success
                listener_info["after"] = "healthy" if success else "unhealthy"
                if success:
                    success_count += 1
                    LOG.info(f"Reset listener for [{chat_name}] succeeded")
                else:
                    fail_count += 1
                    LOG.error(f"Reset listener for [{chat_name}] failed: {reset_result.get('message')}")

            result_listeners.append(listener_info)

        return {
            "total": len(result_listeners),
            "success_count": success_count,
            "fail_count": fail_count,
            "listeners": result_listeners
        }

    async def reset_listener(self, chat_name: str) -> dict:
        """
        重置单个监听：移除监听（SDK 会顺带关掉聊天窗口），再重新添加

        Args:
            chat_name: 聊天对象名称

        Returns:
            {"success": bool, "message": str}
        """
        if not listen_store.exists(self.bot_id, chat_name):
            return {"success": False, "message": f"Chat [{chat_name}] not in listen list"}

        await self.remove_listen(chat_name, skip_store=True)
        # 等界面稳定再重新添加
        await asyncio.sleep(0.5)
        result = await self.add_listen(chat_name, skip_store=True)
        if result.get("success"):
            LOG.info(f"Reset listener for [{chat_name}] succeeded")
            return {"success": True, "message": f"Reset listener for [{chat_name}]"}
        LOG.error(f"Failed to re-add listener for [{chat_name}]: {result.get('message')}")
        return {"success": False, "message": f"Failed to re-add listener: {result.get('message')}"}

    async def reset_all_listeners(self) -> dict:
        """
        重置所有监听：逐个重置

        Returns:
            {"success": bool, "message": str, "total": int, "recovered": list, "failed": list}
        """
        db_chats = listen_store.list_all(self.bot_id)
        recovered = []
        failed = []

        LOG.info(f"Starting reset all listeners, total: {len(db_chats)}")
        for chat_name in db_chats:
            result = await self.reset_listener(chat_name)
            (recovered if result.get("success") else failed).append(chat_name)

        message = f"Reset complete: {len(recovered)}/{len(db_chats)} recovered"
        if failed:
            message += f", {len(failed)} failed"
        LOG.info(message)

        return {
            "success": not failed,
            "message": message,
            "total": len(db_chats),
            "recovered": recovered,
            "failed": failed,
        }


def get_listen_manager(bot_id: str) -> ListenManager:
    """这个微信机器人的监听管理器。Raises: bot_registry.UnknownBot、base_client.NotSupported"""
    return ListenManager(bot_id)
