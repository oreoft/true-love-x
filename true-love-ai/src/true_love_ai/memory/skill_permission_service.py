# -*- coding: utf-8 -*-
"""
技能权限规则：存在 AI 库里，tl-admin 经 server 管理

每个机器人一份，加上对所有机器人生效的一份（bot_id="*"），同一个技能机器人自己的规则优先。
每条消息都要查全部技能，所以按机器人缓存，改动时清掉。
"""
import json
import logging
import threading

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.skill_permission import SkillPermission

LOG = logging.getLogger("SkillPermissionService")

ALL_BOTS = "*"

_cache: dict[str, dict[str, list[str]]] = {}
_lock = threading.Lock()


def _parse_users(users) -> list[str]:
    """JSON 字符串或列表 → 去空白、去空项的列表。Raises: ValueError"""
    if isinstance(users, str):
        try:
            users = json.loads(users)
        except json.JSONDecodeError:
            raise ValueError("users 必须是 JSON 数组，如 [\"wechat:昵称\"]")
    if not isinstance(users, list) or not all(isinstance(u, str) for u in users):
        raise ValueError("users 必须是字符串数组")
    return [u.strip() for u in users if u.strip()]


def rules(bot_id: str) -> dict[str, list[str]]:
    """这个机器人生效的规则 {技能: 允许的人}"""
    with _lock:
        cached = _cache.get(bot_id)
    if cached is not None:
        return cached
    merged: dict[str, list[str]] = {}
    with SessionLocal() as db:
        rows = db.query(SkillPermission).filter(SkillPermission.bot_id.in_([bot_id, ALL_BOTS])).all()
    # 先放共用的，再用机器人自己的覆盖
    for row in sorted(rows, key=lambda r: r.bot_id != ALL_BOTS):
        try:
            merged[row.skill] = _parse_users(row.users)
        except ValueError:
            LOG.warning("技能权限格式不对，跳过: bot=%s skill=%s", row.bot_id, row.skill)
    with _lock:
        _cache[bot_id] = merged
    return merged


def lookup(bot_id: str, skill: str) -> list[str] | None:
    """这个技能在这个机器人上允许谁用；没配置时返回 None"""
    return rules(bot_id).get(skill)


def list_rules(bot_id: str) -> list[dict]:
    """这个机器人自己的规则和共用的规则，原样列出"""
    with SessionLocal() as db:
        rows = (db.query(SkillPermission)
                .filter(SkillPermission.bot_id.in_([bot_id, ALL_BOTS]))
                .order_by(SkillPermission.skill, SkillPermission.bot_id)
                .all())
        return [{"bot_id": r.bot_id, "skill": r.skill, "users": _parse_users(r.users),
                 "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]


def _clear_cache() -> None:
    with _lock:
        _cache.clear()


def save_rule(bot_id: str, skill: str, users) -> dict:
    """新增或覆盖一条规则。Raises: ValueError"""
    bot_id, skill = (bot_id or "").strip(), (skill or "").strip()
    if not bot_id or not skill:
        raise ValueError("bot_id 和技能名不能为空")
    parsed = _parse_users(users)
    if not parsed:
        raise ValueError("至少要允许一个人；想让所有人都能用就删掉这条规则")
    with SessionLocal() as db:
        row = db.get(SkillPermission, (bot_id, skill))
        if row is None:
            row = SkillPermission(bot_id=bot_id, skill=skill)
            db.add(row)
        row.users = json.dumps(parsed, ensure_ascii=False)
        db.commit()
    _clear_cache()
    LOG.info("技能权限已保存: bot=%s skill=%s users=%s", bot_id, skill, parsed)
    return {"bot_id": bot_id, "skill": skill, "users": parsed}


def delete_rule(bot_id: str, skill: str) -> bool:
    with SessionLocal() as db:
        row = db.get(SkillPermission, ((bot_id or "").strip(), (skill or "").strip()))
        if not row:
            return False
        db.delete(row)
        db.commit()
    _clear_cache()
    LOG.info("技能权限已删除: bot=%s skill=%s", bot_id, skill)
    return True
