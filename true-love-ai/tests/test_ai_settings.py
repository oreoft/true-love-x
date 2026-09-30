"""Personas, skill permissions and models live in the AI database and are chosen per bot."""

import json
import sqlite3
import unittest

from true_love_ai.agent.skills import permission
from true_love_ai.core import migrate, model_registry
from true_love_ai.memory import persona_service, skill_permission_service
from ai_db import memory_db


class PersonaTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)

    def test_the_most_specific_persona_wins_field_by_field(self):
        persona_service.save_persona("*", "", "shared, I am {name}", "shared voice")
        persona_service.save_persona("bot_a", "", "bot a, I am {name}", "")
        persona_service.save_persona("bot_a", "group1", "", "group voice")

        in_group = persona_service.resolve("bot_a", "group1", "Alice")
        self.assertEqual((in_group.prompt, in_group.voice_style), ("bot a, I am Alice", "group voice"))
        elsewhere = persona_service.resolve("bot_a", "group2", "Alice")
        self.assertEqual((elsewhere.prompt, elsewhere.voice_style), ("bot a, I am Alice", "shared voice"))
        other_bot = persona_service.resolve("bot_b", "group1", "Bob")
        self.assertEqual((other_bot.prompt, other_bot.voice_style), ("shared, I am Bob", "shared voice"))

    def test_without_any_persona_the_bot_still_knows_its_name(self):
        self.assertEqual(persona_service.resolve("bot_a", "", "Alice").prompt, "你是智能聊天机器人，你的名字叫Alice。")
        self.assertIn(persona_service.FALLBACK_NAME, persona_service.resolve("bot_a", "", "").prompt)

    def test_braces_other_than_name_are_left_alone(self):
        self.assertEqual(persona_service.render('reply as {"a": 1}, {name}', "Alice"), 'reply as {"a": 1}, Alice')

    def test_a_chat_persona_cannot_belong_to_every_bot(self):
        with self.assertRaises(ValueError):
            persona_service.save_persona("*", "group1", "prompt", "")
        with self.assertRaises(ValueError):
            persona_service.save_persona("bot_a", "", "", "")

    def test_listing_shows_the_bot_and_the_shared_default_only(self):
        persona_service.save_persona("*", "", "shared", "")
        persona_service.save_persona("bot_a", "group1", "a", "")
        persona_service.save_persona("bot_b", "", "b", "")

        listed = [(p["bot_id"], p["chat"]) for p in persona_service.list_personas("bot_a")]
        self.assertEqual(listed, [("*", ""), ("bot_a", "group1")])
        self.assertTrue(persona_service.delete_persona("bot_a", "group1"))
        self.assertFalse(persona_service.delete_persona("bot_a", "group1"))


class PermissionTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)

    def allowed(self, bot_id, sender_id, skill="set_model"):
        return permission.check_permission(skill, {"platform": "wechat", "sender_id": sender_id, "bot_id": bot_id})

    def test_a_bot_rule_overrides_the_shared_rule(self):
        skill_permission_service.save_rule("*", "set_model", ["wechat:admin"])
        skill_permission_service.save_rule("bot_b", "set_model", ["wechat:owner_b"])

        self.assertTrue(self.allowed("bot_a", "admin"))
        self.assertFalse(self.allowed("bot_a", "owner_b"))
        self.assertTrue(self.allowed("bot_b", "owner_b"))
        self.assertFalse(self.allowed("bot_b", "admin"))

    def test_skills_without_rules_are_open_and_code_permissions_come_first(self):
        skill_permission_service.save_rule("*", "set_model", ["wechat:admin"])

        self.assertTrue(self.allowed("bot_a", "anyone", skill="gold_price"))
        ctx = {"platform": "wechat", "sender_id": "admin", "bot_id": "bot_a"}
        self.assertFalse(permission.check_permission("set_model", ctx, code_permissions=["wechat:nobody"]))

    def test_changes_take_effect_without_a_restart(self):
        self.assertTrue(self.allowed("bot_a", "anyone"))
        skill_permission_service.save_rule("bot_a", "set_model", '["wechat:admin"]')
        self.assertFalse(self.allowed("bot_a", "anyone"))
        skill_permission_service.delete_rule("bot_a", "set_model")
        self.assertTrue(self.allowed("bot_a", "anyone"))

    def test_rules_must_name_someone(self):
        for users in ([], ["  "], "not json", [1]):
            with self.assertRaises(ValueError):
                skill_permission_service.save_rule("bot_a", "set_model", users)


class ModelTests(unittest.TestCase):
    def setUp(self):
        memory_db(self)
        self.registry = model_registry.ModelRegistry()
        self.registry.load()

    def test_changed_models_are_kept_in_the_database(self):
        self.registry.set("tts", "default", "provider/voice-2")

        reloaded = model_registry.ModelRegistry()
        reloaded.load()
        self.assertEqual(reloaded.get("tts"), "provider/voice-2")
        self.assertEqual(reloaded.get("chat"), model_registry.DEFAULT_MODELS["chat"]["default"])

    def test_clearing_a_model_restores_the_default(self):
        self.registry.set("image", "fallback", "provider/other")
        self.registry.set("image", "fallback", "")
        self.assertEqual(self.registry.get("image", "fallback"), model_registry.DEFAULT_MODELS["image"]["fallback"])

    def test_unknown_categories_and_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.set("music", "default", "x")
        with self.assertRaises(ValueError):
            self.registry.set("chat", "backup", "x")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE personas (bot_id, chat, prompt, voice_style, updated_at, PRIMARY KEY (bot_id, chat))")
        self.conn.execute("CREATE TABLE skill_permissions (bot_id, skill, users, updated_at, PRIMARY KEY (bot_id, skill))")
        self.addCleanup(self.conn.close)

    def test_old_config_becomes_the_shared_defaults(self):
        old = {
            "llm": {"system_prompt": "你是智能聊天机器人,你的名字叫小明，喜欢唱歌", "user_prompt_map": {"wechat:old_id": "x"}},
            "skill_permissions": {"set_model": ["wechat:admin"]},
        }
        with self.assertLogs("migrate", level="INFO"):
            migrate.migrate(self.conn, old)

        prompt, voice = self.conn.execute("SELECT prompt, voice_style FROM personas WHERE bot_id='*' AND chat=''").fetchone()
        self.assertEqual(prompt, "你是智能聊天机器人,你的名字叫{name}，喜欢唱歌")
        self.assertTrue(voice)
        users = self.conn.execute("SELECT users FROM skill_permissions WHERE bot_id='*' AND skill='set_model'").fetchone()[0]
        self.assertEqual(json.loads(users), ["wechat:admin"])

    def test_without_an_old_config_only_the_voice_style_is_seeded(self):
        migrate.migrate(self.conn, {})
        self.assertEqual(self.conn.execute("SELECT prompt FROM personas").fetchall(), [("",)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM skill_permissions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
