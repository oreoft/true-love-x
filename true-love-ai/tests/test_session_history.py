"""
Conversation history: what is stored, how it comes back for the model, and how long conversations get compressed
without losing anything.
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from ai_db import memory_db
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from true_love_ai.core import session as session_module
from true_love_ai.core.session import Session, to_model_messages, tool_steps_json
from true_love_ai.memory.session_repository import MSG, TOOLS, StoredRow, get_session_repo

SID = "bot_a:room"


def call(name="query_user_memory", call_id="c1", args=None):
    return ModelResponse(parts=[ToolCallPart(name, args or {}, tool_call_id=call_id)])


def result(content, name="query_user_memory", call_id="c1"):
    return ModelRequest(parts=[ToolReturnPart(name, content, tool_call_id=call_id)])


def kinds(messages):
    return [(type(m).__name__, [type(p).__name__ for p in m.parts]) for m in messages]


class ToolStepsTests(unittest.TestCase):
    def test_only_tool_calls_and_their_results_are_kept(self):
        steps = [
            ModelRequest(parts=[UserPromptPart("【当前时间】… 记住我")], instructions="人设"),
            call(),
            ModelRequest(parts=[result("记好了").parts[0]], instructions="人设"),
            ModelResponse(parts=[TextPart("好的")]),
        ]
        stored = ModelMessagesTypeAdapter.validate_json(tool_steps_json(steps))

        self.assertEqual(kinds(stored), [("ModelResponse", ["ToolCallPart"]), ("ModelRequest", ["ToolReturnPart"])])
        self.assertIsNone(stored[1].instructions)

    def test_a_call_without_a_result_is_not_stored(self):
        self.assertIsNone(tool_steps_json([call(), result("x", call_id="other")]))
        self.assertIsNone(tool_steps_json([ModelResponse(parts=[TextPart("只是说话")])]))

    def test_retries_for_bad_arguments_count_as_answers(self):
        steps = [call(call_id="c1"), ModelRequest(parts=[RetryPromptPart("Invalid JSON", tool_name="x", tool_call_id="c1")])]

        self.assertIsNotNone(tool_steps_json(steps))

    def test_long_results_are_cut(self):
        stored = ModelMessagesTypeAdapter.validate_json(tool_steps_json([call(), result("字" * 5000)]))

        content = stored[1].parts[0].content
        self.assertLessEqual(len(content), session_module.STORED_TOOL_RESULT_CHARS + 10)
        self.assertTrue(content.endswith("（已截断）"))


class HistoryRoundTripTests(unittest.TestCase):
    def test_rows_come_back_as_a_valid_conversation(self):
        rows = [
            StoredRow(1, MSG, "user", "张三：记住我"),
            StoredRow(2, TOOLS, None, tool_steps_json([call(), result("记好了")])),
            StoredRow(3, MSG, "assistant", "好的"),
            StoredRow(4, MSG, "user", "李四：在吗"),
        ]
        messages = to_model_messages(rows)

        self.assertEqual(kinds(messages), [
            ("ModelRequest", ["UserPromptPart"]),
            ("ModelResponse", ["ToolCallPart"]),
            ("ModelRequest", ["ToolReturnPart"]),
            ("ModelResponse", ["TextPart"]),
            ("ModelRequest", ["UserPromptPart"]),
        ])
        self.assertEqual(messages[0].parts[0].content, "张三：记住我")

    def test_old_conversations_without_tool_rows_still_load(self):
        messages = to_model_messages([StoredRow(1, MSG, "user", "老消息"), StoredRow(2, MSG, "assistant", "老回复")])

        self.assertEqual(kinds(messages), [("ModelRequest", ["UserPromptPart"]), ("ModelResponse", ["TextPart"])])

    def test_a_broken_tool_row_is_skipped(self):
        with self.assertLogs("Session", "WARNING"):
            messages = to_model_messages([StoredRow(1, MSG, "user", "a"), StoredRow(2, TOOLS, None, "{not json"),
                                          StoredRow(3, MSG, "assistant", "b")])

        self.assertEqual(len(messages), 2)


class TurnStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)

    async def test_a_turn_is_stored_user_tools_reply_and_read_back_for_the_next_message(self):
        session = Session(SID)
        self.assertEqual(session.history(), (None, []))
        session.record_turn("Alice：记住我", "好的", [ModelRequest(parts=[UserPromptPart("live")]), call(),
                                                   result("记好了"), ModelResponse(parts=[TextPart("好的")])])

        _, history = session.history()

        self.assertEqual(kinds(history), [
            ("ModelRequest", ["UserPromptPart"]),
            ("ModelResponse", ["ToolCallPart"]),
            ("ModelRequest", ["ToolReturnPart"]),
            ("ModelResponse", ["TextPart"]),
        ])
        self.assertEqual(history[0].parts[0].content, "Alice：记住我")
        self.assertEqual([(r.type, r.role) for r in get_session_repo().load(SID)[1]],
                         [(MSG, "user"), (TOOLS, None), (MSG, "assistant")])

    async def test_a_reply_without_tools_is_just_the_text(self):
        Session(SID).record_turn("Alice：hi", "你好")

        self.assertEqual([(r.type, r.content) for r in get_session_repo().load(SID)[1]],
                         [(MSG, "Alice：hi"), (MSG, "你好")])

    async def test_an_unanswered_turn_keeps_only_what_the_user_said(self):
        Session(SID).record_turn("Alice：[图片]", None, [call(), result("健身照")])

        self.assertEqual([(r.type, r.role) for r in get_session_repo().load(SID)[1]], [(MSG, "user")])

    async def test_two_turns_handled_at_the_same_time_do_not_interleave(self):
        a, b = Session(SID), Session(SID)
        _, history_a = a.history()
        _, history_b = b.history()
        b.record_turn("Bob：在吗", "在", [call(call_id="b1"), result("x", call_id="b1")])
        a.record_turn("Alice：记住我", "好的", [call(call_id="a1"), result("y", call_id="a1")])

        rows = get_session_repo().load(SID)[1]
        self.assertEqual([(r.type, r.role, r.content) for r in rows if r.type == MSG],
                         [(MSG, "user", "Bob：在吗"), (MSG, "assistant", "在"),
                          (MSG, "user", "Alice：记住我"), (MSG, "assistant", "好的")])
        self.assertEqual([r.type for r in rows], [MSG, TOOLS, MSG, MSG, TOOLS, MSG])


class ToolFreeHistoryTests(unittest.TestCase):
    def test_tool_steps_are_dropped_for_someone_with_no_skills(self):
        history = [ModelRequest(parts=[UserPromptPart("Alice：记住我")]), call(), result("记好了"),
                   ModelResponse(parts=[TextPart("好的")])]

        self.assertEqual(kinds(session_module.without_tool_steps(history)),
                         [("ModelRequest", ["UserPromptPart"]), ("ModelResponse", ["TextPart"])])


class CompressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_db(self)
        self.repo = get_session_repo()

    def fill(self, turns: int, start: int = 0):
        for i in range(start, start + turns):
            steps = tool_steps_json([call(call_id=f"c{i}"), result(f"技能{i}", call_id=f"c{i}")]) if i % 2 else None
            self.repo.append_turn(SID, f"问{i}", steps, f"答{i}")

    async def wait_compressed(self, session):
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not session._compressing:
                return

    async def test_old_turns_are_summarized_and_recent_ones_kept_whole(self):
        self.fill(6)
        compress_fn = AsyncMock(return_value="【摘要】")
        session = Session(SID, compress_threshold=10, compress_keep_recent=4, compress_fn=compress_fn)

        session.record_turn("问6")
        await self.wait_compressed(session)

        summary, rows = self.repo.load(SID)
        self.assertEqual(summary, "【摘要】")
        # the kept part starts at a user message, tool rows stay with their turn
        self.assertEqual(rows[0].content, "问5")
        self.assertEqual([r.content for r in rows if r.type == MSG], ["问5", "答5", "问6"])
        prompt = compress_fn.await_args.args[0][0]["content"]
        for i in range(5):
            self.assertIn(f"问{i}", prompt)
        self.assertIn("调用技能 query_user_memory", prompt)
        self.assertIn("技能 query_user_memory 返回: 技能1", prompt)
        self.assertNotIn("问5", prompt)

    async def test_messages_that_arrive_while_compressing_are_not_lost(self):
        self.fill(6)
        release = asyncio.Event()

        async def slow_summary(messages):
            await release.wait()
            return "【摘要】"

        session = Session(SID, compress_threshold=10, compress_keep_recent=4, compress_fn=slow_summary)
        session.record_turn("问6")
        await asyncio.sleep(0.01)
        # the group keeps talking while the summary is being written
        for i in range(7, 12):
            self.repo.append_message(SID, "user", f"问{i}")
        release.set()
        await self.wait_compressed(session)

        _, rows = self.repo.load(SID)
        kept = [r.content for r in rows if r.type == MSG]
        self.assertEqual(kept, ["问5", "答5", "问6", "问7", "问8", "问9", "问10", "问11"])

    async def test_failed_compression_is_not_retried_on_every_message(self):
        self.fill(6)
        compress_fn = AsyncMock(side_effect=RuntimeError("model down"))
        session = Session(SID, compress_threshold=10, compress_keep_recent=4, compress_fn=compress_fn)

        with self.assertLogs("Session", level="ERROR"):
            session.record_turn("问6")
            await self.wait_compressed(session)
            session.record_turn("问7", "答7")
            await self.wait_compressed(session)
        self.assertEqual(compress_fn.await_count, 1)
        self.assertIsNone(self.repo.load(SID)[0])

        # 冷却过了再试，成功后正常写库
        session._compress_retry_at = datetime.now() - timedelta(seconds=1)
        compress_fn.side_effect = None
        compress_fn.return_value = "摘要"
        session.record_turn("问8")
        await self.wait_compressed(session)

        self.assertEqual(self.repo.load(SID)[0], "摘要")
        self.assertIsNone(session._compress_retry_at)

    async def test_an_empty_summary_is_not_saved(self):
        self.fill(6)
        session = Session(SID, compress_threshold=10, compress_keep_recent=4, compress_fn=AsyncMock(return_value=" "))

        with self.assertLogs("Session", level="ERROR"):
            session.record_turn("问6")
            await self.wait_compressed(session)

        summary, rows = self.repo.load(SID)
        self.assertIsNone(summary)
        self.assertEqual(len([r for r in rows if r.type == MSG]), 13)

    async def test_short_conversations_are_left_alone(self):
        self.fill(2)
        compress_fn = AsyncMock(return_value="【摘要】")
        session = Session(SID, compress_threshold=10, compress_keep_recent=4, compress_fn=compress_fn)
        session.record_turn("问2")
        await self.wait_compressed(session)

        compress_fn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
