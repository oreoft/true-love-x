# -*- coding: utf-8 -*-
"""外部服务配置"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformKeyConfig(BaseSettings):
    """第三方平台 Key"""
    litellm_api_key: str = ""
    litellm_base_url: str = ""


class BaseServerConfig(BaseSettings):
    """飞书 base 地址；微信 base、server 的地址登记在 true_love_common.hosts"""
    model_config = SettingsConfigDict(extra="ignore")

    lark_host: str = ""


class NexuConfig(BaseSettings):
    """Nexu 服务配置"""
    model_config = SettingsConfigDict(extra="ignore")

    base_url: str = "http://nexu:3010"
    token: str = ""


