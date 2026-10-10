#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 路由模块：通过 LiteLLM proxy 调模型，模型由 ModelRegistry 统一管理；agent 用 pydantic_ai，单次调用直接用 OpenAI SDK"""
import logging
from typing import Optional

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from true_love_ai.core.model_registry import get_model_registry

LOG = logging.getLogger("LLMRouter")

_openai_client: Optional[AsyncOpenAI] = None

# 只约束 agent 对话；共用 client 的生图请求可能更慢，不能套这个值
AGENT_LLM_TIMEOUT_SECONDS = 120
# 工具定义原样发给 LiteLLM：pydantic_ai 默认会按模型改写 JSON schema（给无参数的工具补上空的 properties 等），
# 技能 schema 是按改造前直接发给模型的样子写的
_AGENT_MODEL_PROFILE = {"json_schema_transformer": None}


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

    def agent_model(self) -> Model:
        """
        agent 用的模型：主力加备用（有配的话），主力报错或连不上时自动换备用再试

        走同一个 LiteLLM proxy；pydantic_ai 按模型名前缀（openai/、gemini/…）选对应的兼容处理。
        SDK 默认 600s 超时 + 2 次重试，LLM 卡住时用户要等半小时才收到兜底回复，这里收紧
        """
        client = get_openai_client().with_options(timeout=AGENT_LLM_TIMEOUT_SECONDS, max_retries=1)
        provider = LiteLLMProvider(openai_client=client)
        primary_name = self._model("chat")
        fallback_name = self._model("chat", "fallback")
        primary = OpenAIChatModel(primary_name, provider=provider, profile=_AGENT_MODEL_PROFILE)
        LOG.info("agent: model=%s fallback=%s", primary_name, fallback_name or "-")
        if not fallback_name or fallback_name == primary_name:
            return primary
        return FallbackModel(primary, OpenAIChatModel(fallback_name, provider=provider, profile=_AGENT_MODEL_PROFILE))

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
