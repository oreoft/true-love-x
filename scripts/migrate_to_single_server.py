#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并成单 server 时的一次性数据迁移，只用标准库，先演练（默认），确认后加 --apply 才动文件。

1. server：每台机器原来的 dbs/group_messages.db 就是那台机器上那个号的库，改名成 <bot_id>.db 放进新 server 的 dbs 目录。
   库里的 platform 列、提醒和定时任务参数里的 bot_id，新 server 在这个号第一次登记时自己补上，这里不用改。

       python3 scripts/migrate_to_single_server.py server \\
           --db <真爱粉的 wxid>=/path/from/m8s/group_messages.db \\
           --db <kun jr 的 wxid>=/path/from/ser/group_messages.db \\
           --out /path/to/new/server/dbs [--apply]

2. AI：会话 key 从 "wechat:群或人" 改成 "bot_id:群或人"。以前的微信会话分不出是哪个号的，全部归到一个号（真爱粉）。
   用户画像的 group_id 就是会话 key，一起改。别的平台的会话（lark:）不动，只报个数。

       python3 scripts/migrate_to_single_server.py ai --db /path/to/ai/dbs/ai_data.db \\
           --bot <真爱粉的 wxid> [--apply]

两步都要在对应服务停着的时候做，--apply 之前会先把要改的库备份成 *.bak-<时间>。
"""

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 以前微信会话 key 的前缀
OLD_PREFIX = "wechat:"


def backup(path: Path) -> Path:
    target = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(path, target)
    return target


def checkpoint(path: Path) -> None:
    """把 WAL 里还没写回的内容并进主文件，免得只拷走半个库"""
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def migrate_server(pairs: list[str], out: Path, apply: bool) -> int:
    plan = []
    for pair in pairs:
        bot_id, _, source = pair.partition("=")
        source_path = Path(source).expanduser()
        if not BOT_ID.match(bot_id) or not source:
            print(f"参数不对，应为 bot_id=路径: {pair}")
            return 1
        if not source_path.is_file():
            print(f"找不到库文件: {source_path}")
            return 1
        target = out / f"{bot_id}.db"
        if target.exists():
            print(f"目标已经存在，不覆盖: {target}")
            return 1
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as conn:
            counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("group_messages", "listen_chats", "apscheduler_jobs")
                      if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()}
        plan.append((bot_id, source_path, target, counts))

    for bot_id, source_path, target, counts in plan:
        print(f"{source_path} -> {target}  ({', '.join(f'{k} {v} 行' for k, v in counts.items())})")
    if not apply:
        print("演练结束，没有改动任何文件；确认后加 --apply")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    for bot_id, source_path, target, _ in plan:
        checkpoint(source_path)
        shutil.copy2(source_path, target)
        print(f"已复制: {target}")
    print("完成。新 server 在每个号的 base 第一次登记时打开它的库，补上 migration 和任务里的 bot_id")
    return 0


def migrate_ai(db: Path, bot_id: str, apply: bool) -> int:
    if not BOT_ID.match(bot_id):
        print(f"非法的 bot_id: {bot_id}")
        return 1
    if not db.is_file():
        print(f"找不到库文件: {db}")
        return 1

    def rewrite(key: str) -> str:
        return f"{bot_id}:{key[len(OLD_PREFIX):]}" if key.startswith(OLD_PREFIX) else key

    with sqlite3.connect(db) as conn:
        sessions = [row[0] for row in conn.execute("SELECT DISTINCT session_id FROM session_messages")]
        groups = [row[0] for row in conn.execute("SELECT DISTINCT group_id FROM user_memory")]
        session_changes = {key: rewrite(key) for key in sessions if rewrite(key) != key}
        group_changes = {key: rewrite(key) for key in groups if rewrite(key) != key}
        taken = set(groups) - set(group_changes)
        clashes = [new for new in group_changes.values() if new in taken]
        print(f"会话: {len(session_changes)}/{len(sessions)} 个要改；用户画像: {len(group_changes)}/{len(groups)} 个会话要改")
        others = sorted({key.split(":", 1)[0] for key in sessions if key not in session_changes and ":" in key})
        if others:
            print(f"不改的会话前缀: {others}")
        for old, new in list(session_changes.items())[:10]:
            print(f"  {old} -> {new}")
        if clashes:
            print(f"这些新 key 已经有画像了，合并会冲突，先人工处理: {clashes}")
            return 1
        if not apply:
            print("演练结束，没有改动任何文件；确认后加 --apply")
            return 0

    print(f"已备份: {backup(db)}")
    with sqlite3.connect(db) as conn:
        conn.executemany("UPDATE session_messages SET session_id = ? WHERE session_id = ?",
                         [(new, old) for old, new in session_changes.items()])
        conn.executemany("UPDATE user_memory SET group_id = ? WHERE group_id = ?",
                         [(new, old) for old, new in group_changes.items()])
    print("完成")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="合并成单 server 时的一次性数据迁移")
    sub = parser.add_subparsers(dest="target", required=True)
    server = sub.add_parser("server", help="每台机器的 server 库改名成 <bot_id>.db")
    server.add_argument("--db", action="append", required=True, metavar="BOT_ID=PATH", help="一个号和它原来的库，可以写多次")
    server.add_argument("--out", required=True, type=Path, help="新 server 的 dbs 目录")
    server.add_argument("--apply", action="store_true", help="真的执行，不加只演练")
    ai = sub.add_parser("ai", help="AI 的会话 key 改成 bot_id 开头")
    ai.add_argument("--db", required=True, type=Path, help="AI 的 dbs/ai_data.db")
    ai.add_argument("--bot", required=True, help="以前的会话全部归到这个号")
    ai.add_argument("--apply", action="store_true", help="真的执行，不加只演练")
    args = parser.parse_args()
    if args.target == "server":
        return migrate_server(args.db, args.out.expanduser(), args.apply)
    return migrate_ai(args.db.expanduser(), args.bot, args.apply)


if __name__ == "__main__":
    sys.exit(main())
