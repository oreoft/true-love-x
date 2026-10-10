"""
What actually goes over the wire to LiteLLM: the real LLMRouter and pydantic_ai's OpenAI model against an
OpenAI-compatible fake (e2e/fakes.FakeLLM) in this process.
"""

import json
from unittest.mock import patch

from agent_harness import AgentTestCase, message
from e2e.fakes import FakeLLM, call, calls, text
from openai import AsyncOpenAI

from true_love_ai.agent import agent_loop
from true_love_ai.llm import router as router_module
from true_love_ai.memory import skill_access_service

SAVE_PROFILE = {"type": "object", "properties": {"key": {"type": "string", "description": "键"},
                                                 "value": {"type": "string"}}, "required": ["key", "value"]}


class WireTests(AgentTestCase):
    @classmethod
    def setUpClass(cls):
        cls.llm = FakeLLM().start()

    @classmethod
    def tearDownClass(cls):
        cls.llm.stop()

    def setUp(self):
        super().setUp()
        self.llm.reset()
        self.models = {("chat", "default"): "openai/gpt-test", ("chat", "fallback"): ""}
        client = AsyncOpenAI(base_url=self.llm.url, api_key="k")
        for patcher in (patch.object(router_module, "get_openai_client", return_value=client),
                        patch.object(router_module.LLMRouter, "_model",
                                     lambda _, category, key="default": self.models[(category, key)])):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def deliver(self, **fields):
        loop = agent_loop.AgentLoop(llm_router=router_module.LLMRouter(), session_manager=self.sessions)
        await loop.run(message(**fields))
        return [r.body for r in self.llm.requests]

    async def test_tool_definitions_are_sent_as_the_skills_wrote_them(self):
        self.add_skill("save_user_profile", schema=SAVE_PROFILE, description="保存画像")
        self.add_skill("query_user_memory", description="查画像")
        self.llm.brain = lambda req: text("好")
        body = (await self.deliver())[0]

        self.assertEqual(body["model"], "openai/gpt-test")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["tools"], [
            {"type": "function", "function": {"name": "save_user_profile", "description": "保存画像",
                                              "parameters": SAVE_PROFILE}},
            {"type": "function", "function": {"name": "query_user_memory", "description": "查画像",
                                              "parameters": {"type": "object"}}},
        ])

    async def test_messages_go_out_as_system_history_then_the_live_message(self):
        self.llm.brain = lambda req: text("好")
        await self.deliver(content="第一句")
        bodies = await self.deliver(content="第二句", msg_id="m2")

        roles = [m["role"] for m in bodies[-1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(bodies[-1]["messages"][1]["content"], "Alice：第一句")
        self.assertTrue(bodies[-1]["messages"][3]["content"].startswith("【当前时间】"))
        # the system message is byte-identical across requests, so the provider can cache it
        self.assertEqual(bodies[0]["messages"][0], bodies[-1]["messages"][0])

    async def test_after_the_last_skill_round_the_model_is_told_not_to_call_skills(self):
        self.add_skill("query_user_memory")
        self.llm.brain = lambda req: text("就这些") if req.body.get("tool_choice") == "none" \
            else calls(call("query_user_memory"))
        with self.assertLogs("AgentLoop", "WARNING"):
            bodies = await self.deliver()

        self.assertEqual([b.get("tool_choice") for b in bodies],
                         ["auto"] * agent_loop.MAX_TOOL_ITERATIONS + ["none"])
        self.assertTrue(bodies[-1]["tools"])
        self.assertEqual(self.replies, ["就这些"])

    async def test_claude_gets_its_system_prompt_marked_for_caching_and_others_do_not(self):
        self.llm.brain = lambda req: text("好")
        await self.deliver()
        self.models[("chat", "default")] = "anthropic/claude-test"
        await self.deliver(msg_id="m2")

        openai_body, claude_body = (r.body for r in self.llm.requests)
        self.assertNotIn("cache_control_injection_points", openai_body)
        self.assertEqual(claude_body["cache_control_injection_points"], [{"location": "message", "role": "system"}])

    async def test_someone_with_no_skills_gets_history_without_tool_calls(self):
        self.add_skill("query_user_memory")
        self.llm.brain = lambda req: calls(call("query_user_memory")) if not req.tool_results else text("好")
        await self.deliver(content="查一下")
        skill_access_service.set_points("query_user_memory", ["wechat:bot_a:boss"], kind="builtin")
        self.llm.reset()
        self.llm.brain = lambda req: text("好")
        body = (await self.deliver(content="再说一句", msg_id="m2"))[0]

        self.assertNotIn("tools", body)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        self.assertNotIn("tool_calls", json.dumps(body))
