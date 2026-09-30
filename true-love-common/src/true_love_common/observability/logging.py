# -*- coding: utf-8 -*-
"""Shared logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from queue import Queue
from typing import Any

from true_love_common.observability.sanitize import sanitize_text, sanitize_value
from true_love_common.observability.trace import get_span_id, get_trace_id


class TraceLogFilter(logging.Filter):
    """Inject current trace fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        record.span_id = get_span_id()
        return True


class JsonFormatter(logging.Formatter):
    """JSON formatter for Docker/Loki/Grafana logs."""

    def __init__(self, service_name: str = "unknown"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": sanitize_text(record.getMessage()),
            "trace_id": getattr(record, "trace_id", get_trace_id()),
            "span_id": getattr(record, "span_id", get_span_id()),
        }

        for field in (
            "event",
            "direction",
            "request_id",
            "method",
            "path",
            "status_code",
            "cost_ms",
            "error_type",
        ):
            if hasattr(record, field):
                log_data[field] = getattr(record, field)

        if record.levelno >= logging.WARNING:
            log_data["location"] = {
                "file": record.filename,
                "function": record.funcName,
                "line": record.lineno,
            }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_fields"):
            log_data["extra"] = sanitize_value(record.extra_fields)

        return json.dumps(log_data, ensure_ascii=False)


class LoggingConfig:
    """Common logging setup for True Love services."""

    _initialized: bool = False

    SIMPLE_FORMAT = "%(asctime)s %(levelname)s [trace=%(trace_id)s span=%(span_id)s] %(name)s: %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    @classmethod
    def setup(
        cls,
        service_name: str,
        log_level: int = logging.INFO,
        json_format: bool = True,
        queue_size: int = 10000,
        enable_loki: bool = False,
        loki_url: str = "",
        loki_user_id: str = "",
        loki_api_key: str = "",
        loki_tags: dict[str, str] | None = None,
    ) -> None:
        if cls._initialized:
            return

        cls._ensure_utf8_stdout()

        trace_filter = TraceLogFilter()
        if json_format:
            formatter: logging.Formatter = JsonFormatter(service_name)
        else:
            formatter = logging.Formatter(cls.SIMPLE_FORMAT, cls.DATE_FORMAT)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(trace_filter)
        handlers: list[logging.Handler] = [console_handler]

        loki_enabled = False
        if enable_loki and loki_url and loki_user_id and loki_api_key:
            try:
                from logging_loki import LokiQueueHandler

                loki_handler = LokiQueueHandler(
                    Queue(queue_size),
                    url=f"{loki_url.rstrip('/')}/loki/api/v1/push",
                    tags={"service_name": service_name, **(loki_tags or {})},
                    auth=(loki_user_id, loki_api_key),
                    version="1",
                )
                loki_handler.setLevel(log_level)
                loki_handler.addFilter(trace_filter)
                handlers.append(loki_handler)
                loki_enabled = True
            except ImportError:
                print("[LoggingConfig] 警告: 未安装 python-logging-loki，Loki 推送已禁用")
            except Exception as e:
                print(f"[LoggingConfig] 警告: 无法创建 LokiHandler: {e}")

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()
        root_logger.addFilter(trace_filter)

        for handler in handlers:
            root_logger.addHandler(handler)

        cls._initialized = True
        logging.getLogger("LoggingConfig").info(
            "日志配置完成: service=%s, json=%s",
            service_name,
            json_format,
            extra={"extra_fields": {"loki": loki_enabled}},
        )

    @classmethod
    def _ensure_utf8_stdout(cls) -> None:
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            else:
                sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
        except Exception:
            pass
