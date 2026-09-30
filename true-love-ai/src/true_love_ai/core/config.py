# -*- coding: utf-8 -*-
"""
配置管理模块
主配置类，从各子模块聚合所有配置段。
"""
import logging
from typing import Optional

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config_llm import LLMConfig
from .config_http import HTTPConfig, SessionConfig
from .config_services import PlatformKeyConfig, NexuConfig
from true_love_common.observability.logging import LoggingConfig

LOG = logging.getLogger(__name__)


class Config(BaseSettings):
    """
    主配置类
    支持从 config.yaml 和环境变量加载
    """
    model_config = SettingsConfigDict(extra="ignore")

    # 每个 skill 的权限白名单，key 为 skill 名称
    # 格式：["*"] / ["wechat:*"] / ["wechat:user1", "lark:*"]
    # 未配置时所有人可用（规则1）；skill 代码/DB 中声明的权限优先（规则2）
    skill_permissions: dict[str, list[str]] = {}

    llm: Optional[LLMConfig] = None
    http: Optional[HTTPConfig] = None
    session: SessionConfig = SessionConfig()
    platform_key: PlatformKeyConfig = PlatformKeyConfig()
    nexu: NexuConfig = NexuConfig()

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        loki_config = data.get("loki", {}) or {}
        # 日志系统需要在读到配置后才能初始化（Loki 上报参数来自配置文件）
        LoggingConfig.setup(
            service_name="tl-ai",
            log_level=logging.INFO,
            json_format=True,
            enable_loki=loki_config.get("enable", False),
            loki_url=loki_config.get("loki_url", ""),
            loki_user_id=loki_config.get("user_id", ""),
            loki_api_key=loki_config.get("api_key", ""),
        )
        LOG.info(f"从 {path} 加载配置...")
        return cls(**data)


_config: Optional[Config] = None


def _config_path() -> str:
    import os
    return "config.yaml" if os.environ.get("APP_ENV", "") == "prod" else "config-dev.yaml"


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_yaml(_config_path())
    return _config


def reload_config() -> Config:
    global _config
    _config = Config.from_yaml(_config_path())
    return _config


__all__ = [
    "Config",
    "LLMConfig",
    "HTTPConfig",
    "SessionConfig",
    "PlatformKeyConfig",
    "NexuConfig",
    "get_config",
    "reload_config",
]
