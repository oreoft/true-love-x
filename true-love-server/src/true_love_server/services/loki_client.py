# -*- coding: utf-8 -*-
"""
Loki Client - Loki 日志查询客户端

直接访问 Grafana Cloud Loki API（使用 Basic Auth）。
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from ..core import Config

LOG = logging.getLogger("LokiClient")


@dataclass
class LogEntry:
    """日志条目"""
    timestamp: int  # 毫秒时间戳
    time_str: str  # 格式化时间字符串
    level: str  # 日志等级
    service: str  # 服务名称
    content: str  # 日志内容
    raw: str  # 原始日志行
    ts_ns: str  # Loki 入库时间戳（纳秒），用作分页游标和去重

    def to_dict(self) -> dict:
        return asdict(self)


class LokiClient:
    """Loki 客户端 - 直接访问 Grafana Cloud Loki"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        config = Config()

        loki_config = config.LOKI or {}

        self.loki_url = loki_config.get('loki_url', '').rstrip('/')
        self.user_id = loki_config.get('user_id', '')
        self.api_key = loki_config.get('api_key', '')
        self.services = loki_config.get('services', ['tl-ai', 'tl-base', 'tl-server'])

        # 复用 Session + 重试，避免连接复用时偶发的 SSL EOF
        retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503])
        adapter = HTTPAdapter(max_retries=retry)
        self._session = requests.Session()
        self._session.mount("https://", adapter)

        self._initialized = True
        LOG.info("LokiClient 初始化完成, loki_url: %s, user_id: %s", self.loki_url, self.user_id)

    def _get_auth(self) -> HTTPBasicAuth:
        """获取 Basic Auth 认证"""
        return HTTPBasicAuth(self.user_id, self.api_key)

    def _build_query(self, services: Optional[List[str]] = None, keyword: str = '', bot_id: str = '') -> str:
        """
        构建 LogQL 查询语句

        services 只接受配置里的服务名，不认识的忽略；为空时查全部。
        keyword 按不区分大小写的子串匹配整行日志。
        bot_id 只筛带 bot_id 标签的日志：每个 base 只跑一个机器人，上报时带这个标签；
        server 和 AI 同时服务所有机器人，日志没有这个标签。
        """
        selected = [s for s in (services or []) if s in self.services] or self.services
        services_regex = '|'.join(selected)
        bot_id = re.sub(r'[^A-Za-z0-9_-]', '', bot_id or '')
        bot_selector = f', bot_id=`{bot_id}`' if bot_id else ''
        query = f'{{service_name=~`{services_regex}`{bot_selector}}}'
        keyword = keyword.replace('`', '').strip()
        if keyword:
            # 反引号字符串里不需要再转义，关键词本身按字面匹配
            query += f' |~ `(?i){re.escape(keyword)}`'
        return query

    def _parse_log_line(self, line: str, labels: dict, ts_ns: int) -> LogEntry:
        """
        解析日志行

        Loki 返回的数据结构：
        - labels 中包含 service_name, level 等信息
        - line 是 JSON 格式日志内容（由 JsonFormatter 生成）

        时间戳优先使用 JSON body 中的 timestamp 字段（实际打日志时间），
        避免 LokiQueueHandler 异步推送导致的入库时间偏差。
        """
        service = labels.get('service_name', labels.get('service', 'unknown'))
        level = labels.get('level', 'INFO').upper()
        content = line.strip()

        # 优先从 JSON body 提取实际打日志时间和内容
        actual_ts_ns = ts_ns
        try:
            log_data = json.loads(line)
            content = log_data.get('message', content)
            if 'level' in log_data:
                level = log_data['level'].upper()
            if 'service' in log_data:
                service = log_data['service']
            if 'timestamp' in log_data:
                # JsonFormatter 写入的是 ISO 格式 UTC 时间，转为纳秒时间戳
                dt_log = datetime.fromisoformat(log_data['timestamp'])
                actual_ts_ns = int(dt_log.timestamp() * 1_000_000_000)
        except (ValueError, TypeError, AttributeError):
            # 不是 JSON 或字段格式不对的日志行，按原文显示
            pass

        if level == 'WARNING':
            level = 'WARN'
        if level not in ('INFO', 'WARN', 'ERROR', 'DEBUG'):
            level = 'INFO'

        dt = datetime.fromtimestamp(actual_ts_ns / 1_000_000_000)
        time_str = dt.strftime('%Y-%m-%d %H:%M:%S.') + f'{dt.microsecond // 1000:03d}'

        return LogEntry(
            timestamp=actual_ts_ns // 1_000_000,
            time_str=time_str,
            level=level,
            service=service,
            content=content,
            raw=line,
            ts_ns=str(ts_ns)
        )

    def query_range(
            self,
            start_ns: int,
            end_ns: int,
            limit: int = 50,
            services: Optional[List[str]] = None,
            keyword: str = '',
            bot_id: str = ''
    ) -> dict:
        """
        查询时间范围内的日志
        
        Args:
            start_ns: 开始时间（纳秒时间戳）
            end_ns: 结束时间（纳秒时间戳）
            limit: 最大返回条数
            services: 只查这些服务，为空查全部
            keyword: 关键词，不区分大小写
            bot_id: 只查这个机器人的 base 日志，为空不筛
        
        Returns:
            {
                "success": bool,
                "logs": [LogEntry...],
                "message": str
            }
        """
        if not self.loki_url or not self.user_id or not self.api_key:
            return {
                "success": False,
                "logs": [],
                "message": "Loki 配置不完整，请检查 config.yaml 中的 loki 配置"
            }

        query = self._build_query(services, keyword, bot_id)

        # 直接访问 Loki API
        url = f"{self.loki_url}/loki/api/v1/query_range"

        params = {
            'query': query,
            'start': start_ns,
            'end': end_ns,
            'limit': limit,
            'direction': 'backward',  # 从新到旧取，分页往更早翻
        }

        try:
            resp = self._session.get(url, auth=self._get_auth(), params=params, timeout=(10, 30))
            resp.raise_for_status()

            data = resp.json()

            if data.get('status') != 'success':
                LOG.error("Loki 查询失败: %s", data)
                return {
                    "success": False,
                    "logs": [],
                    "message": f"Loki 返回错误: {data.get('error', 'unknown')}"
                }

            # 解析结果
            logs: List[LogEntry] = []

            result = data.get('data', {}).get('result', [])
            for stream in result:
                labels = stream.get('stream', {})
                values = stream.get('values', [])

                for ts_ns_str, line in values:
                    logs.append(self._parse_log_line(line, labels, int(ts_ns_str)))

            # 按时间戳排序（从新到旧，前端最新的在最上面）
            logs.sort(key=lambda x: x.timestamp, reverse=True)

            return {
                "success": True,
                "logs": logs,
                "message": ""
            }

        except requests.exceptions.Timeout:
            LOG.error("Loki 查询超时")
            return {
                "success": False,
                "logs": [],
                "message": "查询超时，请稍后重试"
            }
        except requests.exceptions.HTTPError as e:
            LOG.error("Loki HTTP 错误: %s", e)
            error_msg = str(e)
            if e.response is not None:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get('message', str(e))
                except (ValueError, AttributeError):
                    # 返回体不是 JSON 对象时用 HTTPError 自己的描述
                    pass
            return {
                "success": False,
                "logs": [],
                "message": f"HTTP 错误: {error_msg}"
            }
        except Exception as e:
            LOG.exception("Loki 查询异常")
            return {
                "success": False,
                "logs": [],
                "message": str(e)
            }


# 单例获取函数
_loki_client: Optional[LokiClient] = None


def get_loki_client() -> LokiClient:
    """获取 LokiClient 单例"""
    global _loki_client
    if _loki_client is None:
        _loki_client = LokiClient()
    return _loki_client
