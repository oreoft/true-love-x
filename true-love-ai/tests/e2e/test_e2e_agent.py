"""
End to end through real tl-server and tl-ai processes: a message comes in from the (fake) base, the reply has to
come back out to the base. The model is scripted per test (see fakes.FakeLLM).
"""

import json
import time
from pathlib import Path

import pytest

from .fakes import LLMRequest, call, calls, scripted, text
from .stack import BOT_ID

pytestmark = pytest.mark.e2e


def reply_with_tool_result(req: LLMRequest) -> dict:
    """Answer with whatever the last tool said"""
    return text("技能说：" + " | ".join(req.tool_results))


def first_call_then(answer_after, *tool_calls):
    """Call these tools on the first request of the turn, then hand over to answer_after"""
    def brain(req: LLMRequest) -> dict:
        if not req.tool_results:
            return calls(*tool_calls)
        return answer_after(req)
    return brain


# ==================== plain replies and routing ====================

def test_private_message_gets_the_model_reply(e2e):
    e2e.llm.brain = lambda req: text(f"你好呀，你说的是：{req.last_user[-10:]}")
    e2e.say("今天天气不错", chat="", sender="bob_private", group=False)

    sent = e2e.base.wait_texts("bob_private")
    assert sent[0]["content"].startswith("你好呀，你说的是：")
    assert "今天天气不错" in sent[0]["content"]
    assert sent[0]["atReceiver"] == ""


def test_group_mention_is_answered_in_the_group_replying_to_the_message(e2e, chat):
    e2e.llm.brain = lambda req: text("群里好")
    e2e.say("在吗", chat=chat, sender="alice", msg_id="msg-1")

    sent = e2e.base.wait_texts(chat)
    assert sent == [{"kind": "text", "sendReceiver": chat, "atReceiver": "alice", "content": "群里好",
                     "replyMsgId": "msg-1"}]
    # the mention text is not passed to the model
    assert "@小助手" not in e2e.llm.agent_requests()[0].last_user


def test_the_bot_answers_with_its_persona(e2e, chat):
    e2e.ai_admin("/admin/persona/save", bot_id=BOT_ID, chat=chat, prompt="你是{name}，说话结尾带喵。")
    e2e.llm.brain = lambda req: text("喵")
    e2e.say("你是谁", chat=chat)
    e2e.base.wait_texts(chat)

    system = e2e.llm.agent_requests()[0].system
    assert "你是小助手，说话结尾带喵。" in system


def test_voice_message_is_passed_as_text(e2e, chat):
    e2e.llm.brain = lambda req: text("听到了")
    e2e.say("", chat=chat, msg_type="voice", voice_msg={"text_content": "帮我查下天气"})
    e2e.base.wait_texts(chat)

    assert "帮我查下天气" in e2e.llm.agent_requests()[0].last_user


def test_quoted_message_is_included(e2e, chat):
    e2e.llm.brain = lambda req: text("懂了")
    e2e.say("这个靠谱吗", chat=chat, msg_type="refer",
            refer_msg={"msg_type": "text", "content": "明天全市停水", "sender_id": "carol"})
    e2e.base.wait_texts(chat)

    prompt = e2e.llm.agent_requests()[0].last_user
    assert "这个靠谱吗" in prompt and "明天全市停水" in prompt


# ==================== history ====================

def test_the_next_message_sees_the_previous_exchange(e2e, chat):
    e2e.llm.brain = scripted(text("记住了，你喜欢蓝色"), text("你喜欢蓝色"))
    e2e.say("我喜欢蓝色", chat=chat)
    e2e.base.wait_texts(chat)
    e2e.say("我喜欢什么颜色", chat=chat)
    e2e.base.wait_texts(chat, 2)

    second = e2e.llm.agent_requests()[-1].all_text()
    assert "我喜欢蓝色" in second
    assert "记住了，你喜欢蓝色" in second


def test_group_history_says_who_said_what(e2e, chat):
    e2e.llm.brain = lambda req: text("ok")
    e2e.say("我是张三", chat=chat, sender="zhang", sender_name="张三")
    e2e.base.wait_texts(chat)
    e2e.say("我是李四", chat=chat, sender="li", sender_name="李四")
    e2e.base.wait_texts(chat, 2)

    history = e2e.llm.agent_requests()[-1].all_text()
    assert "张三：我是张三" in history
    assert "李四：我是李四" in history


