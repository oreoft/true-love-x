"""What the server does before it starts serving."""

import unittest
from unittest.mock import patch

from server_env import DEFAULT_BOT_ID, ServerCase
from true_love_server import main
from true_love_server.core import db_engine
from true_love_server.services import bot_registry, scheduler_service


class StartupTests(ServerCase):
    def start(self):
        with patch.object(main.uvicorn, "run") as run, patch.object(main.signal, "signal"), \
                patch.object(main.bot_registry, "on_new_bot"):
            main.main()
        run.assert_called_once()

    def test_master_of_the_default_bot_is_told_the_server_started(self):
        default = self.register(DEFAULT_BOT_ID)
        self.register("wxid_ser")

        self.start()

        self.assertEqual(self.bases.sent(), [(f"{default}/send/text", {"is_master": True, "content": "tl-server 启动成功"})])

    def test_bots_registered_before_a_restart_get_their_reminders_back(self):
        self.register("wxid_ser")
        scheduler_service.scheduler.remove_jobstore("wxid_ser")
        db_engine.reset()
        bot_registry.reset()

        self.start()

        self.assertIsNotNone(scheduler_service.scheduler._lookup_jobstore("wxid_ser"))


if __name__ == "__main__":
    unittest.main()
