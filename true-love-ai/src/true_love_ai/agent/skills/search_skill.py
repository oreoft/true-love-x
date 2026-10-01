# -*- coding: utf-8 -*-
"""搜索增强 Skill（通过百度搜索获取实时信息）"""
import asyncio
import json
import logging
import subprocess
from typing import Optional
from urllib.parse import quote_plus

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("SearchSkill")

# 百度搜索 CURL 命令模板
BAIDU_SEARCH_CURL = (
    "curl --location 'https://www.baidu.com/s?wd=%s&tn=json' "
    "--header 'User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'"
)


@register_skill({
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
                "联网搜索实时信息，适合查询新闻、实时数据、近期事件等。"
                "当用户需要搜索最新资讯、当前股价、天气预报等实时信息时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "完整、具体的搜索关键词（中文）"
                }
            },
            "required": ["query"]
        }
    }
})
async def web_search(params: dict, ctx: dict) -> str:
    query = params.get("query", "")
    if not query:
        return "诶嘿~请提供搜索关键词哦~"

    # curl 是阻塞调用，放到线程里跑，不卡事件循环
    results = await asyncio.to_thread(fetch_baidu_references, query)
    if results is None:
        raise SkillFailed("呜呜~搜索失败了捏，稍后再试试吧~")
    if not results:
        return f"搜索「{query}」没有找到相关结果"
    return f"搜索「{query}」的结果：\n{json.dumps(results[:5], ensure_ascii=False, indent=2)}"


def fetch_baidu_references(keyword: str) -> Optional[list[dict]]:
    """
    通过百度搜索获取参考信息

    使用 curl 命令避免被风控

    Args:
        keyword: 搜索关键词

    Returns:
        参考信息列表，每项包含 content 和 source_url；搜索本身失败（超时、curl 报错、响应解析不了）返回 None
    """
    send_curl = BAIDU_SEARCH_CURL % quote_plus(keyword)
    LOG.info("百度搜索: %s", keyword)
    try:
        baidu_response = subprocess.run(
            send_curl,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        LOG.warning("百度搜索超时: keyword=%s", keyword)
        return None

    if baidu_response.returncode != 0:
        LOG.warning("百度搜索 curl 失败: keyword=%s returncode=%s stderr=%s",
                    keyword, baidu_response.returncode, (baidu_response.stderr or "")[:200])
        return None

    try:
        data = json.loads(baidu_response.stdout)
        reference_list = [
            {"content": entry['abs'], "source_url": entry['url']}
            for entry in data['feed']['entry']
            if 'abs' in entry and 'url' in entry
        ]
    except (ValueError, KeyError, TypeError) as e:
        LOG.warning("百度搜索响应解析失败: keyword=%s err=%r body=%s", keyword, e, baidu_response.stdout[:200])
        return None

    LOG.info("百度搜索结果数量: %d", len(reference_list))
    return reference_list
