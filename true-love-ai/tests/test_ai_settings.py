"""Personas and models live in the AI database; personas are chosen per bot."""

import unittest

from true_love_ai.core import model_registry
from true_love_ai.memory import persona_service
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


if __name__ == "__main__":
    unittest.main()
