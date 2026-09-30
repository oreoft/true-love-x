#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""图像服务：文生图（OpenAI SDK → LiteLLM proxy）+ 图像分析"""
import logging
from pathlib import Path
from typing import Optional

from true_love_common.http.client import async_get
from true_love_ai.core.model_registry import get_model_registry
from true_love_ai.llm.router import get_llm_router, get_openai_client
from true_love_ai.models.response import ImageResponse

GEN_IMG_DIR = Path("gen_img")
GEN_IMG_DIR.mkdir(exist_ok=True)

LOG = logging.getLogger(__name__)


# 识图时加在用户问题前面的提示
VISION_PROMPT = "请根据需要分析图片"


class ImageService:

    def __init__(self):
        self.llm_router = get_llm_router()
        self.registry = get_model_registry()

    async def generate_image(
            self,
            image_prompt: str,
            provider: Optional[str] = None,
    ) -> ImageResponse:
        default_model  = self.registry.get("image", "default")
        fallback_model = self.registry.get("image", "fallback")

        if provider:
            model = default_model if provider in default_model else fallback_model
            return await self._generate(image_prompt, model)

        try:
            return await self._generate(image_prompt, default_model)
        except Exception as e:
            if fallback_model:
                LOG.warning("主力生图失败，降级备用模型 %s: %s", fallback_model, e)
                return await self._generate(image_prompt, fallback_model)
            raise

    async def _generate(self, prompt: str, model: str) -> ImageResponse:
        import base64
        LOG.info("生图: model=%s, prompt=%s...", model, prompt[:50])
        try:
            response = await get_openai_client().images.generate(
                model=model, prompt=prompt, n=1,
            )
            item = response.data[0] if response.data else None
            if item and item.b64_json:
                return ImageResponse(prompt=prompt, img=item.b64_json)
            if item and item.url:
                r = await async_get(item.url, timeout=60.0)
                if r.ok:
                    return ImageResponse(prompt=prompt, img=base64.b64encode(r.content).decode())
            raise ValueError("返回数据异常")
        except Exception as e:
            err = str(e).lower()
            if "content_policy" in err or "safety" in err or "filtered" in err:
                raise ValueError("生成失败啦! 内容太不堪入目了吧~")
            if "timeout" in err:
                raise ValueError("生成超时啦! 稍后再试试吧~")
            LOG.error("生图异常 model=%s: %s", model, e)
            raise ValueError("生成失败啦!")

    async def analyze_image(
            self,
            content: str,
            img_data: str,
            model: Optional[str] = None,
    ) -> str:
        LOG.info("分析图像: %s...", content[:50])
        is_debug = content.startswith("debug")
        clean_content = content.removeprefix("debug").strip() if is_debug else content

        result = await self.llm_router.vision(
            prompt=f"{VISION_PROMPT}\n\n用户问题: {clean_content}",
            image_data=img_data,
            model=model,
        )

        if is_debug:
            result = f"{result}\n\n(model: {model or 'default'})"
        return result
