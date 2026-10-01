#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音服务：文本转语音（通过 LiteLLM proxy HTTP API）"""
import io
import logging
import uuid
import wave
from pathlib import Path

from true_love_common.http.client import HttpResult, async_post

from true_love_ai.core.config import get_config
from true_love_ai.core.model_registry import get_model_registry
from true_love_ai.models.response import AudioResponse

LOG = logging.getLogger("AudioService")

GEN_AUDIO_DIR = Path("gen_audio")
GEN_AUDIO_DIR.mkdir(exist_ok=True)


class AudioService:

    def __init__(self):
        cfg = get_config()
        self.registry = get_model_registry()
        self.base_url = cfg.platform_key.litellm_base_url.rstrip("/")
        self.api_key = cfg.platform_key.litellm_api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def text_to_speech(self, text: str, voice: str = "Kore", style: str = "") -> AudioResponse:
        """style：语音风格描述（来自人设），加在正文前面让模型按这个风格朗读"""
        text_input = f"{style}\n{text}" if style else text
        default_model = self.registry.get("tts", "default")
        fallback_model = self.registry.get("tts", "fallback")

        try:
            return await self._generate_by_model(text, text_input, default_model, voice)
        except Exception as e:
            if fallback_model:
                LOG.warning("主力语音合成失败，降级备用模型 %s: %s", fallback_model, e, exc_info=True)
                return await self._generate_by_model(text, text_input, fallback_model, voice)
            raise

    async def _generate_by_model(self, text: str, text_input: str, model: str, voice: str) -> AudioResponse:
        LOG.info("生成语音: model=%s voice=%s", model, voice)
        body = {"model": model, "input": text_input, "voice": voice}

        resp = await async_post(f"{self.base_url}/v1/audio/speech", headers=self._headers(), json=body, timeout=60.0)
        self._raise_for_audio_error(resp)

        aid = str(uuid.uuid4())
        audio_path = GEN_AUDIO_DIR / f"{aid}.wav"
        audio_path.write_bytes(resp.content)
        duration = self._wav_duration_seconds(resp.content)
        LOG.info("语音完成: %s (%.2fKB, %.1fs)", audio_path, len(resp.content) / 1024, duration)
        return AudioResponse(text=text, audio_id=aid)

    @staticmethod
    def _wav_duration_seconds(data: bytes) -> float:
        try:
            with wave.open(io.BytesIO(data)) as wf:
                return round(wf.getnframes() / float(wf.getframerate()), 2)
        except Exception as e:
            LOG.warning("解析 wav 时长失败: %s", e)
            return 0.0

    @staticmethod
    def _raise_for_audio_error(resp: HttpResult) -> None:
        if resp.status_code == 429:
            raise ValueError("呜呜~语音酱今天太累了，等一会再来找我玩吧~")
        if resp.status_code >= 400:
            err = resp.text.lower()
            if any(k in err for k in ["content_policy", "safety", "filtered", "blocked"]):
                raise ValueError("生成失败啦! 内容太不堪入目了吧~")
            raise ValueError(f"语音接口错误 {resp.status_code}: {resp.text[:200]}")