def test_tool_calls_stay_in_the_history(e2e, chat):
    """The next turn knows which skill ran last time and what it returned"""
    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("save_user_profile", {"key": "interest.music", "value": "爵士乐"}))
    e2e.say("记住我喜欢爵士乐", chat=chat)
    e2e.base.wait_texts(chat)

    e2e.llm.reset()
    e2e.llm.brain = lambda req: text("好")
    e2e.say("你刚才做了什么", chat=chat)
    e2e.base.wait_texts(chat, 2)

    messages = e2e.llm.agent_requests()[0].messages
    assert any(m.get("role") == "assistant" and m.get("tool_calls")
               and m["tool_calls"][0]["function"]["name"] == "save_user_profile" for m in messages)
    assert any(m.get("role") == "tool" and "爵士乐" in str(m.get("content")) for m in messages)


# ==================== skills ====================

def test_profile_is_saved_and_shown_to_the_model_next_time(e2e, chat):
    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("save_user_profile", {"key": "occupation", "value": "程序员"}))
    e2e.say("记住我是程序员", chat=chat, sender="dave")
    sent = e2e.base.wait_texts(chat)
    assert "occupation = 程序员" in sent[0]["content"]

    e2e.llm.reset()
    e2e.llm.brain = lambda req: text("你是程序员")
    e2e.say("我是做什么的", chat=chat, sender="dave")
    e2e.base.wait_texts(chat, 2)
    assert "职业：程序员" in e2e.llm.agent_requests()[0].all_text()

    memory = e2e.ai_admin("/admin/memory/get", bot_id=BOT_ID, chat=chat, sender="dave")
    assert [f["value"] for f in memory["data"]["facts"]] == ["程序员"]


def test_two_skills_called_at_once_both_run(e2e, chat):
    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("save_user_profile", {"key": "interest.food", "value": "火锅"}),
                                    call("query_user_memory", {}))
    e2e.say("记住我爱吃火锅，再看看你记了啥", chat=chat, sender="erin")
    sent = e2e.base.wait_texts(chat)

    assert "interest.food = 火锅" in sent[0]["content"]
    assert len(e2e.llm.agent_requests()[-1].tool_results) == 2


def test_image_is_analyzed_with_a_notice_first(e2e, chat):
    e2e.base.media["wx_imgs/cat.jpg"] = (b"\xff\xd8\xff fake jpeg", "image/jpeg")

    def brain(req: LLMRequest) -> dict:
        if req.tool_results:
            return text("这是" + req.tool_results[0])
        path = req.last_user.split("[图片:")[1].split("]")[0]
        return calls(call("analyze_image", {"question": "图上是什么", "image_path": path}))

    e2e.llm.brain = brain
    e2e.say("图片", chat=chat, msg_type="image", image_msg={"resource": {"ref": "wx_imgs/cat.jpg"}})
    sent = e2e.base.wait_texts(chat, 2)

    notice, answer = sent[0]["content"], sent[1]["content"]
    assert any(word in notice for word in ("看看", "观察", "分析"))
    assert answer == "这是图上是一只橘猫"
    vision = [r for r in e2e.llm.requests if r.model == e2e.llm.VISION_MODEL]
    assert vision and vision[0].has_image


def test_group_context_comes_from_the_server_history(e2e, chat):
    # someone talks in the group without mentioning the bot: stored by server only
    e2e.say("周六下午三点在老地方集合", chat=chat, sender="frank", at_me=False)
    time.sleep(0.5)
    e2e.llm.brain = first_call_then(reply_with_tool_result, call("fetch_group_context", {"limit": 10}))
    e2e.say("他刚才说几点集合", chat=chat, sender="gina")
    sent = e2e.base.wait_texts(chat)

    assert "周六下午三点在老地方集合" in sent[0]["content"]


