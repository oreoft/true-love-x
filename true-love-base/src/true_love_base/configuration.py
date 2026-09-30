#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration - 配置管理模块

提供配置加载功能。
使用单例模式确保配置只加载一次。
日志配置使用 LoggingConfig 类。
"""

import logging
import os
from typing import Optional

import yaml

from true_love_common.observability.logging import LoggingConfig


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

        # 这个 base 跑的是哪个号：部署时注入 BOT_ID（微信是 wxid），所有 base 共用一份配置，按它取自己那一段
        self.bot_id = os.environ.get("BOT_ID", "").strip()

        # 先初始化日志系统（使用配置文件中的 loki 配置），日志带上 bot_id 方便按号筛选
        self._setup_logging()

        bot = self._find_bot(self.config.get("bots"), self.bot_id)
        self.master_wix = str(bot.get("master") or "").strip()
        self.http_token = self.config["http_token"]
        # server 回调这个 base 的地址，启动时由 main 用 tailnet 地址填上
        self.callback = ""

        Config._initialized = True

        LOG = logging.getLogger("Config")
        if not self.master_wix:
            LOG.warning(f"No master configured for bot [{self.bot_id}], notifications to the master are off")
        LOG.info(f"Config loaded: bot_id={self.bot_id}, master_wix={self.master_wix}")

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
            loki_tags={"bot_id": self.bot_id} if self.bot_id else None,
        )

    @staticmethod
    def _find_bot(bots, bot_id: str) -> dict:
        """
        配置里这个号的那一段；没注入 BOT_ID 或者表里没有这个号时抛 SystemExit，base 不启动

        Args:
            bots: 配置里的 bots，{bot_id: {master: 管理员昵称}}
            bot_id: 部署时注入的 BOT_ID
        """
        if not bot_id:
            raise SystemExit("BOT_ID is not set: deploy base with the wxid of the account it runs")
        bot = bots.get(bot_id) if isinstance(bots, dict) else None
        if not isinstance(bot, dict):
            raise SystemExit(f"Bot [{bot_id}] is not in the bots map of config.yaml, add it before starting base")
        return bot

    @staticmethod
    def _load_config() -> dict:
        """从当前工作目录读取配置文件"""
        config_path = "config.yaml"
        with open(config_path, "r", encoding='utf-8') as fp:
            config = yaml.safe_load(fp)
        return config
