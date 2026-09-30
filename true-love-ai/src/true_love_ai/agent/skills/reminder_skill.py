# -*- coding: utf-8 -*-
"""提醒管理 Skill（通过 Server /action/reminder/* 实现）"""
import logging
import time

from true_love_ai.agent.skill_registry import register_skill

LOG = logging.getLogger("ReminderSkill")


@register_skill({
    "type": "function",
    "function": {
        "name": "set_reminder",
        "description": (
            "为用户设定一个未来的定时提醒任务。例如'十分钟后提醒我关火'或'明天中午提醒我开会'。"
            "系统已根据用户的时区换算了当地时间，请务必根据其当地时间推演出标准 ISO-8601 触达时间传入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_time_iso": {
                    "type": "string",
                    "description": "换算成目标的标准 ISO-8601 时刻字符串，含时区（如 2026-04-14T10:30:00+08:00）"
                },
                "content": {
                    "type": "string",
                    "description": "要提醒的具体内容，以直接命令语气描述，如'关火'、'去开会'"
                }
            },
            "required": ["target_time_iso", "content"]
        }
    }
})
async def set_reminder(params: dict, ctx: dict) -> str:
    iso_str = params.get("target_time_iso", "")
    content = params.get("content", "")
    if not iso_str or not content:
        return "呜呜~设置提醒需要确切的时间点和内容哦！"

    try:
        import dateutil.parser
        from datetime import datetime
        dt = dateutil.parser.isoparse(iso_str)
        now = datetime.now(dt.tzinfo)
        if dt <= now:
            return f"诶嘿？解析出的时间 {iso_str} 好像在过去啦！是不是时区没对上呢？"
    except Exception as e:
        return f"呜呜~时间格式解析失败: {iso_str}, 错误: {e}"

    receiver = ctx.get("receiver", "")
    at_user = ctx.get("at_user", "")
    job_id = f"reminder_{receiver}_{int(time.time())}"

    from true_love_ai.agent.server_client import add_reminder
    result = await add_reminder(job_id, iso_str, receiver, content, at_user)

    if result.get("code") == 0:
        tz_display = dt.tzname() or f"UTC{dt.strftime('%z')}"
        reply = f"好耶~设置提醒成功！会在 {dt.strftime('%m-%d %H:%M:%S')} ({tz_display}) 准时提醒你哦~"

        # 主动引导时区设置
        from true_love_ai.memory.memory_manager import get_user_context
        user_ctx = get_user_context(ctx.get("session_id", ""), ctx.get("sender_id", ""))
        if not user_ctx or ("时区" not in user_ctx and "timezone" not in user_ctx.lower()):
            reply += "\n\n(诶，我看你还没设置所在地，刚才的推算是按北京时间瞎估的哦~ 如果人在其他时区可以告诉我，我会永远记住哒！)"
        return reply

    return f"呜呜~提醒设置失败了: {result.get('msg', '未知错误')}"


@register_skill({
    "type": "function",
    "function": {
        "name": "query_reminder",
        "description": "查询当前用户所在会话所有未执行的定时提醒作业。",
        "parameters": {"type": "object", "properties": {}}
    }
})
async def query_reminder(params: dict, ctx: dict) -> str:
    receiver = ctx.get("receiver", "")
    from true_love_ai.agent.server_client import query_reminders
    jobs = await query_reminders(receiver)
    if not jobs:
        return "暂未查到你在这个聊天中的待办提醒记录哦~"
    lines = [f"【{j['job_id']}】执行时间: {j['next_run_time']}" for j in jobs]
    return "查询到以下未执行的提醒任务：\n" + "\n".join(lines)


@register_skill({
    "type": "function",
    "function": {
        "name": "delete_reminder",
        "description": "取消或删除指定的定时提醒任务。",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "要取消的提醒作业ID，通过 query_reminder 获取"
                }
            },
            "required": ["job_id"]
        }
    }
})
async def delete_reminder(params: dict, ctx: dict) -> str:
    job_id = params.get("job_id", "")
    if not job_id:
        return "呜呜~不提供任务ID的话，我不知道你要删哪个呢~"
    from true_love_ai.agent.server_client import delete_reminder as _del
    ok = await _del(job_id)
    return "好耶~已成功删除对应的提醒记录！" if ok else "呜呜~没找到指定的提醒任务，是不是已经过期或者已被删除啦？"


@register_skill({
    "type": "function",
    "function": {
        "name": "update_reminder",
        "description": (
            "修改一个已有的定时提醒任务，可以更新触达时间、内容，或两者同时修改。"
            "当用户说'把XX提醒改成…'、'刚刚那个提醒时间改一下'等情况时使用。"
            "job_id 可从本次会话上下文（set_reminder 的返回结果）或 query_reminder 的结果中获取。"
            "如果不确定 job_id，先调用 query_reminder 查询后再修改。"
            "new_time_iso 和 new_content 至少提供一个，不填则保留原值。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "要修改的提醒任务 ID"
                },
                "new_time_iso": {
                    "type": "string",
                    "description": "新的触达时间，标准 ISO-8601 含时区（如 2026-04-14T10:30:00+08:00）。不修改时间可不传。"
                },
                "new_content": {
                    "type": "string",
                    "description": "新的提醒内容。不修改内容可不传。"
                }
            },
            "required": ["job_id"]
        }
    }
})
async def update_reminder(params: dict, ctx: dict) -> str:
    job_id = params.get("job_id", "")
    new_time_iso = params.get("new_time_iso", "")
    new_content = params.get("new_content", "")

    if not job_id:
        return "呜呜~需要提供任务ID才能修改哦，可以先用 query_reminder 查一下~"
    if not new_time_iso and not new_content:
        return "呜呜~新时间和新内容至少要提供一个嘛~"

    if new_time_iso:
        try:
            import dateutil.parser
            from datetime import datetime
            dt = dateutil.parser.isoparse(new_time_iso)
            now = datetime.now(dt.tzinfo)
            if dt <= now:
                return f"诶嘿？新时间 {new_time_iso} 好像在过去啦！是不是时区没对上呢？"
        except Exception as e:
            return f"呜呜~时间格式解析失败: {new_time_iso}, 错误: {e}"

    from true_love_ai.agent.server_client import update_reminder as _update
    result = await _update(job_id, new_time_iso=new_time_iso, new_content=new_content)

    if result.get("code") == 0:
        parts = []
        if new_time_iso:
            from dateutil.parser import isoparse
            dt = isoparse(new_time_iso)
            tz_display = dt.tzname() or f"UTC{dt.strftime('%z')}"
            parts.append(f"时间改为 {dt.strftime('%m-%d %H:%M')} ({tz_display})")
        if new_content:
            parts.append(f"内容改为「{new_content}」")
        return f"好耶~提醒已更新！{'，'.join(parts)}~"

    return f"呜呜~修改失败了: {result.get('msg', '未知错误')}"
