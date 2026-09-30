#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging

import yaml

from true_love_common.observability.logging import LoggingConfig

LOG = logging.getLogger("Configuration")


class Config:
    _instance = None

    def __new__(cls):
        """new是魔法方法 实例化的时候会调用一次 用它来实现单例"""
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance.reload()
        return cls._instance

    @staticmethod
    def _load_config() -> dict:
        import os
        app_env = os.environ.get("APP_ENV", "")
        config_path = "config.yaml" if app_env == "prod" else "config-dev.yaml"
        with open(config_path, "r", encoding='utf-8') as fp:
            updated_config: dict = yaml.safe_load(fp)
        return updated_config

    def reload(self) -> None:
        yconfig = self._load_config()
        if yconfig:
            loki_config = yconfig.get("loki", {}) or {}
            # 日志系统需要在读到配置后才能初始化（Loki 上报参数来自配置文件）
            LoggingConfig.setup(
                service_name="tl-server",
                log_level=logging.INFO,
                json_format=True,
                enable_loki=loki_config.get("enable", False),
                loki_url=loki_config.get("loki_url", ""),
                loki_user_id=loki_config.get("user_id", ""),
                loki_api_key=loki_config.get("api_key", ""),
            )
            LOG.info("_load_config 刷新配置成功: keys=%s", sorted(yconfig.keys()))

            self.HTTP_TOKEN: dict = yconfig.get("http_token")
            self.HTTP = yconfig.get("http")
            self.ALAPI: dict = yconfig.get("alapi", {})
            # 调用方没指定机器人时用哪个号（外部 /send-msg、server 启停通知、AI 通知管理员）
            self.DEFAULT_BOT_ID: str = str(yconfig.get("default_bot_id") or "").strip()
            self.LOKI: dict = loki_config
