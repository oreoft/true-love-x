#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration - 配置管理模块

提供配置加载功能。
使用单例模式确保配置只加载一次。
日志配置使用 LoggingConfig 类。
"""

import logging
from typing import Optional

import yaml

from true_love_common.observability.logging import LoggingConfig

LOG = logging.getLogger("Config")


class Config:
    """
    配置管理类（单例模式）
    
    确保配置只加载一次。
    """
    _instance: Optional["Config"] = None
    _initialized: bool = False
    
    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        # 防止重复初始化
        if Config._initialized:
            return
        
        self.config = self._load_config()

        # 先初始化日志系统（使用配置文件中的 loki 配置）
        self._setup_logging()

        self.http_token = self.config["http_token"]
        # 所有 base 共用一份配置，按号（wxid）写各自的管理员；这个 base 跑的是哪个号，连上微信时才读得到
        bots = self.config.get("bots")
        self.bots: dict = bots if isinstance(bots, dict) else {}
        # server 回调这个 base 的地址，启动时由 main 用 tailnet 地址填上
        self.callback = ""

        Config._initialized = True

        LOG.info("Config loaded: %s bots configured", len(self.bots))

    def _setup_logging(self) -> None:
        """设置日志系统（从配置文件读取 Loki 配置）"""
        loki_config = self.config.get("loki", {}) or {}

        LoggingConfig.setup(
            service_name="tl-base",
            log_level=logging.INFO,
            json_format=True,
            enable_loki=loki_config.get("enable", False),
            loki_url=loki_config.get("loki_url", ""),
            loki_user_id=loki_config.get("user_id", ""),
            loki_api_key=loki_config.get("api_key", ""),
        )

    def master_of(self, bot_id: str) -> str:
        """这个号的管理员昵称，没配置时为空串"""
        bot = self.bots.get(bot_id) if bot_id else None
        if not isinstance(bot, dict):
            return ""
        return str(bot.get("master") or "").strip()

    @staticmethod
    def _load_config() -> dict:
        """从当前工作目录读取配置文件"""
        config_path = "config.yaml"
        with open(config_path, "r", encoding='utf-8') as fp:
            config = yaml.safe_load(fp)
        return config
