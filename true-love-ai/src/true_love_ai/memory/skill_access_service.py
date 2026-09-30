# -*- coding: utf-8 -*-
"""
技能权限：所有机器人共用一份，存在 AI 库里，tl-admin 经 server 管理

每个技能（内置的和安装的）有一个权限点列表，匹配上任意一个就能用。权限点的写法：
  *:*                      所有平台、所有号、所有人
  wechat:*                 所有微信号
  wechat:<号>:*            这个号的所有群、所有人（私聊安装的技能默认是这个）
  wechat:<号>:<昵称>       这个号里的这个人，群里私聊都算；<号> 写 * 就是所有号
  wechat:<号>:<群名>:*     只在这个群里（群里安装的技能默认是这个）；最后一段写昵称就是群里的这个人
库里统一存成四段 "平台:号:群:人"。微信读不到对方的 wxid，人和群都按昵称、群名匹配，名字里不能有冒号。

内置技能第一次出现时写进库：这次改造上线时已有的内置技能都是所有人能用，
之后新加的用后台设置的"新内置技能的默认权限点"。
"""
import json
import logging
import threading

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.skill_access import AiSetting, SkillAccess

LOG = logging.getLogger("SkillAccessService")

ANY = "*"
EVERYONE = "*:*:*:*"
BUILTIN, INSTALLED = "builtin", "installed"
DEFAULT_POINTS_KEY = "new_builtin_skill_points"
SEEDED_KEY = "builtin_skills_seeded"

_cache: dict[str, list[str]] | None = None
_lock = threading.Lock()


# ==================== 权限点 ====================

def parse_point(text: str) -> str:
    """后台或聊天里写的权限点 → 库里存的四段。Raises: ValueError"""
    parts = [p.strip() for p in str(text).strip().split(":")]
    if any(not p for p in parts):
        raise ValueError(f"权限点「{text}」有空的段")
    if len(parts) == 1 and parts[0] == ANY:
        parts = [ANY, ANY, ANY, ANY]
    elif len(parts) == 2:
        parts = [parts[0], parts[1], ANY, ANY]
    elif len(parts) == 3:
        parts = [parts[0], parts[1], ANY, parts[2]]
    elif len(parts) != 4:
        raise ValueError(f"权限点「{text}」写法不对：最多四段「平台:号:群:人」，群名和昵称里不能有冒号")
    return ":".join(parts)


def format_point(point: str) -> str:
    """库里的四段 → 后台显示的最短写法"""
    platform, bot, chat, user = point.split(":")
    if chat != ANY:
        return point
    if user != ANY:
        return f"{platform}:{bot}:{user}"
    if bot != ANY:
        return f"{platform}:{bot}:*"
    return f"{platform}:*"


def parse_points(points) -> list[str]:
    """列表、JSON 数组或按行分隔的文本 → 去重的四段列表。Raises: ValueError"""
    if isinstance(points, str):
        text = points.strip()
        if text.startswith("["):
            try:
                points = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("权限点必须是 JSON 数组或一行一个")
        else:
            points = text.splitlines()
    if not isinstance(points, list):
        raise ValueError("权限点必须是列表")
    parsed: list[str] = []
    for item in points:
        if not str(item).strip():
            continue
        point = parse_point(item)
        if point not in parsed:
            parsed.append(point)
    if not parsed:
        raise ValueError("至少要有一个权限点；想让所有人都能用就写 *:*")
    return parsed


def point_matches(point: str, ctx: dict) -> bool:
    platform, bot, chat, user = point.split(":")
    is_group = bool(ctx.get("is_group"))
    return ((platform == ANY or platform == ctx.get("platform", ""))
            and (bot == ANY or bot == ctx.get("bot_id", ""))
            and (chat == ANY or (is_group and chat == ctx.get("chat", "")))
            and (user == ANY or user == ctx.get("sender_id", "")))


