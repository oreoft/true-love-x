"""Images and files carry a request for the model; the user's own words win when the platform lets them write any."""

import unittest

from true_love_common.chat_msg import ChatMsg, FileMsg, ImageMsg, ResourceRef

from true_love_ai.agent import agent_loop


def build(**fields):
    loop = agent_loop.AgentLoop.__new__(agent_loop.AgentLoop)
    return loop._build_user_content(ChatMsg(**fields))


def image(content):
    return build(msg_type="image", content=content, image_msg=ImageMsg(resource=ResourceRef(ref="http://b/a.jpg")))


def file(content):
    return build(msg_type="file", content=content,
                 file_msg=FileMsg(file_name="a.pdf", resource=ResourceRef(ref="http://b/a.pdf")))


class MediaRequestTests(unittest.TestCase):
    def test_image_with_only_a_placeholder_asks_the_model_to_look(self):
        for content in ("图片", "", '{"image_key":"img_v3_x"}'):
            with self.subTest(content=content):
                self.assertEqual(image(content), f"[图片:http://b/a.jpg] {agent_loop.IMAGE_REQUEST}")

    def test_image_with_the_users_words_keeps_them(self):
        self.assertEqual(image("帮我图片内容变成英文的"), "[图片:http://b/a.jpg] 帮我图片内容变成英文的")

    def test_file_card_text_is_replaced_by_the_request(self):
        for content in ("文件\n华为韬定律.pdf\n1.3M\n微信电脑版", ""):
            with self.subTest(content=content):
                self.assertEqual(file(content), f"[文件:http://b/a.pdf] {agent_loop.FILE_REQUEST}")

    def test_file_with_the_users_words_keeps_them(self):
        self.assertEqual(file("总结一下第三章"), "[文件:http://b/a.pdf] 总结一下第三章")

    def test_quoted_image_keeps_the_text_the_user_typed(self):
        quoted = ChatMsg(msg_type="image", image_msg=ImageMsg(resource=ResourceRef(ref="http://b/q.jpg")))
        text = build(msg_type="refer", content="这张图里写的啥", refer_msg=quoted)

        self.assertEqual(text, "这张图里写的啥\n\n[引用图片:http://b/q.jpg]")


if __name__ == "__main__":
    unittest.main()
