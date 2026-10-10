"""
Runs the real server in-process against throwaway databases.

Only the network edges are faked: calls to a base (by callback address) and to AI. Every test gets
its own data directory, so each bot's database and the platform database start empty.
"""

import atexit
import json
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from true_love_common.http.client import HttpResult

SERVER_ROOT = Path(__file__).parents[1]
TOKEN = "token"
DEFAULT_BOT_ID = "wxid_default"

# The server reads config-dev.yaml from its working directory while it is imported.
_HOME = Path(tempfile.mkdtemp(prefix="tl-server-test-"))
atexit.register(shutil.rmtree, _HOME, True)
(_HOME / "config-dev.yaml").write_text(
    f'http_token: ["{TOKEN}"]\nalapi: {{token: "alapi"}}\nhttp: {{host: 127.0.0.1, port: 8088}}\n'
    f'default_bot_id: "{DEFAULT_BOT_ID}"\n'
    'r2: {account_id: "acct", bucket: "relay", access_key_id: "key", secret_access_key: "secret"}\n',
    encoding="utf-8")
_previous = os.getcwd()
os.environ.pop("APP_ENV", None)
os.chdir(_HOME)
try:
    from fastapi.testclient import TestClient

    from true_love_server.api import create_app
    from true_love_server.core import db_engine
    from true_love_server.services import ai_rate_limit, auto_ai_limit, bot_registry, scheduler_service
    from true_love_server.services.ai_client import business as ai_business
    from true_love_server.services.base_client import _client as base_http
    from true_love_server.core import Config

    # Config is read on first use; load the test config now, while the working directory is the test home
    Config()
finally:
    os.chdir(_previous)

# The server logs JSON to stdout; keep test output readable (assertLogs still sees every record)
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.CRITICAL)

scheduler_service.start_scheduler()
bot_registry.on_new_bot(scheduler_service.add_bot_store)


def http_result(url, data=None, *, status_code=200, method="POST"):
    return HttpResult(method=method, url=url, ok=200 <= status_code < 400, status_code=status_code, headers={},
                      text="", content=b"", data=data, cost_ms=1)


class FakeBases:
    """Every base the server calls, keyed by callback address; records what it was asked to do."""

    def __init__(self):
        self.calls = []
        self.replies = {}
        self.statuses = {}

    def reply(self, url, data=None, error=None):
        """What the base answers at this URL (default {"code": 0})"""
        self.replies[url] = (data, error)

    def sent(self, host=None):
        return [(url, payload) for url, payload in self.calls if host is None or url.startswith(host)]

    async def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append((url, json.loads(data)))
        answer, error = self.replies.get(url, ({"code": 0, "message": "success", "data": None}, None))
        if error:
            raise error
        return http_result(url, answer)

    async def get(self, url, timeout=None, **kwargs):
        host = url.rsplit("/status", 1)[0]
        status = self.statuses.get(host)
        if status is None:
            # the real client reports a failed connection instead of raising
            return HttpResult(method="GET", url=url, ok=False, status_code=0, headers={}, text="", content=b"",
                              data=None, cost_ms=1, error="ConnectError('All connection attempts failed')")
        return http_result(url, {"code": 0, "data": status}, method="GET")


class ServerCase(unittest.TestCase):
    """A fresh server: no bots registered, empty databases, fake bases and AI."""

    def setUp(self):
        data_dir = tempfile.TemporaryDirectory()
        self.addCleanup(data_dir.cleanup)
        self.data_dir = Path(data_dir.name)
        db_engine.reset()
        bot_registry.reset()
        ai_rate_limit.reset()
        auto_ai_limit.reset()
        dbs = patch.object(db_engine, "DBS_DIR", self.data_dir)
        dbs.start()
        self.addCleanup(dbs.stop)
        self.addCleanup(self._forget_bots)
        db_engine.init_platform_db()

        self.bases = FakeBases()
        self.ai_calls = []
        self.ai_answer = lambda msg: http_result("http://ai.test/trigger", {"code": 0})
        for patcher in (
            patch.object(base_http, "async_post", self.bases.post),
            patch.object(base_http, "async_get", self.bases.get),
            patch.object(ai_business, "post_json", self._ai_post),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        os.chdir(SERVER_ROOT)
        self.addCleanup(os.chdir, _previous)
        self.client = TestClient(create_app())

    def _ai_post(self, url, payload, timeout=None):
        self.ai_calls.append((url, payload))
        answer = self.ai_answer(payload["msg"])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _forget_bots(self):
        for bot_id in list(bot_registry._opened):
            try:
                scheduler_service.scheduler.remove_jobstore(bot_id)
            except KeyError:
                pass
        bot_registry.reset()
        db_engine.reset()

    # ==================== helpers ====================

    def register(self, bot_id, platform="wechat", callback=None, name="") -> str:
        """Registers a bot the way its base does; returns its callback address"""
        callback = callback or f"http://{bot_id}.base:5000"
        response = self.post("/base/register", bot=self.bot(bot_id, platform, callback, name))
        self.assertEqual(response["code"], 0, response)
        return callback

    @staticmethod
    def bot(bot_id, platform="wechat", callback=None, name=""):
        return {"bot_id": bot_id, "platform": platform, "callback": callback or f"http://{bot_id}.base:5000",
                "name": name}

    def post(self, url, token=TOKEN, **body):
        if token is not None:
            body["token"] = token
        return self.client.post(url, json=body).json()

    def get(self, url, **params):
        return self.client.get(url, params=params).json()
