"""
The two network edges the e2e stack does not run for real: the LLM (an OpenAI-compatible endpoint, like the
LiteLLM proxy) and the base (the bot client that sends messages). Both run in the test process on their own
port, so a test can script the model and read back what the bot was asked to send.
"""

import json
import socket
import threading
import time
import uuid
from typing import Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Runs a FastAPI app with uvicorn in a daemon thread"""

    def __init__(self, app: FastAPI):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self):
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError(f"fake server on {self.port} did not start")
            time.sleep(0.02)
        return self

    def stop(self):
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=5)


# ==================== LLM ====================

def text(content: str) -> dict:
    """A model answer: plain text"""
    return {"content": content}


def call(name: str, arguments: dict | str | None = None, call_id: str | None = None) -> dict:
    """One tool call; arguments may be a raw string to send broken JSON"""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)
    return {"id": call_id or f"call_{uuid.uuid4().hex[:8]}", "name": name, "arguments": args}


def calls(*tool_calls: dict) -> dict:
    """A model answer: one or more tool calls"""
    return {"tool_calls": list(tool_calls)}


class LLMRequest:
    """One chat completion request the AI sent"""

    def __init__(self, body: dict):
        self.body = body
        self.model = body.get("model", "")
        self.messages = body.get("messages", [])
        self.tools = [t["function"]["name"] for t in body.get("tools") or []]

    @staticmethod
    def _text(message: dict) -> str:
        content = message.get("content")
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        return content or ""

    @property
    def system(self) -> str:
        return "\n".join(self._text(m) for m in self.messages if m.get("role") in ("system", "developer"))

    @property
    def last_user(self) -> str:
        users = [m for m in self.messages if m.get("role") == "user"]
        return self._text(users[-1]) if users else ""

    @property
    def tool_results(self) -> list[str]:
        """The tool results at the end of the conversation (answers to the latest tool calls)"""
        results = []
        for m in reversed(self.messages):
            if m.get("role") != "tool":
                break
            results.insert(0, self._text(m))
        return results

    @property
    def has_image(self) -> bool:
        return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
                   for m in self.messages)

    def all_text(self) -> str:
        return "\n".join(f"{m.get('role')}: {self._text(m)}" for m in self.messages)


Brain = Callable[[LLMRequest], dict]


def echo_brain(req: LLMRequest) -> dict:
    return text(f"收到：{req.last_user}")


class FakeLLM:
    """
    OpenAI-compatible /v1/chat/completions. Each request goes to the current brain, which returns
    text(...) or calls(...). Requests for the compress model and vision requests get canned answers unless
    the brain is set to handle them.
    """

    COMPRESS_MODEL = "e2e/compress"
    VISION_MODEL = "e2e/vision"

    def __init__(self):
        self.requests: list[LLMRequest] = []
        self.brain: Brain = echo_brain
        self.vision_answer = "图上是一只橘猫"
        self.compress_answer = "【摘要】之前聊过天"
        self.fail_next = 0
        self._lock = threading.Lock()
        app = FastAPI()
        app.post("/v1/chat/completions")(self._completions)
        self._server = _Server(app)
        self.url = f"{self._server.url}/v1"

    def start(self):
        self._server.start()
        return self

    def stop(self):
        self._server.stop()

    def reset(self):
        with self._lock:
            self.requests = []
            self.brain = echo_brain
            self.fail_next = 0

    def agent_requests(self) -> list[LLMRequest]:
        """Requests from the agent loop (not compress / vision helpers)"""
        return [r for r in self.requests if r.model not in (self.COMPRESS_MODEL, self.VISION_MODEL)]

    async def _completions(self, request: Request):
        body = await request.json()
        req = LLMRequest(body)
        with self._lock:
            self.requests.append(req)
            if self.fail_next > 0:
                self.fail_next -= 1
                return JSONResponse({"error": {"message": "fake upstream down", "type": "server_error"}},
                                    status_code=500)
        if req.model == self.COMPRESS_MODEL:
            answer = text(self.compress_answer)
        elif req.model == self.VISION_MODEL:
            answer = text(self.vision_answer)
        else:
            try:
                answer = self.brain(req)
            except Exception as e:  # a broken script shows up as a 500 in the AI's logs
                return JSONResponse({"error": {"message": f"brain crashed: {e!r}"}}, status_code=500)
        message = {"role": "assistant", "content": answer.get("content")}
        finish = "stop"
        if answer.get("tool_calls"):
            message["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"]}}
                                     for c in answer["tool_calls"]]
            finish = "tool_calls"
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}", "object": "chat.completion", "created": int(time.time()),
            "model": req.model, "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


def scripted(*answers: dict) -> Brain:
    """A brain that gives these answers in order, then repeats the last one"""
    queue = list(answers)

    def brain(req: LLMRequest) -> dict:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return brain


# ==================== base ====================

class FakeBase:
    """The bot's base: records what server asks it to send, serves media files"""

    def __init__(self):
        self.sent: list[dict] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()
        app = FastAPI()
        app.post("/send/text")(self._send_text)
        app.post("/send/file")(self._send_file)
        app.get("/status")(self._status)
        app.get("/media/{path:path}")(self._media)
        self._server = _Server(app)
        self.url = self._server.url

    def start(self):
        self._server.start()
        return self

    def stop(self):
        self._server.stop()

    def reset(self):
        with self._lock:
            self.sent = []

    async def _send_text(self, request: Request):
        body = await request.json()
        with self._lock:
            self.sent.append({"kind": "text", **body})
        return {"code": 0, "message": "ok", "data": None}

    async def _send_file(self, request: Request):
        body = await request.json()
        with self._lock:
            self.sent.append({"kind": "file", **body})
        return {"code": 0, "message": "ok", "data": None}

    async def _status(self):
        return {"code": 0, "data": {"wx_online": True, "self_name": "e2e-bot", "since": "", "bot_id": ""}}

    async def _media(self, path: str):
        if path not in self.media:
            return Response(status_code=404)
        content, mime = self.media[path]
        return Response(content=content, media_type=mime)

    def texts(self, receiver: str | None = None) -> list[dict]:
        with self._lock:
            return [s for s in self.sent if s["kind"] == "text" and (receiver is None or s.get("sendReceiver") == receiver)]

    def wait_texts(self, receiver: str, count: int = 1, timeout: float = 20) -> list[dict]:
        """Wait until the base got at least count texts for receiver"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = self.texts(receiver)
            if len(got) >= count:
                return got
            time.sleep(0.05)
        raise AssertionError(f"base got {self.texts(receiver)} for {receiver}, expected {count} texts; all sent: {self.sent}")
