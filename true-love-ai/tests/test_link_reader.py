"""Links are read for the model: WeChat articles straight from the page, other sites through Firecrawl."""

import types
import unittest
from unittest.mock import patch

from true_love_common.http.client import HttpResult

from true_love_ai.agent import link_reader

ARTICLE = """<html><head><meta property="og:title" content="中国男足冲击决赛"></head>
<body><div id="js_content"><p>北京时间今天下午</p><p>中国队将对阵韩国队</p></div></body></html>"""
CAPTCHA = "<html><body><h2>环境异常</h2><p>完成验证后即可继续访问</p></body></html>"


def page(text, status=200):
    return HttpResult(method="GET", url="", ok=status < 400, status_code=status, text=text)


def firecrawl(data, status=200):
    return HttpResult(method="POST", url="", ok=status < 400, status_code=status, data=data)


def config(key="fc-test"):
    return types.SimpleNamespace(platform_key=types.SimpleNamespace(firecrawl_api_key=key))


class LinkReaderTests(unittest.TestCase):
    def test_wechat_article_is_read_from_the_page_itself(self):
        with patch.object(link_reader, "get", return_value=page(ARTICLE)), \
                patch.object(link_reader, "post_json") as scrape:
            text = link_reader.read("https://mp.weixin.qq.com/s/abc")

        self.assertEqual(text, "中国男足冲击决赛\n北京时间今天下午 中国队将对阵韩国队")
        scrape.assert_not_called()

    def test_wechat_captcha_page_gives_nothing(self):
        with patch.object(link_reader, "get", return_value=page(CAPTCHA)), self.assertLogs("LinkReader", "WARNING"):
            self.assertEqual(link_reader.read("https://mp.weixin.qq.com/s/abc"), "")

    def test_other_links_go_through_firecrawl_without_images_or_link_targets(self):
        markdown = "# 派早报\n\n![封面](https://img/a.png)\n\n[iPod touch](https://x) 宣布停产\n\n\n\n完"
        with patch.object(link_reader, "get_config", return_value=config()), \
                patch.object(link_reader, "post_json",
                             return_value=firecrawl({"success": True, "data": {"markdown": markdown}})) as scrape:
            text = link_reader.read("https://sspai.com/post/1")

        self.assertEqual(text, "# 派早报\n\niPod touch 宣布停产\n\n完")
        payload = scrape.call_args.args[1]
        self.assertEqual(payload["url"], "https://sspai.com/post/1")
        self.assertEqual(scrape.call_args.kwargs["headers"], {"Authorization": "Bearer fc-test"})

    def test_firecrawl_failure_gives_nothing_instead_of_an_error_page(self):
        with patch.object(link_reader, "get_config", return_value=config()), \
                patch.object(link_reader, "post_json",
                             return_value=firecrawl({"success": False, "error": "quota"}, status=402)), \
                self.assertLogs("LinkReader", "WARNING"):
            self.assertEqual(link_reader.read("https://sspai.com/post/1"), "")

    def test_without_a_key_other_links_are_not_read(self):
        with patch.object(link_reader, "get_config", return_value=config(key="")), \
                patch.object(link_reader, "post_json") as scrape:
            self.assertEqual(link_reader.read("https://sspai.com/post/1"), "")
        scrape.assert_not_called()

    def test_xiaohongshu_needs_a_login_so_it_is_skipped(self):
        with patch.object(link_reader, "get") as fetch, patch.object(link_reader, "post_json") as scrape:
            for url in ("https://www.xiaohongshu.com/discovery/item/1", "http://xhslink.com/a/b"):
                self.assertEqual(link_reader.read(url), "")
        fetch.assert_not_called()
        scrape.assert_not_called()

    def test_long_text_is_cut(self):
        long_article = ARTICLE.replace("北京时间今天下午", "字" * 5000)
        with patch.object(link_reader, "get", return_value=page(long_article)):
            self.assertEqual(len(link_reader.read("https://mp.weixin.qq.com/s/abc")), link_reader.MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
