# -*- coding: utf-8 -*-
"""
模型注册表
所有机器人共用一套模型：代码里是默认值，改过的存在 AI 库的 model_settings 表，
tl-admin（经 server 转发）和 set_model 技能都改库，改完立即生效。

模型直接写完整 LiteLLM 字符串（如 openai/gpt-5.4），不做前缀转换。
"""
import logging
from typing import Optional

from true_love_ai.core.db_engine import SessionLocal
from true_love_ai.models.model_setting import ModelSetting

LOG = logging.getLogger("ModelRegistry")

# {类别: {key: 模型}}；default 是主力，fallback 是主力失败时的备用
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "chat":       {"default": "2api/openai/gpt-5.6-sol", "fallback": "gemini/gemini-3-pro"},
    "compress":   {"default": "openai/gpt-5.6-luna"},
    "vision":     {"default": "2api/openai/gpt-5.6-sol"},
    "image":      {"default": "2api/openai/gpt-image-2", "fallback": "gemini/gemini-3-pro-image"},
    "image_edit": {"default": "2api/openai/gpt-image-2", "fallback": "openai/gpt-image-1.5"},
    "video":      {"default": "gemini/veo-3.1-fast-generate-preview", "fallback": "openai/sora-2-pro"},
    "tts":        {"default": "gemini/gemini-3.1-flash-tts-preview"},
}
CATEGORIES = list(DEFAULT_MODELS)
KEYS = ("default", "fallback")


class ModelRegistry:

    def __init__(self):
        self._models: dict[str, dict[str, str]] = {}

    def load(self) -> None:
        """代码默认值叠上库里改过的"""
        models = {cat: dict(keys) for cat, keys in DEFAULT_MODELS.items()}
        with SessionLocal() as db:
            for row in db.query(ModelSetting).all():
                if row.category in models and row.key in KEYS:
                    models[row.category][row.key] = row.value
        self._models = models
        LOG.info("ModelRegistry 加载完成: %d 个类别", len(self._models))

    def get(self, category: str, key: str = "default") -> str:
        if key == "fallback":
            return self._models.get(category, {}).get("fallback", "")
        try:
            return self._models[category][key]
        except KeyError:
            raise KeyError(f"模型未找到: {category}.{key}，当前配置: {self._models}")

    def set(self, category: str, key: str, value: str) -> None:
        """改一个模型；value 为空时恢复代码里的默认值。Raises: ValueError"""
        category, key, value = category.strip(), key.strip(), value.strip()
        if category not in DEFAULT_MODELS:
            raise ValueError(f"没有这个类别：{category}，可选 {', '.join(CATEGORIES)}")
        if key not in KEYS:
            raise ValueError("key 只能是 default 或 fallback")
        if not value and key == "default" and "default" not in DEFAULT_MODELS[category]:
            raise ValueError("主力模型不能为空")
        with SessionLocal() as db:
            row = db.get(ModelSetting, (category, key))
            if value:
                if row is None:
                    row = ModelSetting(category=category, key=key)
                    db.add(row)
                row.value = value
            elif row is not None:
                db.delete(row)
            db.commit()
        self.load()
        LOG.info("模型已更新: %s.%s = %s", category, key, value or "（默认）")

    def all(self) -> dict[str, dict[str, str]]:
        return {cat: dict(keys) for cat, keys in self._models.items()}

    def describe(self) -> list[dict]:
        """tl-admin 用：每个类别当前的主力、备用和各自的默认值"""
        return [{
            "category": cat,
            "default": self._models[cat].get("default", ""),
            "fallback": self._models[cat].get("fallback", ""),
            "builtin_default": DEFAULT_MODELS[cat].get("default", ""),
            "builtin_fallback": DEFAULT_MODELS[cat].get("fallback", ""),
        } for cat in CATEGORIES]


_registry: Optional[ModelRegistry] = None


def get_model_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
