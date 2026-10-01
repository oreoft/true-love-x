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
        - msg:      ChatMsg.to_dict() 格式，回复以 msg.bot_id 这个机器人的身份发回 server
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
    from true_love_ai.agent.agent_loop import get_agent_loop
    from true_love_ai.agent.outcome import Ending, Outcome
    from true_love_ai.agent.server_client import replying_for
    with replying_for(msg.bot_id):
        try:
            await get_agent_loop().run(msg)
        except Exception as e:
            LOG.exception("Agent Loop 执行异常: sender_id=%s, err=%s", msg.sender_id, e)
            try:
                await get_agent_loop().finish(msg, Ending(Outcome.CRASHED, detail=repr(e)[:200]))
            except Exception as ex:
                LOG.error("收尾失败: %s", ex)
