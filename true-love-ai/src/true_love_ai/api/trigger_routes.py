# -*- coding: utf-8 -*-
import logging

from fastapi import APIRouter, BackgroundTasks

from true_love_common.chat_msg import ChatMsg
from true_love_ai.api.deps import verify_token
from true_love_ai.models.response import APIResponse

LOG = logging.getLogger("TriggerRoutes")

trigger_router = APIRouter()


@trigger_router.post("/trigger")
async def trigger(request: dict, background_tasks: BackgroundTasks):
    """
    Server 在收到 Base 消息并完成存储后，异步 POST 到此接口。
    立即返回 200，后台启动 Agent Loop 处理。

    Body:
        - token:    鉴权 token
        - msg:      ChatMsg.to_dict() 格式，回复发往 msg.bot_id 这个机器人的 server
    """
    if not verify_token(request.get("token", "")):
        return APIResponse.token_error()

    msg_data = request.get("msg")
    if not msg_data:
        return APIResponse.error("msg 不能为空")

    msg = ChatMsg.from_dict(msg_data)

    LOG.info("收到 trigger: sender_id=%s, type=%s, is_group=%s",
             msg.sender_id, msg.msg_type, msg.is_group)

    from true_love_ai.agent.skills import ensure_skills_loaded
    ensure_skills_loaded()

    background_tasks.add_task(_run_agent, msg)
    return APIResponse.success(None)


async def _run_agent(msg: ChatMsg) -> None:
    from true_love_ai.agent.server_client import replying_for
    with replying_for(msg.bot_id):
        try:
            from true_love_ai.agent.agent_loop import get_agent_loop
            await get_agent_loop().run(msg)
        except Exception as e:
            LOG.exception("Agent Loop 执行异常: sender_id=%s, err=%s", msg.sender_id, e)
            await _send_fallback(msg)


async def _send_fallback(msg: ChatMsg) -> None:
    try:
        receiver = msg.chat_id if msg.is_group else msg.sender_id
        at_user = msg.sender_id if msg.is_group else ""
        if not receiver:
            return
        from true_love_ai.agent.server_client import send_text
        await send_text(receiver, "啊哦~处理消息时出了点问题，稍后再试试捏~", at_user, platform=msg.platform)
    except Exception as ex:
        LOG.error("发送兜底消息失败: %s", ex)
