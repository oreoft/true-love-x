# -*- coding: utf-8 -*-
"""外部服务配置"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformKeyConfig(BaseSettings):
    """第三方平台 Key"""
    litellm_api_key: str = ""
    litellm_base_url: str = ""
    firecrawl_api_key: str = ""     # 读链接正文（公众号以外的链接），见 agent/link_reader


class NexuConfig(BaseSettings):
    """Nexu 服务配置"""
    model_config = SettingsConfigDict(extra="ignore")

    base_url: str = "http://nexu:3010"
    token: str = ""


