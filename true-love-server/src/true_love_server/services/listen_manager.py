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

import logging

from true_love_common.http.exceptions import BusinessException
from true_love_common.http.response import BizCode

from . import base_client, listen_store

LOG = logging.getLogger("ListenManager")


class ListenStatusError(BusinessException):
    """base 没能报告监听状态（微信离线、base 连不上等），这时不知道哪些监听坏了"""

    def __init__(self, message: str):
        super().__init__(code=BizCode.ROBOT_NOT_READY, message=message)


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

        Raises:
            ListenStatusError: 状态查询本身失败；这时不能把所有监听都当成坏的去重置
        """
        db_chats = listen_store.list_all(self.bot_id)

        if not db_chats:
            return {"listeners": [], "summary": {"healthy": 0, "unhealthy": 0}}

        result = await self.base.listen_status(db_chats)
        if not result.get("success"):
            LOG.error("listen_status failed: bot_id=%s message=%s", self.bot_id, result.get("message"))
            raise ListenStatusError(f"查询监听状态失败: {result.get('message')}")
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

        LOG.info("Listener status: bot_id=%s summary=%s", self.bot_id, summary)
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
            LOG.info("[%s] already in listen list", chat_name)
            return {"success": True, "message": f"[{chat_name}] already exists"}

        # Step 1: 切换到聊天窗口
        chat_with_result = await self.base.execute_wx("ChatWith", {"who": chat_name})
        if not chat_with_result.get("success"):
            LOG.error("ChatWith failed for [%s]: %s", chat_name, chat_with_result.get("message"))
            return {"success": False, "message": f"ChatWith failed: {chat_with_result.get('message')}"}

        # Step 2: 调用 Base 的 /listen/add 添加监听
        result = await self.base.add_listen_chat(chat_name)

        if result.get("success"):
            # SDK 添加成功，写入数据库（除非 skip_store）
            if not skip_store:
                listen_store.add(self.bot_id, chat_name)
            LOG.info("Added listener for [%s]", chat_name)
            return {"success": True, "message": f"Added listener for [{chat_name}]"}
        else:
            LOG.error("Failed to add listener for [%s]: %s", chat_name, result.get("message"))
            return {"success": False, "message": result.get("message", "Unknown error")}

    async def remove_listen(self, chat_name: str, skip_store: bool = False) -> dict:
        """
        移除监听

        流程：
        1. 调用 Base 移除 SDK 监听（SDK 返回失败也算失败）
        2. 成功后从数据库删除（除非 skip_store=True）；失败时不删库，免得 base 还在监听而列表里没了
        
        Args:
            chat_name: 聊天对象名称
            skip_store: 是否跳过数据库操作（用于 reset 场景，不需要删除本地记录）
            
        Returns:
            {"success": bool, "message": str}
        """
        # 调用 Base 的 RemoveListenChat
        result = await self.base.execute_wx("RemoveListenChat", {"nickname": chat_name})
        if result.get("success"):
            message = f"Removed listener for [{chat_name}]"
        elif await self._base_not_listening(chat_name):
            # SDK 移除失败是因为 base 本来就没在监听它，列表里照样删
            LOG.info("[%s] was not being listened to on base, removed from the list only", chat_name)
            message = f"[{chat_name}] was not being listened to, removed from the list"
        else:
            LOG.warning("Failed to remove listener for [%s]: %s", chat_name, result.get("message"))
            return {"success": False, "message": f"Remove listener failed: {result.get('message') or 'unknown error'}"}

        if not skip_store:
            listen_store.remove(self.bot_id, chat_name)
        LOG.info("Removed listener for [%s]", chat_name)
        return {"success": True, "message": message}

    async def _base_not_listening(self, chat_name: str) -> bool:
        """问 base 这个聊天还在不在监听；查询本身失败时当作还在监听（不能确定就不删库）"""
        status = await self.base.listen_status([chat_name])
        if not status.get("success"):
            LOG.warning("listen_status for [%s] failed: %s", chat_name, status.get("message"))
            return False
        reasons = (status.get("data") or {}).get("results", {})
        return reasons.get(chat_name, "not_listening") == "not_listening"

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
                else:
                    fail_count += 1

            result_listeners.append(listener_info)

        return {
            "total": len(result_listeners),
            "success_count": success_count,
            "fail_count": fail_count,
            "listeners": result_listeners
        }

    async def reset_listener(self, chat_name: str) -> dict:
        """
        重置单个监听：移除监听（SDK 会顺带关掉聊天窗口），马上重新添加

        base 加监听时自己从会话右键菜单把聊天弹成独立窗口，不用先 ChatWith，也不用等界面稳定

        Args:
            chat_name: 聊天对象名称

        Returns:
            {"success": bool, "message": str}
        """
        if not listen_store.exists(self.bot_id, chat_name):
            return {"success": False, "message": f"Chat [{chat_name}] not in listen list"}

        # 移除失败（查询失败或 base 还在监听）也照样重新添加，只记下来
        removed = await self.remove_listen(chat_name, skip_store=True)
        if not removed.get("success"):
            LOG.warning("Reset [%s]: remove failed, adding it again anyway: %s", chat_name, removed.get("message"))
        result = await self.base.add_listen_chat(chat_name)
        if result.get("success"):
            LOG.info("Reset listener for [%s] succeeded", chat_name)
            return {"success": True, "message": f"Reset listener for [{chat_name}]"}
        message = f"Failed to re-add listener: {result.get('message')}"
        if not removed.get("success"):
            message += f" ({removed.get('message')})"
        LOG.error("Reset listener for [%s] failed: %s", chat_name, message)
        return {"success": False, "message": message}

    async def reset_all_listeners(self) -> dict:
        """
        重置所有监听：逐个重置

        Returns:
            {"success": bool, "message": str, "total": int, "recovered": list, "failed": list}
        """
        db_chats = listen_store.list_all(self.bot_id)
        recovered = []
        failed = []

        LOG.info("Starting reset all listeners: bot_id=%s total=%d", self.bot_id, len(db_chats))
        for chat_name in db_chats:
            result = await self.reset_listener(chat_name)
            (recovered if result.get("success") else failed).append(chat_name)

        message = f"Reset complete: {len(recovered)}/{len(db_chats)} recovered"
        if failed:
            message += f", {len(failed)} failed"
            LOG.error("Reset all listeners: bot_id=%s %s: %s", self.bot_id, message, failed)
        else:
            LOG.info("Reset all listeners: bot_id=%s %s", self.bot_id, message)

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
