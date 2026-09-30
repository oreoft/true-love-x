"""A database from the one-server-per-machine days becomes a bot's database just by renaming the file."""

import pickle
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from apscheduler.job import Job
from apscheduler.triggers.date import DateTrigger

from server_env import ServerCase
from true_love_server.core.db_engine import bot_session
from true_love_server.services import listen_store, reminder_service, scheduler_service, task_service
from true_love_server.services.group_message_repository import GroupMessageRepository

LEGACY_SCHEMA = """
CREATE TABLE group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform VARCHAR(32) NOT NULL, msg_id VARCHAR(64) NOT NULL,
    msg_hash VARCHAR(64) NOT NULL UNIQUE, msg_type VARCHAR(32) NOT NULL, sender_id VARCHAR(128) NOT NULL,
    sender_name VARCHAR(128) NOT NULL, chat_id VARCHAR(128) NOT NULL, chat_name VARCHAR(128) NOT NULL,
    content TEXT NOT NULL, is_group BOOLEAN NOT NULL, is_at_me BOOLEAN NOT NULL, image_msg TEXT, voice_msg TEXT,
    video_msg TEXT, file_msg TEXT, link_msg TEXT, refer_msg TEXT, created_at DATETIME NOT NULL);
CREATE INDEX idx_chat_time ON group_messages (chat_id, created_at);
CREATE INDEX idx_platform_chat ON group_messages (platform, chat_id);
CREATE TABLE listen_chats (chat_name VARCHAR(128) PRIMARY KEY, created_at DATETIME NOT NULL);
CREATE TABLE schema_migrations (version VARCHAR(32) PRIMARY KEY, description VARCHAR(256) NOT NULL, applied_at DATETIME NOT NULL);
CREATE TABLE apscheduler_jobs (id VARCHAR(191) PRIMARY KEY, next_run_time FLOAT, job_state BLOB NOT NULL);
INSERT INTO schema_migrations VALUES ('003', 'settings: drop the settings table', '2026-09-30T00:00:00');
INSERT INTO group_messages (platform, msg_id, msg_hash, msg_type, sender_id, sender_name, chat_id, chat_name,
    content, is_group, is_at_me, created_at)
    VALUES ('wechat', 'm1', 'm1', 'text', 'alice', 'alice', '群A', '群A', 'old message', 1, 0, '2026-09-29 10:00:00');
INSERT INTO listen_chats VALUES ('群A', '2026-09-29 10:00:00');
"""


def legacy_job(job_id, func, kwargs):
    """A job pickled the way the old server stored it: kwargs with platform and without bot_id"""
    run_at = datetime.now(timezone.utc) + timedelta(days=1)
    job = Job.__new__(Job)
    job.__setstate__({
        "version": 1, "id": job_id, "func": func, "trigger": DateTrigger(run_date=run_at), "executor": "default",
        "args": (), "kwargs": kwargs, "name": job_id, "misfire_grace_time": 3600, "coalesce": False,
        "max_instances": 3, "next_run_time": run_at,
    })
    return job_id, run_at.timestamp(), pickle.dumps(job.__getstate__(), pickle.HIGHEST_PROTOCOL)


class LegacyDatabaseTests(ServerCase):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.data_dir / "wxid_m8s.db") as conn:
            conn.executescript(LEGACY_SCHEMA)
            conn.executemany("INSERT INTO apscheduler_jobs VALUES (?, ?, ?)", [
                legacy_job("reminder_群A_1", "true_love_server.services.reminder_service:_send_reminder",
                           {"receiver": "群A", "at_user": "", "content": "开会", "job_id": "reminder_群A_1",
                            "platform": "wechat"}),
                legacy_job("task_notice_moyu_schedule_1", "true_love_server.services.task_service:_run_task",
                           {"task_id": "task_notice_moyu_schedule_1", "job_name": "notice_moyu_schedule",
                            "receivers": ["群A"], "schedule": {"mode": "daily", "time": "09:05",
                                                               "timezone": "Asia/Shanghai"}}),
            ])
        self.register("wxid_m8s")

    def test_old_messages_and_listen_list_carry_over(self):
        with bot_session("wxid_m8s") as db:
            [old] = GroupMessageRepository(db).get_messages("群A")
        self.assertEqual(old["content"], "old message")
        self.assertEqual(listen_store.list_all("wxid_m8s"), ["群A"])

    def test_platform_column_is_dropped(self):
        with sqlite3.connect(self.data_dir / "wxid_m8s.db") as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(group_messages)")}
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        self.assertNotIn("platform", columns)
        self.assertIn("004", versions)

    def test_old_reminders_and_tasks_are_stamped_with_their_bot(self):
        for job in scheduler_service.get_jobs("wxid_m8s"):
            self.assertEqual(job.kwargs["bot_id"], "wxid_m8s")
            self.assertNotIn("platform", job.kwargs)
        self.assertEqual([r["content"] for r in reminder_service.list_all_reminders("wxid_m8s")], ["开会"])
        self.assertEqual([t["job_name"] for t in task_service.list_tasks("wxid_m8s")], ["notice_moyu_schedule"])

    def test_old_reminder_fires_through_its_bot(self):
        [job] = [job for job in scheduler_service.get_jobs("wxid_m8s") if job.id.startswith("reminder_")]

        job.func(**job.kwargs)

        [(url, payload)] = self.bases.sent()
        self.assertEqual(url, "http://wxid_m8s.base:5000/send/text")
        self.assertIn("开会", payload["content"])


if __name__ == "__main__":
    unittest.main()
