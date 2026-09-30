# -*- coding: utf-8 -*-
"""外部服务配置"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformKeyConfig(BaseSettings):
    """第三方平台 Key"""
    litellm_api_key: str = ""
    litellm_base_url: str = ""


class NexuConfig(BaseSettings):
    """Nexu 服务配置"""
    model_config = SettingsConfigDict(extra="ignore")

    base_url: str = "http://nexu:3010"
    token: str = ""


