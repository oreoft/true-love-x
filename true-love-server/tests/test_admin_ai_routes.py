"""Settings stored on the AI side are passed through as-is; the server only checks which bot they are for."""

import unittest
from unittest.mock import patch

from server_env import ServerCase, http_result
from true_love_server.services.ai_client import admin as ai_admin


class AdminAiRoutesTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.forwarded = []
        self.ai_reply = {"code": 0, "data": {"ok": True}}

        async def post(url, payload, timeout=None):
            self.forwarded.append((url.rsplit("/admin", 1)[1], {k: v for k, v in payload.items() if k != "token"}))
            return http_result(url, self.ai_reply)

        patcher = patch.object(ai_admin, "async_post_json", post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_persona_is_saved_for_the_bot_or_for_every_bot(self):
        self.register("wxid_a")

        self.post("/admin/bots/wxid_a/personas/save", chat="群A", prompt="p", voice_style="")
        self.post("/admin/bots/wxid_a/personas/save", all_bots=True, prompt="shared", voice_style="v")

        self.assertEqual(self.forwarded, [
            ("/persona/save", {"bot_id": "wxid_a", "chat": "群A", "prompt": "p", "voice_style": ""}),
            ("/persona/save", {"bot_id": "*", "chat": "", "prompt": "shared", "voice_style": "v"}),
        ])

    def test_unknown_bot_is_refused_before_asking_ai(self):
        response = self.get("/admin/bots/nobody/personas")

        self.assertNotEqual(response["code"], 0)
        self.assertEqual(self.forwarded, [])

    def test_skill_permissions_are_platform_wide_and_ai_errors_pass_through(self):
        self.ai_reply = {"code": 1, "message": "至少要有一个权限点"}

        response = self.post("/admin/skill/permissions/save", skill="set_model", permissions=[])

        self.assertEqual(self.forwarded, [("/skill/permissions/save", {"skill": "set_model", "permissions": []})])
        self.assertEqual(response["message"], "至少要有一个权限点")

    def test_models_are_shared_by_every_bot(self):
        self.ai_reply = {"code": 0, "data": {"models": [{"category": "chat"}]}}

        response = self.post("/admin/models/save", category="chat", key="default", value="provider/model")

        self.assertEqual(self.forwarded, [("/model/save", {"category": "chat", "key": "default", "value": "provider/model"})])
        self.assertEqual(response["data"], {"models": [{"category": "chat"}]})


if __name__ == "__main__":
    unittest.main()
