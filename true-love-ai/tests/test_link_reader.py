"""Links are read for the model: WeChat articles straight from the page, other sites through Firecrawl."""

import asyncio
import time
import types
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

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


class LinkReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_wechat_article_is_read_from_the_page_itself(self):
        with patch.object(link_reader, "async_get", new_callable=AsyncMock, return_value=page(ARTICLE)), \
                patch.object(link_reader, "async_post_json", new_callable=AsyncMock) as scrape:
            text = await link_reader.read("https://mp.weixin.qq.com/s/abc")

        self.assertEqual(text, "中国男足冲击决赛\n北京时间今天下午 中国队将对阵韩国队")
        scrape.assert_not_awaited()

    async def test_wechat_captcha_page_gives_nothing(self):
        with patch.object(link_reader, "async_get", new_callable=AsyncMock, return_value=page(CAPTCHA)), self.assertLogs("LinkReader", "WARNING"):
            self.assertEqual(await link_reader.read("https://mp.weixin.qq.com/s/abc"), "")

    async def test_other_links_go_through_firecrawl_without_images_or_link_targets(self):
        markdown = "# 派早报\n\n![封面](https://img/a.png)\n\n[iPod touch](https://x) 宣布停产\n\n\n\n完"
        with patch.object(link_reader, "get_config", return_value=config()), \
                patch.object(link_reader, "async_post_json", new_callable=AsyncMock,
                             return_value=firecrawl({"success": True, "data": {"markdown": markdown}})) as scrape:
            text = await link_reader.read("https://sspai.com/post/1")

        self.assertEqual(text, "# 派早报\n\niPod touch 宣布停产\n\n完")
        payload = scrape.call_args.args[1]
        self.assertEqual(payload["url"], "https://sspai.com/post/1")
        self.assertEqual(scrape.call_args.kwargs["headers"], {"Authorization": "Bearer fc-test"})

    async def test_firecrawl_failure_gives_nothing_instead_of_an_error_page(self):
        with patch.object(link_reader, "get_config", return_value=config()), \
                patch.object(link_reader, "async_post_json", new_callable=AsyncMock,
                             return_value=firecrawl({"success": False, "error": "quota"}, status=402)), \
                self.assertLogs("LinkReader", "WARNING"):
            self.assertEqual(await link_reader.read("https://sspai.com/post/1"), "")

    async def test_without_a_key_other_links_are_not_read(self):
        with patch.object(link_reader, "get_config", return_value=config(key="")), \
                patch.object(link_reader, "async_post_json", new_callable=AsyncMock) as scrape:
            self.assertEqual(await link_reader.read("https://sspai.com/post/1"), "")
        scrape.assert_not_awaited()

    async def test_xiaohongshu_login_redirect_is_read_at_the_note_it_points_to(self):
        note = ("https://www.xiaohongshu.com/discovery/item/6ab7?app_platform=android&xsec_token=CB="
                "&xsec_source=app_share")
        wrapped = ("https://www.xiaohongshu.com/login?redirectPath=https://www.xiaohongshu.com/discovery/item/6ab7"
                   "?app_platform=android%26xsec_token=CB=%26xsec_source=app_share&wechatWid=8f&wechatOrigin=menu")
        encoded = "https://www.xiaohongshu.com/login?redirectPath=" + quote(note, safe="")
        with patch.object(link_reader, "get_config", return_value=config()), \
                patch.object(link_reader, "async_post_json", new_callable=AsyncMock,
                             return_value=firecrawl({"success": True, "data": {"markdown": "笔记正文"}})) as scrape:
            for url in (wrapped, encoded, note):
                self.assertEqual(await link_reader.read(url), "笔记正文")

        self.assertEqual([c.args[1]["url"] for c in scrape.call_args_list], [note, note, note])

    async def test_long_text_is_cut(self):
        long_article = ARTICLE.replace("北京时间今天下午", "字" * 5000)
        with patch.object(link_reader, "async_get", new_callable=AsyncMock, return_value=page(long_article)):
            self.assertEqual(len(await link_reader.read("https://mp.weixin.qq.com/s/abc")), link_reader.MAX_CHARS)

    async def test_a_slow_link_does_not_hold_up_other_work(self):
        async def slow_page(url, **kwargs):
            await asyncio.sleep(0.3)
            return page(ARTICLE)

        ticks = []

        async def ticker():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        with patch.object(link_reader, "async_get", slow_page):
            start = time.monotonic()
            text, _ = await asyncio.gather(link_reader.read("https://mp.weixin.qq.com/s/abc"), ticker())

        self.assertTrue(text)
        # the ticker kept running while the page was loading
        self.assertLess(ticks[-1] - start, 0.25)


if __name__ == "__main__":
    unittest.main()
