#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频服务：文生视频 / 图生视频（通过 LiteLLM proxy HTTP API）"""
import asyncio
import base64
import io
import logging
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image
from true_love_common.http.client import HttpResult, async_get, async_post

from true_love_ai.core.config import get_config
from true_love_ai.core.model_registry import get_model_registry
from true_love_ai.llm.router import get_llm_router
from true_love_ai.models.response import VideoResponse

LOG = logging.getLogger("VideoService")

GEN_VIDEO_DIR = Path("gen_video")
GEN_VIDEO_DIR.mkdir(exist_ok=True)

_VIDEO_PROMPT_SYSTEM = (
    "把用户的视频描述翻译并优化为适合 AI 生视频的英文 prompt，直接输出英文，不超过 150 词，不要解释。"
)


class VideoService:

    def __init__(self):
        cfg = get_config()
        self.llm_router = get_llm_router()
        self.registry = get_model_registry()
        self.base_url = cfg.platform_key.litellm_base_url.rstrip("/")
        self.api_key = cfg.platform_key.litellm_api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _gemini_headers(self) -> dict:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    async def generate_video(
            self,
            content: str,
            img_data_list: Optional[list[str]] = None,
    ) -> VideoResponse:
        video_prompt = await self._build_prompt(content)
        default_model  = self.registry.get("video", "default")
        fallback_model = self.registry.get("video", "fallback")

        try:
            return await self._generate_by_model(video_prompt, default_model, img_data_list)
        except Exception as e:
            if fallback_model:
                LOG.warning("主力视频生成失败，降级备用模型 %s: %s", fallback_model, e, exc_info=True)
                return await self._generate_by_model(video_prompt, fallback_model, img_data_list)
            raise

    async def _build_prompt(self, content: str) -> str:
        try:
            return await self.llm_router.chat(
                messages=[
                    {"role": "system", "content": _VIDEO_PROMPT_SYSTEM},
                    {"role": "user", "content": content},
                ]
            )
        except Exception as e:
            LOG.warning("视频 prompt 翻译失败，使用原始内容: %s", e)
            return content

    async def _generate_by_model(
            self,
            prompt: str,
            model: str,
            img_data_list: Optional[list[str]] = None,
    ) -> VideoResponse:
        is_img2video = bool(img_data_list)
        LOG.info("生成视频: model=%s %s", model, "图生视频" if is_img2video else "文生视频")

        if "gemini-omni" in model:
            video_bytes = await self._generate_by_omni(prompt, model, img_data_list)
        else:
            video_bytes = await self._generate_by_videos_api(prompt, model, img_data_list)

        vid = str(uuid.uuid4())
        video_path = GEN_VIDEO_DIR / f"{vid}.mp4"
        video_path.write_bytes(video_bytes)
        LOG.info("视频完成: %s (%.2fMB)", video_path, len(video_bytes) / 1024 / 1024)
        return VideoResponse(prompt=prompt, video_id=vid)

    async def _generate_by_videos_api(
            self,
            prompt: str,
            model: str,
            img_data_list: Optional[list[str]] = None,
    ) -> bytes:
        """Veo / Sora：/v1/videos 建任务，轮询完成后下载"""
        body: dict = {"model": model, "prompt": prompt, "size": "1280x720", "seconds": "8"}
        if img_data_list:
            mime_type, b64_data = self._detect_image(img_data_list[0])
            body["input_reference"] = {"bytesBase64Encoded": b64_data, "mimeType": mime_type}

        resp = await async_post(f"{self.base_url}/v1/videos", headers=self._headers(), json=body, timeout=60.0)
        self._raise_for_video_error(resp)
        data = resp.data if isinstance(resp.data, dict) else {}

        video_id = data.get("id")
        if not video_id:
            raise ValueError("视频任务创建失败：未获取到 job id")
        LOG.info("视频任务已创建: %s", video_id)
        return await self._poll_and_download(video_id)

    async def _generate_by_omni(
            self,
            prompt: str,
            model: str,
            img_data_list: Optional[list[str]] = None,
    ) -> bytes:
        """Gemini Omni 不支持 /v1/videos，经 LiteLLM 的 Gemini 透传调 Interactions API。
        同步返回视频文件地址（inline 上限 4MB，720p 的 10 秒视频会超），文件就绪后下载"""
        if img_data_list:
            mime_type, b64_data = self._detect_image(img_data_list[0])
            content = [
                {"type": "image", "data": b64_data, "mime_type": mime_type},
                {"type": "text", "text": prompt},
            ]
        else:
            content = prompt
        body = {
            "model": model.split("/", 1)[-1],
            "input": content,
            "response_format": {"type": "video", "aspect_ratio": "16:9", "resolution": "720p", "delivery": "uri"},
        }
        resp = await async_post(f"{self.base_url}/gemini/v1beta/interactions",
                                headers=self._gemini_headers(), json=body, timeout=600.0)
        self._raise_for_video_error(resp)
        data = resp.data if isinstance(resp.data, dict) else {}

        uri = next((c["uri"] for step in data.get("steps") or [] if step.get("type") == "model_output"
                    for c in step.get("content") or [] if c.get("type") == "video" and c.get("uri")), None)
        if not uri:
            LOG.warning("Omni 没有返回视频: status=%s id=%s", data.get("status"), data.get("id"))
            raise ValueError("视频生成失败啦!")
        file_name = uri.split("/v1beta/", 1)[1].split(":", 1)[0]  # files/xxx
        LOG.info("Omni 视频已生成: %s", file_name)

        for attempt in range(60):
            resp = await async_get(f"{self.base_url}/gemini/v1beta/{file_name}",
                                   headers=self._gemini_headers(), timeout=30.0)
            self._raise_for_video_error(resp)
            state = resp.data.get("state", "") if isinstance(resp.data, dict) else ""
            if state == "ACTIVE":
                break
            if state == "FAILED":
                raise ValueError("视频生成失败啦!")
            await asyncio.sleep(2.0)
        else:
            raise ValueError("视频生成超时，请稍后再试~")

        resp = await async_get(f"{self.base_url}/gemini/v1beta/{file_name}:download?alt=media",
                               headers=self._gemini_headers(), timeout=120.0)
        self._raise_for_video_error(resp)
        return resp.content

    async def _poll_and_download(self, video_id: str) -> bytes:
        for attempt in range(120):
            resp = await async_get(f"{self.base_url}/v1/videos/{video_id}", headers=self._headers(), timeout=30.0)
            self._raise_for_video_error(resp)
            data = resp.data if isinstance(resp.data, dict) else {}

            status = data.get("status", "")
            if status == "completed":
                break
            if status == "failed":
                raise ValueError("视频生成失败啦!")
            LOG.debug("视频生成中... %d/120 status=%s", attempt + 1, status)
            await asyncio.sleep(5.0)
        else:
            raise ValueError("视频生成超时，请稍后再试~")

        resp = await async_get(f"{self.base_url}/v1/videos/{video_id}/content", headers=self._headers(), timeout=120.0)
        self._raise_for_video_error(resp)
        return resp.content

    @staticmethod
    def _detect_image(raw: str) -> tuple[str, str]:
        """从传入的图片数据中探出真实 mime type，返回 (mime_type, 纯 base64 数据)"""
        if raw.startswith("data:") and ";base64," in raw:
            raw = raw.split(";base64,", 1)[1]
        image_bytes = base64.b64decode(raw)
        fmt = Image.open(io.BytesIO(image_bytes)).format
        mime_type = Image.MIME.get(fmt, "image/png")
        return mime_type, base64.b64encode(image_bytes).decode()

    @staticmethod
    def _raise_for_video_error(resp: HttpResult) -> None:
        if resp.status_code == 429:
            raise ValueError("呜呜~视频酱今天太累了，等一会再来找我玩吧~")
        if resp.status_code >= 400:
            err = resp.text.lower()
            if any(k in err for k in ["content_policy", "safety", "filtered", "blocked"]):
                raise ValueError("生成失败啦! 内容太不堪入目了吧~")
            raise ValueError(f"视频接口错误 {resp.status_code}: {resp.text[:200]}")