def test_reminder_is_created_on_the_server(e2e, chat):
    future = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime(time.time() + 3 * 86400))
    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("set_reminder", {"target_time_iso": future, "content": "交房租"}))
    e2e.say("三天后提醒我交房租", chat=chat)
    sent = e2e.base.wait_texts(chat)
    assert "设置提醒成功" in sent[0]["content"]

    reminders = e2e.ai_server_action("/action/reminder/query", receiver=chat)
    assert any("交房租" in json.dumps(r, ensure_ascii=False) for r in reminders["data"]["jobs"])


def test_dynamic_skill_runs_with_its_parameter(e2e, chat):
    e2e.llm.brain = first_call_then(reply_with_tool_result, call("skill_save", {
        "id": "say_hello", "name": "打招呼", "description": "跟某人打招呼",
        "command": "echo hello-{who}", "parameters": {"who": {"default": "world", "desc": "名字"}}}))
    e2e.say("把打招呼存成技能", chat=chat)
    assert "已保存" in e2e.base.wait_texts(chat)[0]["content"]

    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("skill_run", {"id": "say_hello", "params": {"who": "bob"}}))
    e2e.say("跟 bob 打个招呼", chat=chat)
    # skill_run sends a "正在执行" notice first
    sent = e2e.base.wait_texts(chat, 3)
    assert "执行" in sent[1]["content"]
    assert sent[2]["content"] == "技能说：hello-bob"


@pytest.mark.parametrize("payload", ["x\ntouch {marker}", "x' ; touch {marker} ; echo '", "$(touch {marker})",
                                     "x`touch {marker}`"])
def test_dynamic_skill_parameters_cannot_run_commands(e2e, chat, tmp_path, payload):
    marker = tmp_path / "pwned"
    skill_id = f"echo_{abs(hash(payload)) % 100000}"
    e2e.llm.brain = first_call_then(reply_with_tool_result, call("skill_save", {
        "id": skill_id, "name": "回声", "description": "回声", "command": "echo got-{word}",
        "parameters": {"word": {"default": "a", "desc": "词"}}}))
    e2e.say("存个回声技能", chat=chat)
    e2e.base.wait_texts(chat)

    e2e.llm.brain = first_call_then(reply_with_tool_result,
                                    call("skill_run", {"id": skill_id, "params": {"word": payload.format(marker=marker)}}))
    e2e.say("回声一下", chat=chat)
    reply = e2e.base.wait_texts(chat, 3)[2]["content"]

    assert not marker.exists(), "a skill parameter ran a shell command"
    assert reply.startswith("技能说：")


# ==================== failures and fallbacks ====================

def test_a_failing_skill_still_gets_a_reply(e2e, chat):
    e2e.base.media.pop("wx_imgs/missing.jpg", None)
    e2e.llm.brain = first_call_then(lambda req: text("图片打不开，换一张试试"),
                                    call("analyze_image", {"question": "?", "image_path": f"{e2e.base.url}/media/wx_imgs/missing.jpg"}))
    e2e.say("看看这张", chat=chat)

    sent = e2e.base.wait_texts(chat, 2)
    assert sent[-1]["content"] == "图片打不开，换一张试试"
    e2e.wait_log("ai", "outcome=tool_failed")


def test_broken_tool_arguments_go_back_to_the_model(e2e, chat):
    e2e.llm.brain = scripted(
        calls(call("save_user_profile", '{"key": "interest.x", "value": ')),
        calls(call("save_user_profile", {"key": "interest.game", "value": "围棋"})),
        text("记好了"),
    )
    e2e.say("记住我喜欢围棋", chat=chat, sender="hank")
    assert e2e.base.wait_texts(chat)[0]["content"] == "记好了"

    memory = e2e.ai_admin("/admin/memory/get", bot_id=BOT_ID, chat=chat, sender="hank")
    assert [f["value"] for f in memory["data"]["facts"]] == ["围棋"]


def test_llm_down_sends_the_fallback_text(e2e, chat):
    e2e.llm.fail_next = 100
    e2e.say("在吗", chat=chat)

    sent = e2e.base.wait_texts(chat, timeout=60)
    assert sent[0]["content"] == "呜呜~出了点小状况，稍后再试试吧~"


def test_primary_model_down_falls_back_to_the_fallback_model(e2e, chat):
    def brain(req: LLMRequest) -> dict:
        if req.model == "e2e/chat":
            raise RuntimeError("primary down")
        return text(f"备用模型 {req.model} 回答")

    e2e.llm.brain = brain
    e2e.say("在吗", chat=chat)
    assert e2e.base.wait_texts(chat, timeout=60)[0]["content"] == "备用模型 e2e/chat-fallback 回答"


