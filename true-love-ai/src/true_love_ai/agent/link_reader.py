# -*- coding: utf-8 -*-
"""
Link Reader - 读链接的正文，交给模型

- 公众号文章（mp.weixin.qq.com）不用登录，直接请求网页，取 js_content 里的正文
- 其余链接交给 Firecrawl（platform_key.firecrawl_api_key），没配 key 就不读。
  小红书直接请求只有登录页，Firecrawl 读得到；微信里复制出来的有时是登录跳转地址，先还原成原笔记地址

读不到返回空串，调用方只用消息里原有的标题和链接。
请求都是异步的：一个链接读上几十秒，别的消息照常处理。
"""

import html
import logging
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from true_love_common.http.client import async_get, async_post_json

from true_love_ai.core.config import get_config

LOG = logging.getLogger("LinkReader")

MAX_CHARS = 3000
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


async def read(url: str) -> str:
    """链接的标题和正文，最多 MAX_CHARS 字；读不到返回空串"""
    if not url:
        return ""
    url = _unwrap_login(url)
    host = (urlparse(url).hostname or "").lower()
    try:
        if host == "mp.weixin.qq.com":
            text = await _read_wechat_article(url)
        else:
            text = await _read_with_firecrawl(url)
    except Exception as e:
        LOG.warning("读链接失败: url=%s err=%s", url, e)
        return ""
    if not text:
        LOG.warning("链接没读到正文: %s", url)
    return text[:MAX_CHARS]


def _unwrap_login(url: str) -> str:
    """小红书的登录跳转地址（xiaohongshu.com/login?redirectPath=原地址）还原成原地址"""
    parsed = urlparse(url)
    if (parsed.hostname or "").endswith("xiaohongshu.com") and parsed.path.rstrip("/") == "/login":
        target = parse_qs(parsed.query).get("redirectPath", [""])[0]
        if target.startswith("http"):
            return target
    return url


async def _read_wechat_article(url: str) -> str:
    result = await async_get(url, headers={"User-Agent": BROWSER_UA}, timeout=30, follow_redirects=True)
    if not result.ok:
        LOG.warning("公众号文章请求失败: url=%s status=%s err=%s", url, result.status_code, result.error)
        return ""
    soup = BeautifulSoup(result.text, "html.parser")
    body = soup.find(id="js_content")
    if body is None:
        # 被微信拦下时是"环境异常"验证页，没有正文
        return ""
    title = soup.find("meta", property="og:title")
    text = re.sub(r"\s+", " ", body.get_text(" ")).strip()
    if title and title.get("content"):
        text = f"{html.unescape(title['content'])}\n{text}"
    return text


async def _read_with_firecrawl(url: str) -> str:
    key = get_config().platform_key.firecrawl_api_key
    if not key:
        LOG.info("没配 Firecrawl key，不读链接: %s", url)
        return ""
    result = await async_post_json(FIRECRAWL_URL, {"url": url, "formats": ["markdown"], "onlyMainContent": True},
                                   headers={"Authorization": f"Bearer {key}"}, timeout=60)
    data = result.data if isinstance(result.data, dict) else {}
    if not result.ok or not data.get("success"):
        LOG.warning("Firecrawl 读取失败: url=%s status=%s err=%s", url, result.status_code,
                    result.error or data.get("error"))
        return ""
    markdown = (data.get("data") or {}).get("markdown") or ""
    # 去掉图片和链接地址，只留文字，省 token
    markdown = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    markdown = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()
