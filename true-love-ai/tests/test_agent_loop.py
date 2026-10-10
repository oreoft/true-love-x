"""
The agent loop on pydantic_ai: what the model is shown, which skills it may call, what goes out and what is
remembered. The model is scripted (see agent_harness).
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent_harness import AgentTestCase, Script, message, returns, tool
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.fallback import FallbackModel

from true_love_ai.agent import agent_loop, outcome, prompt, tools
from true_love_ai.agent.outcome import Outcome
from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.agent.skills.permission import DENIED_TEXT
from true_love_ai.memory import (
    dynamic_skill_service,
    memory_manager,
    persona_service,
    skill_access_service,
)
from true_love_ai.memory.session_repository import TOOLS, get_session_repo


def boom(exc: Exception):
    async def handler(params, ctx):
        raise exc
    return handler


class ReplyTests(AgentTestCase):
    async def test_group_reply_goes_to_the_group_mentioning_the_sender_and_quoting_the_message(self):
        await self.drive("好耶")

        self.assertEqual(self.sent, [("room", "好耶", "alice", "m1")])

    async def test_private_reply_goes_to_the_sender(self):
        await self.drive("好耶", is_group=False, chat_id="alice")

        self.assertEqual(self.sent, [("alice", "好耶", "", "")])

    async def test_unreadable_message_gets_the_fallback_without_asking_the_model(self):
        script = Script("不该调")
        with self.assertLogs("Outcome", "WARNING") as logs:
            await self.drive(script, msg=message(msg_type="voice", content=""))

        self.assertEqual(self.replies, [outcome.FALLBACK_TEXT[Outcome.UNREADABLE]])
        self.assertEqual(script.seen, [])
        self.assertIn("outcome=unreadable", logs.output[-1])

    async def test_the_mention_is_not_part_of_the_question(self):
        script = await self.drive("在", msg=message(content="@小助手 在吗", mention="@小助手"))

        self.assertTrue(script.seen[0].last_user.endswith("Alice：在吗"))

    async def test_a_reply_that_did_not_go_out_is_logged_as_not_sent(self):
        self.send.side_effect = None
        self.send.return_value = False
        with self.assertLogs("Outcome", "INFO") as logs:
            await self.drive("好耶")

        self.assertIn("outcome=replied sent=False", logs.output[-1])


class PromptTests(AgentTestCase):
    async def test_persona_and_rules_are_in_the_instructions_and_the_skill_list_is_not_repeated_there(self):
        self.add_skill("save_user_profile", description="保存用户画像的技能说明")
        persona_service.save_persona("bot_a", "room", "你是{name}，说话带喵。", "")
        script = await self.drive("喵")

        seen = script.seen[0]
        self.assertIn("你是小助手，说话带喵。", seen.instructions)
        self.assertIn(prompt.FORMAT_RULE, seen.instructions)
        self.assertNotIn("保存用户画像的技能说明", seen.instructions)
        self.assertEqual(seen.tool_def("save_user_profile").description, "保存用户画像的技能说明")

    async def test_the_volatile_parts_are_in_the_latest_message_so_the_prefix_stays_cacheable(self):
        memory_manager.upsert_user_memory("bot_a:room", "alice", [{"key": "occupation", "value": "程序员"}])
        script = Script("一", "二")
        loop = self.loop(script)
        await loop.run(message(content="第一句"))
        await asyncio.sleep(1.1)
        await loop.run(message(content="第二句", msg_id="m2"))

        first, second = script.seen
        # same instructions and the same history prefix on both requests
        self.assertEqual(first.instructions, second.instructions)
        self.assertNotRegex(first.instructions, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        self.assertNotIn("程序员", first.instructions)
        self.assertIn("【当前时间】", second.last_user)
        self.assertIn("已知信息：职业：程序员", second.last_user)
        # what was sent the first time is replayed as stored: speaker and words only
        self.assertEqual(second.messages[0].parts[0].content, "Alice：第一句")

    async def test_the_summary_goes_into_the_instructions(self):
        get_session_repo().append_message("bot_a:room", "user", "很久以前")
        get_session_repo().compress("bot_a:room", "【摘要】聊过猫", upto_id=10**9)
        script = await self.drive("嗯")

        self.assertIn("【摘要】聊过猫", script.seen[0].instructions)

    async def test_user_timezone_from_the_profile_is_used_for_the_time(self):
        memory_manager.upsert_user_memory("bot_a:room", "alice", [{"key": "timezone", "value": "America/Chicago"}])
        script = await self.drive("几点了")

        self.assertIn("时区=America/Chicago", script.seen[0].last_user)


class HistoryTests(AgentTestCase):
    async def test_group_history_says_who_said_what(self):
        script = Script("ok")
        loop = self.loop(script)
        await loop.run(message(content="我是张三", sender_id="zhang", sender_name="张三"))
        await loop.run(message(content="我是李四", sender_id="li", sender_name="李四", msg_id="m2"))

        history = script.seen[-1].text()
        self.assertIn("张三：我是张三", history)
        self.assertIn("李四：我是李四", history.split("\n")[-1])

    async def test_private_history_has_no_speaker_prefix(self):
        await self.drive("ok", is_group=False, chat_id="alice", content="私聊的话")

        self.assertEqual([(r.role, r.content) for r in self.history("bot_a:alice")],
                         [("user", "私聊的话"), ("assistant", "ok")])

    async def test_tool_calls_and_results_are_kept_for_the_next_turn(self):
        self.add_skill("save_user_profile", returns("记住了：爵士乐"))
        script = Script([tool("save_user_profile", {"key": "interest.music", "value": "爵士乐"})], "好的", "你喜欢爵士乐")
        loop = self.loop(script)
        await loop.run(message(content="记住我喜欢爵士乐"))
        await loop.run(message(content="我喜欢什么", msg_id="m2"))

        history = script.seen[-1].messages
        calls = [p for m in history if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)]
        results = [p for m in history if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        self.assertEqual([c.tool_name for c in calls], ["save_user_profile"])
        self.assertEqual([r.content for r in results], ["记住了：爵士乐"])
        self.assertEqual([(r.type, r.role) for r in self.history()],
                         [("msg", "user"), (TOOLS, None), ("msg", "assistant"), ("msg", "user"), ("msg", "assistant")])

    async def test_instructions_and_the_live_header_are_not_stored(self):
        self.add_skill("query_user_memory")
        await self.drive([tool("query_user_memory")], "好")

        stored = json.dumps([r.content for r in self.history()], ensure_ascii=False)
        self.assertNotIn("当前时间", stored)
        self.assertNotIn(prompt.FORMAT_RULE[:10], stored)
        self.assertNotIn('"instructions": "', stored.replace('\\"', '"'))

    async def test_a_turn_that_was_not_answered_keeps_only_what_the_user_said(self):
        self.add_skill("analyze_image", returns("一个人在健身"))
        await self.drive([tool("analyze_image")], outcome.SKIP_MARKER, is_at_me=False)

        self.assertEqual([(r.type, r.role, r.content) for r in self.history()], [("msg", "user", "Alice：hi")])


    async def test_after_a_skipped_auto_turn_the_next_mention_still_sees_it(self):
        script = Script("[不回复]", "那张图是健身照")
        loop = self.loop(script)
        await loop.run(message(is_at_me=False, content="[图片:x.jpg] 请看看这张图片"))
        await loop.run(message(content="刚才那张图是啥", msg_id="m2"))

        self.assertEqual(self.replies, ["那张图是健身照"])
        self.assertIn("Alice：[图片:x.jpg] 请看看这张图片", script.seen[-1].text())

    async def test_conversations_left_by_the_old_compressor_still_load(self):
        """the old compressor kept the last N messages wherever they fell, so history may start with a reply"""
        repo = get_session_repo()
        repo.append_message("bot_a:room", "assistant", "上次的回答")
        repo.append_message("bot_a:room", "user", "Alice：老问题")
        repo.append_message("bot_a:room", "assistant", "老回答")
        script = await self.drive("新回答")

        self.assertEqual(self.replies, ["新回答"])
        self.assertIn("上次的回答", script.seen[0].text())


class AutoReplyTests(AgentTestCase):
    async def test_auto_reply_with_nothing_to_say_is_not_sent(self):
        with self.assertLogs("Outcome", "INFO") as logs:
            script = await self.drive("[不回复]", is_at_me=False, content="看这个")

        self.assertEqual(self.sent, [])
        self.assertIn(agent_loop.AUTO_REPLY_RULE, script.seen[0].last_user)
        self.assertIn("outcome=skipped", logs.output[-1])

    async def test_auto_reply_with_something_to_say_is_sent_and_remembered(self):
        await self.drive("这哥们深蹲姿势不太对", is_at_me=False)

        self.assertEqual(self.sent, [("room", "这哥们深蹲姿势不太对", "alice", "m1")])
        self.assertEqual(self.history()[-1].content, "这哥们深蹲姿势不太对")

    async def test_the_auto_rule_is_not_remembered_and_not_given_to_mentions(self):
        script = Script("嗯", "好的")
        loop = self.loop(script)
        await loop.run(message(is_at_me=False))
        await loop.run(message(msg_id="m2"))

        self.assertNotIn(agent_loop.AUTO_REPLY_RULE, script.seen[1].text())

    async def test_llm_error_on_an_auto_reply_stays_quiet(self):
        with self.assertLogs("Outcome", "WARNING") as logs, self.assertLogs("AgentLoop", "ERROR"):
            await self.drive(RuntimeError("down"), is_at_me=False)

        self.assertEqual(self.sent, [])
        self.assertIn("outcome=llm_error sent=False auto=True", logs.output[-1])

    async def test_auto_reply_after_a_skill_that_failed_then_worked_is_sent(self):
        attempts = []

        async def flaky(params, ctx):
            attempts.append(1)
            if len(attempts) == 1:
                raise SkillFailed("图片没下载下来")
            return "一张猫图"

        self.add_skill("analyze_image", flaky, notify="正在看图")
        with self.assertLogs("Outcome", "INFO") as logs, self.assertLogs("AgentLoop", "ERROR"):
            await self.drive([tool("analyze_image")], [tool("analyze_image")], "哈哈这猫绝了", is_at_me=False)

        # no "正在看图" notice when nobody asked, and the answer goes out
        self.assertEqual(self.replies, ["哈哈这猫绝了"])
        self.assertIn("outcome=replied", logs.output[-1])

    async def test_auto_reply_after_a_skill_that_stayed_broken_is_not_sent(self):
        self.add_skill("analyze_image", boom(SkillFailed("图片没下载下来")))
        with self.assertLogs("Outcome", "INFO") as logs, self.assertLogs("AgentLoop", "ERROR"):
            await self.drive([tool("analyze_image")], "图片好像坏了", is_at_me=False)

        self.assertEqual(self.sent, [])
        self.assertIn("outcome=tool_failed sent=False auto=True", logs.output[-1])


class NoticeTests(AgentTestCase):
    async def notices(self, **fields):
        self.add_skill("analyze_image", returns("一只猫"), notify="让我仔细看看这张图片")
        await self.drive([tool("analyze_image")], "图上是一只猫", **fields)
        return self.replies[:-1]

    async def test_auto_triggered_group_message_gets_no_notice(self):
        self.assertEqual(await self.notices(is_at_me=False), [])

    async def test_mention_in_a_group_gets_the_notice(self):
        self.assertEqual(await self.notices(), ["让我仔细看看这张图片"])

    async def test_private_chat_gets_the_notice(self):
        self.assertEqual(await self.notices(is_group=False, chat_id="alice"), ["让我仔细看看这张图片"])

    async def test_one_of_several_notices_is_picked(self):
        self.add_skill("generate_image", notify=["画画中", "马上就好"])
        await self.drive([tool("generate_image")], "画好了")

        self.assertIn(self.replies[0], ["画画中", "马上就好"])


class EmptyReplyTests(AgentTestCase):
    async def test_the_skill_result_is_sent_when_the_model_replies_empty(self):
        self.add_skill("save_user_profile", returns("时区已经记下啦"))
        with self.assertLogs("Outcome", "WARNING"):
            await self.drive([tool("save_user_profile")], "")

        self.assertEqual(self.replies, ["时区已经记下啦"])

    async def test_an_empty_first_reply_still_gets_an_answer(self):
        with self.assertLogs("Outcome", "WARNING"):
            await self.drive("  ")

        self.assertEqual(self.replies, [outcome.FALLBACK_TEXT[Outcome.EMPTY_FALLBACK]])


class SkillFailureTests(AgentTestCase):
    async def run_failing(self, handler):
        self.add_skill("generate_image", handler)
        with self.assertLogs("Outcome", "INFO") as outcome_logs:
            script = await self.drive([tool("generate_image", {"prompt": "猫"})], "画失败了，晚点再试吧")
        return script, outcome_logs.output[-1]

    async def test_skill_failure_is_logged_with_its_cause_and_counted_as_failed(self):
        async def fail(params, ctx):
            try:
                raise TimeoutError("upstream timed out")
            except TimeoutError as e:
                raise SkillFailed("呜呜~图片生成出错了捏") from e

        with self.assertLogs("AgentLoop", "ERROR") as loop_logs:
            script, outcome_log = await self.run_failing(fail)

        self.assertIn("outcome=tool_failed", outcome_log)
        self.assertIn("detail=generate_image", outcome_log)
        error = next(r for r in loop_logs.records if r.levelname == "ERROR")
        self.assertIsNotNone(error.exc_info)
        self.assertEqual(script.seen[-1].tool_results, ["呜呜~图片生成出错了捏"])
        self.assertEqual(self.replies, ["画失败了，晚点再试吧"])

    async def test_unexpected_exception_is_also_counted_as_failed(self):
        with self.assertLogs("AgentLoop", "ERROR"):
            script, outcome_log = await self.run_failing(boom(RuntimeError("boom")))

        self.assertIn("outcome=tool_failed", outcome_log)
        self.assertEqual(script.seen[-1].tool_results, ["[执行失败] boom"])

    async def test_a_skill_that_takes_too_long_is_cut_off(self):
        async def slow(params, ctx):
            await asyncio.sleep(5)

        self.add_skill("generate_video", slow)
        with patch.object(tools.skill_registry, "get_timeout", return_value=0.05), \
                self.assertLogs("AgentLoop", "ERROR"), self.assertLogs("Outcome", "INFO") as logs:
            script = await self.drive([tool("generate_video")], "视频超时了")

        self.assertIn("[执行超时]", script.seen[-1].tool_results[0])
        self.assertIn("outcome=tool_failed", logs.output[-1])

    async def test_bad_arguments_go_back_to_the_model_instead_of_running_with_nothing(self):
        handler = AsyncMock(return_value="不该执行")
        self.add_skill("set_reminder", handler)
        with self.assertLogs("Outcome", "INFO") as logs:
            script = await self.drive([tool("set_reminder", '{"content": "关')], "参数错了，我重新来", )

        handler.assert_not_awaited()
        self.assertIn("Invalid JSON", script.seen[-1].tool_results[0])
        self.assertIn("outcome=replied", logs.output[-1])

    async def test_arguments_that_are_not_an_object_are_rejected(self):
        handler = AsyncMock(return_value="不该执行")
        self.add_skill("set_reminder", handler)
        script = await self.drive([tool("set_reminder", "[1, 2]")], "好")

        handler.assert_not_awaited()
        self.assertIn("object", script.seen[-1].tool_results[0])

    async def test_valid_arguments_reach_the_skill(self):
        handler = AsyncMock(return_value="定好了")
        self.add_skill("set_reminder", handler)
        await self.drive([tool("set_reminder", '{"content": "关火", "target_time_iso": "2099-01-01T00:00:00+08:00"}')],
                       "好")

        params, ctx = handler.await_args.args
        self.assertEqual(params, {"content": "关火", "target_time_iso": "2099-01-01T00:00:00+08:00"})
        self.assertEqual({k: ctx[k] for k in ("session_id", "receiver", "at_user", "chat", "sender_name")},
                         {"session_id": "bot_a:room", "receiver": "room", "at_user": "alice", "chat": "room",
                          "sender_name": "Alice"})

    async def test_several_skills_called_at_once_all_run(self):
        self.add_skill("a", returns("A 的结果"))
        self.add_skill("b", returns("B 的结果"))
        script = await self.drive([tool("a"), tool("b")], "都好了")

        self.assertEqual(sorted(script.seen[-1].tool_results), ["A 的结果", "B 的结果"])


class PermissionTests(AgentTestCase):
    async def test_skills_the_sender_may_not_use_are_not_offered(self):
        self.add_skill("set_model")
        self.add_skill("gold_price")
        skill_access_service.set_points("set_model", ["wechat:bot_a:boss"], kind="builtin")
        script = await self.drive("好")

        self.assertEqual(script.seen[0].tools, ["gold_price"])

    async def test_a_skill_the_model_calls_anyway_is_refused_not_failed(self):
        handler = AsyncMock(return_value="不该执行")
        self.add_skill("set_model", handler)
        skill_access_service.set_points("set_model", ["wechat:bot_a:boss"], kind="builtin")
        with self.assertLogs("Outcome", "INFO") as logs:
            script = await self.drive([tool("set_model")], "没权限")

        handler.assert_not_awaited()
        # pydantic_ai refuses a tool it did not offer; the model is told and the skill never runs
        self.assertTrue(script.seen[-1].tool_results)
        self.assertIn("outcome=replied", logs.output[-1])

    async def test_a_denied_skill_inside_execute_is_not_a_failure(self):
        self.add_skill("set_model")
        deps = tools.AgentDeps(skill_ctx={"receiver": "room", "at_user": ""}, access={}, instructions="",
                               notify=False)
        with patch("true_love_ai.agent.skills.permission.check_permission", return_value=False):
            result, failed = await tools.execute_skill("set_model", {}, deps)

        self.assertEqual((result, failed), (DENIED_TEXT, False))

    async def test_skill_run_lists_the_dynamic_skills_this_sender_may_use(self):
        self.add_skill("skill_run", description="执行一个已保存的动态技能。")
        dynamic_skill_service.save_skill("pypi_version", "查 PyPI 版本", "查某个包的最新版本", "echo 1", {}, "boss",
                                         default_points=["*:*:*:*"])
        dynamic_skill_service.save_skill("secret_cmd", "秘密命令", "只有老板能用", "echo 2", {}, "boss",
                                         default_points=["wechat:bot_a:boss"])
        script = await self.drive("好")

        description = script.seen[0].tool_def("skill_run").description
        self.assertIn("pypi_version（查 PyPI 版本）: 查某个包的最新版本", description)
        self.assertNotIn("secret_cmd", description)


class RoundLimitTests(AgentTestCase):
    async def test_a_model_that_keeps_calling_skills_is_made_to_answer(self):
        self.add_skill("query_user_memory", returns("什么都没记"))

        def keep_calling(seen):
            return "查了好几遍，就这些" if seen.tool_choice == "none" else [tool("query_user_memory")]

        with self.assertLogs("AgentLoop", "WARNING") as logs, self.assertLogs("Outcome", "INFO") as outcome_logs:
            script = await self.drive(keep_calling)

        self.assertEqual(self.replies, ["查了好几遍，就这些"])
        self.assertEqual(len(script.seen), agent_loop.MAX_TOOL_ITERATIONS + 1)
        # tools stay defined on the last request (history holds tool calls), the model is just told not to use them
        self.assertEqual(script.seen[-1].tools, ["query_user_memory"])
        self.assertEqual([s.tool_choice for s in script.seen[:-1]], [None] * agent_loop.MAX_TOOL_ITERATIONS)
        self.assertIn("上限", "\n".join(logs.output))
        self.assertIn("outcome=replied", outcome_logs.output[-1])

    async def test_a_model_that_ignores_the_limit_runs_no_more_skills_and_what_ran_is_remembered(self):
        handler = AsyncMock(return_value="提醒设好了")
        self.add_skill("set_reminder", handler)
        with self.assertLogs("AgentLoop", "WARNING"), self.assertLogs("Outcome", "WARNING") as logs:
            script = await self.drive([tool("set_reminder", {"content": "交房租"})])

        self.assertEqual(handler.await_count, agent_loop.MAX_TOOL_ITERATIONS)
        self.assertIn(tools.ROUNDS_USED_UP, script.seen[-1].tool_results)
        self.assertEqual(self.replies, [outcome.FALLBACK_TEXT[Outcome.TOO_MANY_ROUNDS]])
        self.assertIn("outcome=too_many_rounds", logs.output[-1])
        # the reminders that were set are in the history, so the next turn does not set them again
        rows = self.history()
        self.assertEqual([r.type for r in rows], ["msg", TOOLS, "msg"])
        self.assertEqual(rows[1].content.count("提醒设好了"), agent_loop.MAX_TOOL_ITERATIONS)


class ModelFailureTests(AgentTestCase):
    async def test_llm_error_when_someone_asked_sends_the_fallback(self):
        with self.assertLogs("Outcome", "WARNING") as logs, self.assertLogs("AgentLoop", "ERROR"):
            await self.drive(RuntimeError("down"))

        self.assertEqual(self.replies, [outcome.FALLBACK_TEXT[Outcome.LLM_ERROR]])
        self.assertEqual(self.history()[-1].content, outcome.FALLBACK_TEXT[Outcome.LLM_ERROR])
        self.assertIn("outcome=llm_error", logs.output[-1])

    async def test_skills_that_ran_before_the_model_failed_are_remembered(self):
        self.add_skill("set_reminder", returns("提醒设好了"))
        with self.assertLogs("Outcome", "WARNING"), self.assertLogs("AgentLoop", "ERROR"):
            await self.drive([tool("set_reminder", {"content": "交房租"})], RuntimeError("down"))

        rows = self.history()
        self.assertEqual([r.type for r in rows], ["msg", TOOLS, "msg"])
        self.assertIn("提醒设好了", rows[1].content)
        self.assertEqual(rows[2].content, outcome.FALLBACK_TEXT[Outcome.LLM_ERROR])

    async def test_the_fallback_model_answers_when_the_primary_fails(self):
        primary = Script(ModelHTTPError(503, "e2e/chat", "overloaded"))
        backup = Script("备用模型来回答")
        await self.drive(primary, model=FallbackModel(primary.model, backup.model))

        self.assertEqual(self.replies, ["备用模型来回答"])

    async def test_the_model_is_asked_for_on_every_message(self):
        """tl-admin can switch models at any time; the next message uses the new one"""
        router_calls = []

        class Router:
            def agent_model(self):
                router_calls.append(1)
                return Script("嗯").model

        loop = agent_loop.AgentLoop(llm_router=Router(), session_manager=self.sessions)
        await loop.run(message())
        await loop.run(message(msg_id="m2"))

        self.assertEqual(len(router_calls), 2)


class SessionKeyTests(AgentTestCase):
    async def test_same_group_on_two_bots_is_two_conversations(self):
        for bot_id in ("wxid_m8s", "wxid_ser"):
            persona_service.save_persona(bot_id, "群A", f"我是 {bot_id} 的人设", "")
            script = Script("嗯")
            with patch.object(agent_loop, "get_user_context", return_value=None) as user_ctx:
                await self.drive(script, msg=message(bot_id=bot_id, chat_id="群A"))
            user_ctx.assert_called_once_with(f"{bot_id}:群A", "alice")
            self.assertIn(f"我是 {bot_id} 的人设", script.seen[0].instructions)

        self.assertEqual(sorted(self.sessions.sessions), ["wxid_m8s:群A", "wxid_ser:群A"])
        self.assertEqual(len(self.history("wxid_m8s:群A")), 2)


class LinkTests(AgentTestCase):
    async def test_link_content_is_read_without_blocking_and_given_to_the_model(self):
        with patch.object(agent_loop.link_reader, "read", AsyncMock(return_value="文章正文")) as read:
            script = await self.drive("看完了", content="看看 https://mp.weixin.qq.com/s/abc")

        read.assert_awaited_once_with("https://mp.weixin.qq.com/s/abc")
        self.assertIn("[链接内容]\n文章正文", script.seen[0].last_user)
