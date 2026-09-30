#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration - 配置管理模块

提供配置加载功能。
使用单例模式确保配置只加载一次。
日志配置使用 LoggingConfig 类。
"""

import logging
import socket
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
        
        # 先初始化日志系统（使用配置文件中的 loki 配置）
        self._setup_logging()
        
        # 这台机器的名字：所有机器共用一份配置，按机器名区分各自的条目；也是消息里的 bot_id
        self.machine_name = socket.gethostname().lower()
        self.master_wix = self._find_master(self.config.get("master_wix"), self.machine_name)
        self.http_token = self.config["http_token"]
        
        Config._initialized = True
        
        # 日志确认配置加载
        LOG = logging.getLogger("Config")
        LOG.info(f"Config loaded: machine_name={self.machine_name}, master_wix={self.master_wix}")
    
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

    @staticmethod
    def _find_master(value, machine_name: str) -> str:
        """
        这台机器的管理员昵称，没配置时为空串

        Args:
            value: 配置里的 master_wix，{机器名: 昵称}
            machine_name: 这台机器的名字
        """
        master = ""
        if isinstance(value, dict):
            masters = {str(name).lower(): nickname for name, nickname in value.items()}
            master = str(masters.get(machine_name) or "").strip()
        if not master:
            logging.getLogger("Config").warning(
                f"No master configured for machine [{machine_name}], notifications to the master are off")
        return master

    @staticmethod
    def _load_config() -> dict:
        """从当前工作目录读取配置文件"""
        config_path = "config.yaml"
        with open(config_path, "r", encoding='utf-8') as fp:
            config = yaml.safe_load(fp)
        return config
