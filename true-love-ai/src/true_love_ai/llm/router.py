#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 路由模块：通过 OpenAI SDK 调用 LiteLLM proxy，模型由 ModelRegistry 统一管理"""
import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from true_love_ai.core.model_registry import get_model_registry

LOG = logging.getLogger("LLMRouter")

_openai_client: Optional[AsyncOpenAI] = None

# 只约束 agent 对话；共用 client 的生图请求可能更慢，不能套这个值
AGENT_LLM_TIMEOUT_SECONDS = 120


def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        from true_love_ai.core.config import get_config
        cfg = get_config()
        _openai_client = AsyncOpenAI(
            base_url=cfg.platform_key.litellm_base_url.rstrip("/"),
            api_key=cfg.platform_key.litellm_api_key,
        )
    return _openai_client


def _parse_tool_call(tc) -> dict:
    """
    把模型的 tool call 转成 {id, name, arguments}

    参数不是合法的 JSON 对象时不拿空参数去执行，带上 arguments_error，AgentLoop 把它当技能结果回给模型让它重来。
    """
    raw = tc.function.arguments or ""
    call = {"id": tc.id, "name": tc.function.name, "arguments": {}}
    if not raw.strip():
        return call
    try:
        args = json.loads(raw)
    except ValueError as e:
        error = f"参数不是合法的 JSON: {e}"
    else:
        if isinstance(args, dict):
            call["arguments"] = args
            return call
        error = f"参数应该是 JSON 对象，收到的是 {type(args).__name__}"
    LOG.warning("tool %s 的参数解析失败: %s arguments=%s", tc.function.name, error, raw[:200])
    call["arguments_error"] = error
    return call


class LLMRouter:

    def _model(self, category: str, key: str = "default") -> str:
        return get_model_registry().get(category, key)

    async def chat(
            self,
            messages: list[dict],
            model: Optional[str] = None,
            **kwargs,
    ) -> str:
        resolved = model or self._model("chat")
        LOG.info("chat: model=%s msgs=%d", resolved, len(messages))
        client = get_openai_client()
        response = await client.chat.completions.create(model=resolved, messages=messages, **kwargs)
        return response.choices[0].message.content

    async def chat_for_agent(
            self,
            messages: list[dict],
            tools: list[dict],
            model: Optional[str] = None,
    ) -> tuple[str, list | None]:
        resolved = model or self._model("chat")
        LOG.info("agent: model=%s tools=%d msgs=%d", resolved, len(tools), len(messages))
        # SDK 默认 600s 超时 + 2 次重试，LLM 卡住时用户要等半小时才收到兜底回复
        client = get_openai_client().with_options(timeout=AGENT_LLM_TIMEOUT_SECONDS, max_retries=1)
        response = await client.chat.completions.create(
            model=resolved,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )
        message = response.choices[0].message

        if message.tool_calls:
            return "tool_calls", [_parse_tool_call(tc) for tc in message.tool_calls]

        return "text", message.content or ""

    async def vision(
            self,
            prompt: str,
            image_data: str,
            model: Optional[str] = None,
            mime_type: str = "image/jpeg",
            **kwargs,
    ) -> str:
        resolved = model or self._model("vision")
        LOG.info("vision: model=%s mime=%s", resolved, mime_type)
        client = get_openai_client()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
            ],
        }]
        response = await client.chat.completions.create(model=resolved, messages=messages, **kwargs)
        return response.choices[0].message.content

    async def document(
            self,
            prompt: str,
            file_data: str,
            mime_type: str = "application/pdf",
            model: Optional[str] = None,
            **kwargs,
    ) -> str:
        """分析文档（PDF 等），使用 LiteLLM file content type"""
        resolved = model or self._model("vision")
        LOG.info("document: model=%s mime=%s", resolved, mime_type)
        client = get_openai_client()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "file", "file": {"file_data": f"data:{mime_type};base64,{file_data}"}},
            ],
        }]
        response = await client.chat.completions.create(model=resolved, messages=messages, **kwargs)
        return response.choices[0].message.content

    async def compress(self, messages: list[dict]) -> str:
        resolved = self._model("compress")
        LOG.info("compress: model=%s", resolved)
        client = get_openai_client()
        response = await client.chat.completions.create(model=resolved, messages=messages)
        return response.choices[0].message.content


_llm_router: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    global _llm_router
    if _llm_router is None:
        _llm_router = LLMRouter()
    return _llm_router
