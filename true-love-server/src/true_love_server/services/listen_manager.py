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
        
        通过 Base 的 execute 接口检查每个监听的健康状态。
        
        状态定义：
        - healthy: 子窗口存在 AND ChatInfo 能正确响应
        - unhealthy: 子窗口不存在 OR ChatInfo 无法响应
        
        Returns:
            状态结果，包含 listeners 和 summary
        """
        db_chats = listen_store.list_all(self.bot_id)

        if not db_chats:
            return {"listeners": [], "summary": {"healthy": 0, "unhealthy": 0}}

        # 通过 execute/wx 调用 GetAllSubWindow
        result = await self.base.execute_wx("GetAllSubWindow", {})
        if not result.get("success"):
            LOG.error(f"GetAllSubWindow failed: {result.get('message')}")
            # 获取失败，所有标记为 unhealthy
            return {
                "listeners": [
                    {"chat": c, "status": "unhealthy", "reason": "get_windows_failed"}
                    for c in db_chats
                ],
                "summary": {"healthy": 0, "unhealthy": len(db_chats)}
            }

        # 解析窗口列表，提取窗口名称
        sub_windows = result.get("data", []) or []
        window_names = set()
        for w in sub_windows:
            # w 可能是 dict 或对象序列化后的结果
            who = w.get("who") if isinstance(w, dict) else None
            if who:
                window_names.add(who)

        LOG.debug(f"GetAllSubWindow returned {len(window_names)} windows: {window_names}")

        # 筛选出存在子窗口的 chat，用于批量查询 ChatInfo
        chats_with_window = [c for c in db_chats if c in window_names]
        chats_without_window = [c for c in db_chats if c not in window_names]

        # 批量获取 ChatInfo（一次请求获取所有）
        chat_info_results = {}
        if chats_with_window:
            batch_result = await self.base.batch_chat_info(chats_with_window)
            if batch_result.get("success") and batch_result.get("data"):
                chat_info_results = batch_result["data"].get("results", {})
            else:
                LOG.warning(f"batch_chat_info failed: {batch_result.get('message')}")

        listeners = []
        summary = {"healthy": 0, "unhealthy": 0}

        # 处理没有子窗口的 chat
        for chat_name in chats_without_window:
            listeners.append({
                "chat": chat_name, 
                "status": "unhealthy", 
                "reason": "window_not_found"
            })
            summary["unhealthy"] += 1

        # 处理有子窗口的 chat
        for chat_name in chats_with_window:
            status_info = {"chat": chat_name, "status": None, "reason": None}
            
            chat_result = chat_info_results.get(chat_name, {})
            if chat_result.get("success") and chat_result.get("data"):
                status_info["status"] = "healthy"
                summary["healthy"] += 1
            else:
                status_info["status"] = "unhealthy"
                status_info["reason"] = chat_result.get("reason", "chat_info_failed")
                summary["unhealthy"] += 1

            listeners.append(status_info)

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
        重置单个监听
        
        流程：
        1. 切换到 SwitchToChat
        2. 关闭子窗口
        3. 调用 remove_listen (skip_store=True)
        4. 等待 UI 稳定
        5. 调用 add_listen (skip_store=True)
        
        Args:
            chat_name: 聊天对象名称
            
        Returns:
            {"success": bool, "message": str, "steps": list}
        """
        if not listen_store.exists(self.bot_id, chat_name):
            return {"success": False, "message": f"Chat [{chat_name}] not in listen list", "steps": []}

        steps = []

        # Step 1: 切换到聊天页面
        try:
            result = await self.base.execute_wx("SwitchToChat", {})
            steps.append({"step": "switch_to_chat", "success": result.get("success", False)})
        except Exception as e:
            steps.append({"step": "switch_to_chat", "success": False, "error": str(e)})

        # Step 2: 尝试关闭子窗口（幂等操作）
        try:
            result = await self.base.execute_chat(chat_name, "Close", {})
            steps.append({"step": "close_window", "success": result.get("success", False)})
        except Exception as e:
            steps.append({"step": "close_window", "success": False, "error": str(e)})

        # Step 3: 移除监听（skip_store=True，不删除数据库记录）
        try:
            result = await self.remove_listen(chat_name, skip_store=True)
            steps.append({"step": "remove_listen", "success": result.get("success", False)})
        except Exception as e:
            steps.append({"step": "remove_listen", "success": False, "error": str(e)})

        # Step 4: 等待 UI 稳定
        await asyncio.sleep(0.5)
        steps.append({"step": "wait", "success": True, "duration": 0.5})

        # Step 5: 重新添加监听（skip_store=True，不重复写入数据库记录）
        try:
            result = await self.add_listen(chat_name, skip_store=True)
            steps.append({"step": "add_listen", "success": result.get("success", False)})

            if result.get("success"):
                LOG.info(f"Reset listener for [{chat_name}] succeeded")
                return {"success": True, "message": f"Reset listener for [{chat_name}]", "steps": steps}
            else:
                LOG.error(f"Failed to re-add listener for [{chat_name}]: {result.get('message')}")
                return {"success": False, "message": f"Failed to re-add listener: {result.get('message')}",
                        "steps": steps}
        except Exception as e:
            steps.append({"step": "add_listen", "success": False, "error": str(e)})
            LOG.error(f"Exception re-adding listener for [{chat_name}]: {e}")
            return {"success": False, "message": str(e), "steps": steps}

    async def reset_all_listeners(self) -> dict:
        """
        重置所有监听
        
        流程：
        1. 把所有子窗口都关掉
        2. 切换页面刷新 UI（联系人和对话来回切一下）
        3. 挨个调用 reset_listener（虽然里面也会关闭子窗口，但是没关系，幂等的）
        
        Returns:
            {"success": bool, "message": str, "total": int, "recovered": list, "failed": list, "steps": list}
        """
        db_chats = listen_store.list_all(self.bot_id)

        if not db_chats:
            return {
                "success": True,
                "message": "No listeners in config",
                "total": 0,
                "recovered": [],
                "failed": [],
                "steps": []
            }

        steps = []
        recovered = []
        failed = []

        LOG.info(f"Starting reset all listeners, total: {len(db_chats)}")

        # Step 1: 关闭所有子窗口
        closed_count = 0
        try:
            result = await self.base.execute_wx("GetAllSubWindow", {})
            if result.get("success"):
                sub_windows = result.get("data", []) or []
                for w in sub_windows:
                    who = w.get("who") if isinstance(w, dict) else None
                    if who:
                        close_result = await self.base.execute_chat(who, "Close", {})
                        if close_result.get("success"):
                            closed_count += 1
            steps.append({"step": "close_all_windows", "success": True, "closed": closed_count})
            LOG.info(f"Closed {closed_count} sub windows")
        except Exception as e:
            steps.append({"step": "close_all_windows", "success": False, "error": str(e)})
            LOG.warning(f"Failed to close sub windows: {e}")

        # Step 2: 切换页面刷新 UI（联系人和对话来回切一下）
        try:
            await self.base.execute_wx("SwitchToContact", {})
            await asyncio.sleep(0.3)
            await self.base.execute_wx("SwitchToChat", {})
            await asyncio.sleep(0.3)
            steps.append({"step": "switch_pages", "success": True})
            LOG.info("Switched pages to refresh UI")
        except Exception as e:
            steps.append({"step": "switch_pages", "success": False, "error": str(e)})
            LOG.warning(f"Failed to switch pages: {e}")

        # Step 3: 挨个调用 reset_listener（幂等操作）
        for chat_name in db_chats:
            try:
                result = await self.reset_listener(chat_name)
                if result.get("success"):
                    recovered.append(chat_name)
                    LOG.info(f"Reset listener for [{chat_name}] succeeded")
                else:
                    failed.append(chat_name)
                    LOG.error(f"Reset listener for [{chat_name}] failed: {result.get('message')}")
            except Exception as e:
                failed.append(chat_name)
                LOG.error(f"Exception resetting listener for [{chat_name}]: {e}")

        steps.append({
            "step": "reset_listeners",
            "success": len(failed) == 0,
            "recovered": len(recovered),
            "failed": len(failed)
        })

        success = len(failed) == 0
        message = f"Reset complete: {len(recovered)}/{len(db_chats)} recovered"
        if failed:
            message += f", {len(failed)} failed"

        LOG.info(message)

        return {
            "success": success,
            "message": message,
            "total": len(db_chats),
            "recovered": recovered,
            "failed": failed,
            "steps": steps
        }


def get_listen_manager(bot_id: str) -> ListenManager:
    """这个微信机器人的监听管理器。Raises: bot_registry.UnknownBot、base_client.NotSupported"""
    return ListenManager(bot_id)
