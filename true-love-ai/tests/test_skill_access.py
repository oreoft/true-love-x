"""One platform-wide permission list per skill; points name a platform, bot, group and person."""

import json
import sqlite3
import unittest

from true_love_ai.agent.skills import dynamic_skill_manage, permission
from true_love_ai.core import migrate
from true_love_ai.memory import dynamic_skill_service, skill_access_service as access
from ai_db import memory_db


def ctx(sender="alice", bot="bot_a", group=""):
    return {"platform": "wechat", "bot_id": bot, "sender_id": sender, "is_group": bool(group), "chat": group}


class PointTests(unittest.TestCase):
    def test_short_forms_are_stored_as_four_parts_and_shown_short_again(self):
        cases = {
            "*": "*:*:*:*", "*:*": "*:*:*:*", "wechat:*": "wechat:*:*:*", "wechat:bot_a:*": "wechat:bot_a:*:*",
            "wechat:*:alice": "wechat:*:*:alice", "wechat:bot_a:room:*": "wechat:bot_a:room:*",
        }
        for written, stored in cases.items():
            self.assertEqual(access.parse_point(written), stored)
        self.assertEqual([access.format_point(p) for p in cases.values()],
                         ["*:*", "*:*", "wechat:*", "wechat:bot_a:*", "wechat:*:alice", "wechat:bot_a:room:*"])

    def test_names_with_colons_and_empty_parts_are_rejected(self):
        for bad in ("wechat:bot_a:room:with:colon", "wechat::alice", ""):
            with self.assertRaises(ValueError):
                access.parse_point(bad)
        with self.assertRaises(ValueError):
            access.parse_points("\n  \n")

    def test_what_each_point_lets_through(self):
        def ok(point, **where):
            return access.point_matches(access.parse_point(point), ctx(**where))

        self.assertTrue(ok("*:*"))
        self.assertFalse(ok("lark:*"))
        self.assertTrue(ok("wechat:bot_a:*", group="room"))
        self.assertFalse(ok("wechat:bot_a:*", bot="bot_b"))
        self.assertTrue(ok("wechat:*:alice", bot="bot_b", group="room"))
        self.assertFalse(ok("wechat:*:alice", sender="bob"))
        self.assertTrue(ok("wechat:bot_a:room:*", sender="bob", group="room"))
        self.assertFalse(ok("wechat:bot_a:room:*", group="other"))
        self.assertFalse(ok("wechat:bot_a:room:*"), "a group-only skill is not usable in a private chat")
        self.assertTrue(ok("wechat:bot_a:room:alice", group="room"))
        self.assertFalse(ok("wechat:bot_a:room:alice", sender="bob", group="room"))

    def test_skills_installed_in_chat_stay_where_they_were_installed(self):
        self.assertEqual(access.install_points(ctx(group="room")), ["wechat:bot_a:room:*"])
        self.assertEqual(access.install_points(ctx()), ["wechat:bot_a:*:*"])


class AccessTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)

    def test_any_matching_point_is_enough(self):
        access.set_points("set_model", ["wechat:*:alice", "wechat:*:bob"], kind=access.BUILTIN)

        self.assertTrue(permission.check_permission("set_model", ctx("alice")))
        self.assertTrue(permission.check_permission("set_model", ctx("bob", bot="bot_b")))
        self.assertFalse(permission.check_permission("set_model", ctx("carol")))
        with self.assertRaises(permission.PermissionDenied):
            permission.require_permission("set_model", ctx("carol"))

    def test_builtins_present_at_rollout_stay_open_and_later_ones_get_the_default(self):
        access.set_default_points(["wechat:*:admin"])

        self.assertEqual(access.sync_builtin(["gold_price", "set_model"]), ["gold_price", "set_model"])
        self.assertTrue(access.allowed("gold_price", ctx("anyone")))

        self.assertEqual(access.sync_builtin(["gold_price", "set_model", "new_admin_tool"]), ["new_admin_tool"])
        self.assertFalse(access.allowed("new_admin_tool", ctx("anyone")))
        self.assertTrue(access.allowed("new_admin_tool", ctx("admin")))

    def test_permission_changes_apply_to_the_next_message(self):
        access.sync_builtin(["set_model"])
        self.assertTrue(access.allowed("set_model", ctx("anyone")))
        access.set_points("set_model", "wechat:*:admin")
        self.assertFalse(access.allowed("set_model", ctx("anyone")))

    def test_unknown_skills_cannot_get_permissions_without_a_kind(self):
        with self.assertRaises(ValueError):
            access.set_points("nothing_here", ["*:*"])


class InstalledSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)

    async def save(self, where, skill_id="echo_hi"):
        return await dynamic_skill_manage.skill_save(
            {"id": skill_id, "name": "say hi", "description": "d", "command": "echo hi"}, where)

    async def test_a_skill_installed_in_a_group_only_works_in_that_group(self):
        reply = await self.save(ctx(group="room"))

        self.assertIn("这个群里", reply)
        self.assertEqual(access.points_of("echo_hi"), ["wechat:bot_a:room:*"])
        self.assertFalse(access.allowed("echo_hi", ctx(group="other")))

    async def test_saving_again_from_elsewhere_keeps_the_permissions(self):
        await self.save(ctx(group="room"))
        access.set_points("echo_hi", ["*:*"])
        await self.save(ctx())

        self.assertEqual(access.points_of("echo_hi"), ["*:*:*:*"])

    async def test_an_installed_skill_cannot_take_a_builtin_name(self):
        from true_love_ai.agent.skills import ensure_skills_loaded
        ensure_skills_loaded()
        reply = await self.save(ctx(), skill_id="set_model")
        self.assertIn("重名", reply)

    def test_deleting_a_skill_drops_its_permissions(self):
        dynamic_skill_service.save_skill("echo_hi", "n", "d", "echo hi", None, points=["*:*"])
        dynamic_skill_service.delete_skill("echo_hi")
        self.assertIsNone(access.points_of("echo_hi"))


class AdminListTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)

    def test_the_admin_list_shows_each_skill_with_its_points(self):
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from true_love_ai.api import admin_routes

        access.set_points("set_model", ["wechat:*:admin"], kind=access.BUILTIN)
        dynamic_skill_service.save_skill("echo_hi", "n", "d", "echo hi", None, points=["wechat:bot_a:room:*"])
        app = FastAPI()
        app.include_router(admin_routes.admin_router)
        with patch.object(admin_routes, "verify_token", lambda token: True):
            data = TestClient(app).post("/admin/skill/list", json={"token": "t"}).json()["data"]

        builtin = {s["name"]: s["permissions"] for s in data["builtin"]}
        self.assertEqual(builtin["set_model"], ["wechat:*:admin"])
        self.assertEqual(builtin["gold_price"], ["*:*"])
        self.assertEqual(data["installed"][0]["permissions"], ["wechat:bot_a:room:*"])
        self.assertEqual(data["default_permissions"], ["*:*"])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE skill_access (skill PRIMARY KEY, kind, points, updated_at)")
        self.conn.execute("CREATE TABLE ai_settings (key PRIMARY KEY, value, updated_at)")
        self.conn.execute("CREATE TABLE skill_permissions (bot_id, skill, users, updated_at)")
        self.conn.execute("CREATE TABLE dynamic_skills (id PRIMARY KEY, permissions)")
        self.addCleanup(self.conn.close)

    def rows(self):
        return {skill: (kind, json.loads(points))
                for skill, kind, points in self.conn.execute("SELECT skill, kind, points FROM skill_access")}

    def test_old_rules_and_skill_permissions_move_to_one_list_per_skill(self):
        self.conn.executemany("INSERT INTO skill_permissions VALUES (?, ?, ?, '')", [
            ("*", "skill_save", '["wechat:admin"]'),
            ("*", "set_model", '["wechat:admin"]'),
            ("bot_b", "set_model", '["wechat:owner"]'),
        ])
        self.conn.executemany("INSERT INTO dynamic_skills VALUES (?, ?)", [
            ("open_skill", None), ("locked_skill", '["lark:*", "*"]'),
        ])
        with self.assertLogs("migrate", level="INFO"):
            migrate.migrate(self.conn)

        self.assertEqual(self.rows(), {
            "skill_save": ("builtin", ["wechat:*:*:admin"]),
            "set_model": ("builtin", ["wechat:*:*:admin", "wechat:bot_b:*:owner"]),
            "open_skill": ("installed", ["*:*:*:*"]),
            "locked_skill": ("installed", ["lark:*:*:*", "*:*:*:*"]),
        })
        default = self.conn.execute("SELECT value FROM ai_settings WHERE key='new_builtin_skill_points'").fetchone()[0]
        self.assertEqual(json.loads(default), ["wechat:*:*:admin"])
        self.assertNotIn("skill_permissions", {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")})


if __name__ == "__main__":
    unittest.main()
