"""
Starts the real tl-server and tl-ai as separate processes, wired together the way they run in production:

    test → POST server /base/on-message → server POST ai /trigger → ai runs the agent → ai POST server /action/*
         → server POST base /send/text

Only the base and the LLM are fakes (see fakes.py). The dev addresses are fixed in true_love_common.hosts
(server on localhost:8078, AI on localhost:8079), so those two ports must be free.

Each service gets a throwaway working directory with its own config-dev.yaml and dbs/.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from true_love_common.hosts import DEV_AI_HOST, DEV_SERVER_HOST

from .fakes import FakeBase, FakeLLM

REPO = Path(__file__).resolve().parents[3]
AI_ROOT = REPO / "true-love-ai"
SERVER_ROOT = REPO / "true-love-server"
TOKEN = "e2e-token"
BOT_ID = "wxid_e2e"
BOT_NAME = "小助手"


def _python(project: Path) -> str:
    python = project / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"{project.name} has no venv; run `uv sync` in {project}")
    return str(python)


class Stack:
    def __init__(self):
        self.home = Path(tempfile.mkdtemp(prefix="tl-e2e-"))
        self.llm = FakeLLM()
        self.base = FakeBase()
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Path] = {}

    # ---------- lifecycle ----------

    def start(self):
        for port in (8078, 8079):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"port {port} is taken (a tl-server / tl-ai left over from another run?)")
        try:
            return self._start()
        except BaseException:
            self.stop()
            raise

    def _start(self):
        self.llm.start()
        self.base.start()
        ai_home = self.home / "ai"
        server_home = self.home / "server"
        ai_home.mkdir()
        server_home.mkdir()
        # the server serves its admin page from ./static
        (server_home / "static").symlink_to(SERVER_ROOT / "static")
        (ai_home / "config-dev.yaml").write_text(json.dumps({
            "http": {"host": "127.0.0.1", "port": 8079, "token": [TOKEN]},
            "session": {"ttl_seconds": 86400, "compress_threshold": 8, "compress_keep_recent": 2},
            "platform_key": {"litellm_api_key": "e2e-key", "litellm_base_url": self.llm.url},
        }), encoding="utf-8")
        (server_home / "config-dev.yaml").write_text(json.dumps({
            "default_bot_id": BOT_ID,
            "http_token": [TOKEN],
            "alapi": {"token": ""},
            "http": {"host": "127.0.0.1", "port": 8078},
        }), encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
        env.update(PYTHONUNBUFFERED="1", PYDANTIC_AI_NO_BANNER="1")
        self._spawn("server", server_home, _python(SERVER_ROOT), "-m", "true_love_server", env=env)
        self._spawn("ai", ai_home, _python(AI_ROOT), "-c", "from true_love_ai.main import main; main()", env=env)
        self._wait_up("server", f"{DEV_SERVER_HOST}/ping")
        self._wait_up("ai", f"{DEV_AI_HOST}/health")

        # point every model at the fake LLM; compress and vision get their own names so the fake can tell them apart
        for category, model in (("chat", "e2e/chat"), ("compress", FakeLLM.COMPRESS_MODEL),
                                ("vision", FakeLLM.VISION_MODEL)):
            self.ai_admin("/admin/model/save", category=category, key="default", value=model)
        self.ai_admin("/admin/model/save", category="chat", key="fallback", value="e2e/chat-fallback")
        self.server("/base/register")
        # tests send many messages from the same people; the server's per-person rate limit would swallow them
        r = httpx.post(f"{DEV_SERVER_HOST}/admin/bots/{BOT_ID}/listen/settings",
                       json={"ai_rate_limit": {"count": 10000, "seconds": 1}}, timeout=10)
        r.raise_for_status()
        return self

    def stop(self):
        for proc in self.procs.values():
            proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.llm.stop()
        self.base.stop()
        if not os.environ.get("TL_E2E_KEEP"):
            shutil.rmtree(self.home, ignore_errors=True)

    def _spawn(self, name: str, cwd: Path, *cmd: str, env: dict):
        log = self.home / f"{name}.log"
        self.logs[name] = log
        self.procs[name] = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT)

    def _wait_up(self, name: str, url: str, timeout: float = 60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.procs[name].poll() is not None:
                raise RuntimeError(f"{name} exited early:\n{self.log_tail(name)}")
            try:
                if httpx.get(url, timeout=1).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{name} did not come up at {url}:\n{self.log_tail(name)}")

    def log_tail(self, name: str, lines: int = 80) -> str:
        text = self.logs[name].read_text(encoding="utf-8", errors="replace") if name in self.logs else ""
        return "\n".join(text.splitlines()[-lines:])

    def wait_log(self, name: str, needle: str, timeout: float = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.log(name):
                return
            time.sleep(0.05)
        raise AssertionError(f"{needle!r} never showed up in the {name} log")

    def log(self, name: str) -> str:
        return self.logs[name].read_text(encoding="utf-8", errors="replace")

    # ---------- calls ----------

    def server(self, path: str, **body) -> dict:
        payload = {"token": TOKEN, "bot": {"bot_id": BOT_ID, "platform": "wechat", "callback": self.base.url,
                                           "name": BOT_NAME}, **body}
        r = httpx.post(f"{DEV_SERVER_HOST}{path}", json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def ai_admin(self, path: str, **body) -> dict:
        r = httpx.post(f"{DEV_AI_HOST}{path}", json={"token": TOKEN, **body}, timeout=10)
        r.raise_for_status()
        data = r.json()
        assert str(data.get("code")) == "0", data
        return data

    def ai_server_action(self, path: str, **body) -> dict:
        """Call the server's /action/* the way AI does"""
        r = httpx.post(f"{DEV_SERVER_HOST}{path}", json={"token": TOKEN, "bot_id": BOT_ID, **body}, timeout=10)
        r.raise_for_status()
        return r.json()

    def say(self, content: str = "", *, chat: str, sender: str = "alice", sender_name: str = "",
            group: bool = True, at_me: bool = True, msg_type: str = "text", msg_id: str = "", **extra) -> dict:
        """A message arriving from the base, as base would report it"""
        msg_id = msg_id or f"m{time.time_ns()}"
        msg = {
            "platform": "wechat", "bot_id": BOT_ID, "bot_name": BOT_NAME, "msg_id": msg_id, "msg_type": msg_type,
            "content": content, "sender_id": sender, "sender_name": sender_name or sender,
            "chat_id": chat if group else sender, "chat_name": chat if group else sender,
            "is_group": group, "is_at_me": at_me if group else False, "mention": f"@{BOT_NAME}" if at_me and group else "",
            "msg_hash": msg_id, **extra,
        }
        if at_me and group:
            msg["content"] = f"@{BOT_NAME} {content}"
        return self.server("/base/on-message", msg=msg)
