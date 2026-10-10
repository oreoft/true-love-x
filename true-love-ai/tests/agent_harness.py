"""
Runs the real AgentLoop with a scripted model.

The model is a pydantic_ai FunctionModel that gives scripted answers and records what it was shown. Sessions,
personas, memory and skill permissions use an in-memory database; the skills are fakes registered for the test;
replies and notices are captured instead of going to the server.
"""

import types
import unittest
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from ai_db import memory_db
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from true_love_common.chat_msg import ChatMsg

from true_love_ai.agent import agent_loop, server_client, skill_registry
from true_love_ai.core.session import Session


def tool(name: str, args: dict | str | None = None) -> tuple[str, Any]:
    """One tool call in a scripted answer; args may be a raw string to send broken JSON"""
    return name, {} if args is None else args


@dataclass
class Seen:
    """What the model was shown on one request"""
    messages: list[ModelMessage]
    info: AgentInfo

    @property
    def tools(self) -> list[str]:
        return [t.name for t in self.info.function_tools]

    def tool_def(self, name: str):
        return next(t for t in self.info.function_tools if t.name == name)

    @property
    def tool_choice(self):
        return (self.info.model_settings or {}).get("tool_choice")

    @property
    def instructions(self) -> str:
        return self.info.instructions or ""

    @property
    def last_user(self) -> str:
        for message in reversed(self.messages):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return part.content if isinstance(part.content, str) else "\n".join(map(str, part.content))
        return ""

    @property
    def tool_results(self) -> list[str]:
        last = self.messages[-1]
        return [str(p.content) for p in last.parts if isinstance(p, (ToolReturnPart, RetryPromptPart))]

    def text(self) -> str:
        lines = []
        for message in self.messages:
            for part in message.parts:
                if isinstance(part, (UserPromptPart, TextPart, SystemPromptPart)):
                    lines.append(str(part.content))
                elif isinstance(part, ToolCallPart):
                    lines.append(f"call {part.tool_name} {part.args_as_json_str()}")
                elif isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    lines.append(f"result {part.tool_name} {part.content}")
        return "\n".join(lines)


class Script:
    """
    A model that answers from a script. Each answer is
      - a str: a text reply
      - a list of tool(...) calls
      - an Exception: raised as if the provider failed
      - a callable(Seen) returning one of the above
    After the script runs out the last answer repeats.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen: list[Seen] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = Seen(list(messages), info)
        self.seen.append(seen)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer) and not isinstance(answer, type):
            answer = answer(seen)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):
            return ModelResponse(parts=[TextPart(answer)] if answer else [])
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in answer])

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self)


class Router:
    """Stands in for LLMRouter: hands the agent the scripted model"""

    def __init__(self, model):
        self.model = model

    def agent_model(self):
        return self.model


class Sessions:
    """Real sessions on the in-memory database, without background compression"""

    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        return self.sessions.setdefault(session_id, Session(session_id, compress_fn=None))


@dataclass
class Skill:
    handler: Callable
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    notify: Any = None
    description: str = "a test skill"


def skill_schema(name: str, skill: Skill) -> dict:
    schema = {"type": "function", "function": {"name": name, "description": skill.description,
                                               "parameters": skill.schema}}
    if skill.notify:
        schema["notify"] = skill.notify
    return schema


def returns(text: str) -> Callable:
    async def handler(params, ctx):
        return text
    return handler


def message(**fields) -> ChatMsg:
    defaults = dict(bot_id="bot_a", bot_name="小助手", platform="wechat", chat_id="room", sender_id="alice",
                    sender_name="Alice", msg_id="m1", content="hi", is_group=True, is_at_me=True)
    return ChatMsg(**{**defaults, **fields})


class AgentTestCase(unittest.IsolatedAsyncioTestCase):
    """Gives each test an in-memory database and a run() that drives the real AgentLoop"""

    def setUp(self):
        self.db = memory_db(self)
        self.sessions = Sessions()
        self.sent: list[tuple] = []
        self.skills: dict[str, Skill] = {}
        stack = ExitStack()
        self.addCleanup(stack.close)

        async def send_text(receiver, content, at_user="", reply_msg_id=""):
            self.sent.append((receiver, content, at_user, reply_msg_id))
            return True

        self.send = AsyncMock(side_effect=send_text)
        stack.enter_context(patch.object(server_client, "send_text", self.send))
        stack.enter_context(patch.dict(skill_registry._skills, clear=True))

    def add_skill(self, name: str, handler: Callable = None, **kwargs) -> Skill:
        skill = Skill(handler or returns(f"{name} done"), **kwargs)
        self.skills[name] = skill
        skill_registry._skills[name] = {"schema": skill_schema(name, skill), "handler": skill.handler}
        return skill

    def loop(self, script: Script, model=None) -> agent_loop.AgentLoop:
        return agent_loop.AgentLoop(llm_router=Router(model or script.model), session_manager=self.sessions)

    async def drive(self, *answers, msg: ChatMsg | None = None, model=None, **fields) -> Script:
        script = answers[0] if len(answers) == 1 and isinstance(answers[0], Script) else Script(*answers)
        await self.loop(script, model).run(msg or message(**fields))
        return script

    @property
    def replies(self) -> list[str]:
        return [content for _, content, _, _ in self.sent]

    def history(self, session_id: str = "bot_a:room") -> list:
        from true_love_ai.memory.session_repository import get_session_repo
        return get_session_repo().load(session_id)[1]


def config(**session):
    defaults = dict(ttl_seconds=86400, compress_threshold=50, compress_keep_recent=10)
    return types.SimpleNamespace(session=types.SimpleNamespace(**{**defaults, **session}))