def install_points(ctx: dict) -> list[str]:
    """聊天里安装技能时的默认权限点：群里装的只在这个群，私聊装的在这个号"""
    platform, bot = ctx.get("platform") or ANY, ctx.get("bot_id") or ANY
    chat = ctx.get("chat") if ctx.get("is_group") and ctx.get("chat") else ANY
    return [parse_point(f"{platform}:{bot}:{chat}:*")]


# ==================== 读写 ====================

def _rows() -> dict[str, list[str]]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    with SessionLocal() as db:
        loaded = {row.skill: json.loads(row.points) for row in db.query(SkillAccess).all()}
    with _lock:
        _cache = loaded
    return loaded


def _clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def allowed(skill: str, ctx: dict) -> bool:
    """库里没有的技能（还没同步进来）当作所有人能用"""
    points = _rows().get(skill)
    return points is None or any(point_matches(p, ctx) for p in points)


def points_of(skill: str) -> list[str] | None:
    return _rows().get(skill)


def set_points(skill: str, points, kind: str | None = None) -> list[str]:
    """改一个技能的权限点；kind 只在新建时需要。Raises: ValueError"""
    parsed = parse_points(points)
    with SessionLocal() as db:
        row = db.get(SkillAccess, skill)
        if row is None:
            if kind not in (BUILTIN, INSTALLED):
                raise ValueError(f"没有这个技能：{skill}")
            row = SkillAccess(skill=skill, kind=kind)
            db.add(row)
        row.points = json.dumps(parsed, ensure_ascii=False)
        db.commit()
    _clear_cache()
    LOG.info("技能权限已保存: skill=%s points=%s", skill, parsed)
    return parsed


def delete(skill: str) -> None:
    with SessionLocal() as db:
        row = db.get(SkillAccess, skill)
        if row:
            db.delete(row)
            db.commit()
    _clear_cache()


def _setting(db, key: str) -> str | None:
    row = db.get(AiSetting, key)
    return row.value if row else None


def _put_setting(db, key: str, value: str) -> None:
    row = db.get(AiSetting, key)
    if row is None:
        db.add(AiSetting(key=key, value=value))
    else:
        row.value = value


def default_points() -> list[str]:
    """新内置技能的默认权限点，没设过时是所有人"""
    with SessionLocal() as db:
        value = _setting(db, DEFAULT_POINTS_KEY)
    return json.loads(value) if value else [EVERYONE]


def set_default_points(points) -> list[str]:
    parsed = parse_points(points)
    with SessionLocal() as db:
        _put_setting(db, DEFAULT_POINTS_KEY, json.dumps(parsed, ensure_ascii=False))
        db.commit()
    LOG.info("新内置技能的默认权限点已改成 %s", parsed)
    return parsed


def sync_builtin(names: list[str]) -> list[str]:
    """把还没进库的内置技能写进库，返回新写进去的；第一次同步时都给所有人，之后用默认权限点"""
    with SessionLocal() as db:
        known = {row.skill for row in db.query(SkillAccess.skill).all()}
        seeded = _setting(db, SEEDED_KEY)
        value = _setting(db, DEFAULT_POINTS_KEY)
        points = json.loads(value) if (seeded and value) else [EVERYONE]
        added = [name for name in names if name not in known]
        for name in added:
            db.add(SkillAccess(skill=name, kind=BUILTIN, points=json.dumps(points, ensure_ascii=False)))
        _put_setting(db, SEEDED_KEY, "1")
        db.commit()
    _clear_cache()
    if added:
        LOG.info("新的内置技能写进权限表: %s，权限点 %s", added, points)
    return added


def list_all() -> dict[str, dict]:
    """{技能: {"kind", "points": [显示写法]}}"""
    with SessionLocal() as db:
        return {row.skill: {"kind": row.kind, "points": [format_point(p) for p in json.loads(row.points)]}
                for row in db.query(SkillAccess).all()}
