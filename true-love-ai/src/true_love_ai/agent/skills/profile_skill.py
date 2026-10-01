# -*- coding: utf-8 -*-
"""用户画像管理 Skill（直接读写 AI 本地 SQLite）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("ProfileSkill")

_SAVE_KEY_GUIDE = (
    "记忆的键名，格式为 'category.sub_key'，category 必须是以下之一："
    "personality（性格）、occupation（职业）、preference（偏好）、"
    "interest（兴趣爱好）、habit（习惯）、personal（个人信息）、"
    "fact（事实）、event（重要事件）。"
    "sub_key 自由填写，用英文描述具体维度，例如："
    "interest.music、habit.bedtime、personal.pet_name、event.birthday。"
    "时区是特殊 key，直接写 timezone，不带 sub_key。"
)


@register_skill({
    "type": "function",
    "function": {
        "name": "save_user_profile",
        "description": (
            "保存用户的个人属性和画像信息到底层系统，使其永久生效。"
            "当用户主动陈述与自身相关的客观事实（例如：时区、职业、兴趣爱好、生活习惯等）并要求你记住时，"
            "【最高指令约束】：你必须放弃使用普通回复，而应该立刻调用本 save_user_profile 技能进行数据入库！"
            "如果不调此技能，数据将永远丢失！"
            "同一个人的同一个 key 会覆盖旧值，不同 sub_key 各自独立存储。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": _SAVE_KEY_GUIDE,
                },
                "value": {
                    "type": "string",
                    "description": (
                        "具体的属性值，用自然语言描述。"
                        "如果 key 是 timezone，请务必转换为标准 IANA 格式"
                        "（如 America/Chicago, Asia/Shanghai），不要使用中文或缩写。"
                    )
                }
            },
            "required": ["key", "value"]
        }
    }
})
async def save_user_profile(params: dict, ctx: dict) -> str:
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()

    if not key or not value:
        return "呜呜~保存画像失败啦，必须要告诉我属性类别和具体的值哦！"

    if key == "timezone":
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(value)
        except Exception:
            return (
                f"呀，时区格式不太对捏，必须是标准城市格式（比如 America/Chicago 或 Asia/Shanghai），"
                f"你发的是「{value}」，请重新告诉我正确的名称哦~"
            )

    from true_love_ai.memory.user_memory_repository import is_valid_key
    if not is_valid_key(key):
        return f"呜呜~保存画像失败啦，键名「{key}」的类别不对，请按 category.sub_key 的格式重新给我哦~"

    group_id = ctx.get("session_id", "")
    sender_id = ctx.get("sender_id", "")

    from true_love_ai.memory.memory_manager import upsert_user_memory
    saved = upsert_user_memory(group_id, sender_id, [{"key": key, "value": value}], source="profile_skill")
    if not saved:
        # 写库失败的原因和堆栈已经在 repository 里记了
        raise SkillFailed("呀，系统小本本卡住了，没能帮你记下来呢，稍后再试一下吧~")
    LOG.info("用户 [%s] 保存画像: %s = %s", sender_id, key, value)

    if key == "timezone":
        return f"好的，我已经把你的时区永久设置为 {value} 啦！以后有关时间的推算都会按这个来哦~"
    return f"好哒，已经把「{key} = {value}」永久记在数据库里啦！"


@register_skill({
    "type": "function",
    "function": {
        "name": "query_user_memory",
        "description": (
            "查询当前用户已存储的所有长期记忆条目。"
            "当用户问'你记得我什么'、'你知道我哪些信息'、'帮我看看你存了什么'时使用。"
            "也可在决定是否需要 save_user_profile 前先调用，避免重复存储已有信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
})
async def query_user_memory(params: dict, ctx: dict) -> str:
    group_id = ctx.get("session_id", "")
    sender_id = ctx.get("sender_id", "")
    sender_name = ctx.get("sender_name", sender_id)

    from true_love_ai.memory.memory_manager import list_user_memory
    try:
        memories = list_user_memory(group_id, sender_id)
    except Exception as e:
        raise SkillFailed("查询记忆时出了点问题，稍后再试~") from e

    if not memories:
        return f"我的数据库里还没有关于你（{sender_name}）的任何记忆哦~"

    lines = [f"以下是我记住的关于你（{sender_name}）的信息："]
    for m in memories:
        updated = m.get("updated_at") or ""
        lines.append(f"  {m['key']} = {m['value']}（来源: {m.get('source', '?')}，更新: {updated}）")
    return "\n".join(lines)