def test_empty_model_reply_sends_the_skill_result(e2e, chat):
    e2e.llm.brain = first_call_then(lambda req: text(""),
                                    call("save_user_profile", {"key": "habit.sleep", "value": "早睡"}))
    e2e.say("记住我早睡", chat=chat)
    assert "habit.sleep = 早睡" in e2e.base.wait_texts(chat)[0]["content"]


def test_a_model_that_keeps_calling_tools_still_answers(e2e, chat):
    def brain(req: LLMRequest) -> dict:
        if req.tools:
            return calls(call("query_user_memory", {}))
        return text("查了好几遍，就这些")

    e2e.llm.brain = brain
    e2e.say("你记得我什么", chat=chat)
    assert e2e.base.wait_texts(chat, timeout=60)[0]["content"] == "查了好几遍，就这些"


# ==================== auto replies (group, nobody mentioned the bot) ====================

def _enable_auto_image(e2e):
    import httpx
    from true_love_common.hosts import DEV_SERVER_HOST
    r = httpx.post(f"{DEV_SERVER_HOST}/admin/bots/{BOT_ID}/listen/settings",
                   json={"auto_ai_image": True, "auto_ai_image_limit": {"count": 100, "seconds": 60}}, timeout=10)
    r.raise_for_status()


def test_auto_reply_with_nothing_to_say_stays_quiet(e2e, chat):
    _enable_auto_image(e2e)
    e2e.base.media["wx_imgs/gym.jpg"] = (b"\xff\xd8\xff gym", "image/jpeg")
    e2e.llm.brain = lambda req: text("[不回复]")
    e2e.say("图片", chat=chat, at_me=False, msg_type="image", image_msg={"resource": {"ref": "wx_imgs/gym.jpg"}})

    e2e.wait_log("ai", "outcome=skipped")
    assert e2e.base.texts(chat) == []
    assert "[不回复]" in e2e.llm.agent_requests()[0].all_text()


def test_auto_reply_answer_after_a_retried_skill_is_sent(e2e, chat):
    """A skill failed once, the model retried it and then had something good to say: that goes out"""
    _enable_auto_image(e2e)
    e2e.base.media["wx_imgs/meme.jpg"] = (b"\xff\xd8\xff meme", "image/jpeg")
    url = f"{e2e.base.url}/media/wx_imgs/meme.jpg"
    e2e.llm.brain = scripted(
        calls(call("analyze_image", {"question": "?", "image_path": url + ".missing"})),
        calls(call("analyze_image", {"question": "?", "image_path": url})),
        text("哈哈这图绝了"),
    )
    e2e.say("图片", chat=chat, at_me=False, msg_type="image", image_msg={"resource": {"ref": "wx_imgs/meme.jpg"}})

    sent = e2e.base.wait_texts(chat)
    # no "正在看图" notice when nobody asked
    assert [s["content"] for s in sent] == ["哈哈这图绝了"]


# ==================== long conversations ====================

def test_long_conversation_is_compressed_without_losing_messages(e2e, chat):
    e2e.llm.brain = lambda req: text("嗯")
    for i in range(10):
        e2e.say(f"第{i}句", chat=chat)
        e2e.base.wait_texts(chat, i + 1)
        time.sleep(0.05)

    deadline = time.time() + 15
    while time.time() < deadline and not [r for r in e2e.llm.requests if r.model == e2e.llm.COMPRESS_MODEL]:
        time.sleep(0.1)
    compress = [r for r in e2e.llm.requests if r.model == e2e.llm.COMPRESS_MODEL]
    assert compress, "the conversation was never compressed"

    e2e.say("最后一句", chat=chat)
    e2e.base.wait_texts(chat, 11)
    last = e2e.llm.agent_requests()[-1].all_text()
    assert "【摘要】之前聊过天" in last
    # every message is either in the summary input or still in the history
    summarized = "\n".join(r.all_text() for r in compress)
    for i in range(10):
        assert f"第{i}句" in summarized or f"第{i}句" in last, f"第{i}句 was lost"
