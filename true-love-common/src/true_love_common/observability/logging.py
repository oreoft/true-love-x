# -*- coding: utf-8 -*-
"""Shared logging configuration."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
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
        loki_error = ""
        if enable_loki and loki_url and loki_user_id and loki_api_key:
            try:
                loki_handler = _build_loki_handler(
                    Queue(queue_size),
                    _LokiFailureReporter(service_name, console_handler),
                    url=f"{loki_url.rstrip('/')}/loki/api/v1/push",
                    tags={"service_name": service_name},
                    auth=(loki_user_id, loki_api_key),
                )
                loki_handler.setLevel(log_level)
                loki_handler.addFilter(trace_filter)
                handlers.append(loki_handler)
                loki_enabled = True
            except ImportError:
                loki_error = "未安装 python-logging-loki"
            except Exception as e:
                loki_error = f"无法创建 LokiHandler: {e!r}"

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()
        root_logger.addFilter(trace_filter)

        for handler in handlers:
            root_logger.addHandler(handler)
        # httpx 每个请求都打一行 "HTTP Request: ..."，和 http.client 的 HTTP OUT 日志重复
        logging.getLogger("httpx").setLevel(logging.WARNING)

        cls._initialized = True
        logging.getLogger("LoggingConfig").info(
            "日志配置完成: service=%s, json=%s",
            service_name,
            json_format,
            extra={"extra_fields": {"loki": loki_enabled}},
        )
        if loki_error:
            logging.getLogger("LoggingConfig").warning("Loki 推送已禁用: %s", loki_error)

    @classmethod
    def add_loki_tags(cls, provider) -> None:
        """
        给之后的每条日志补上 Loki 标签，provider() 返回 {标签: 值}，值为空的不加

        用于启动后才知道的标签，比如 base 连上微信后才读到的 bot_id。
        """
        class _TagFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                tags = {key: value for key, value in (provider() or {}).items() if value}
                if tags:
                    record.tags = {**getattr(record, "tags", {}), **tags}
                return True

        tag_filter = _TagFilter()
        for handler in logging.getLogger().handlers:
            handler.addFilter(tag_filter)

    @classmethod
    def _ensure_utf8_stdout(cls) -> None:
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            else:
                sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
        except Exception:
            pass


# 推 Loki 失败（超时、认证失效、队列满）时日志会成批丢失，库只往 stderr 打 traceback。
# 这里改成限频地在控制台记一条 WARNING，直接交给控制台 handler，不再进 Loki 绕回来。
_LOKI_TIMEOUT = (3, 10)
_LOKI_REPORT_INTERVAL = 60


class _LokiFailureReporter:
    def __init__(self, service_name: str, console_handler: logging.Handler):
        self._service_name = service_name
        self._console = console_handler
        self._lock = threading.Lock()
        self._dropped = 0
        self._last_report = 0.0

    def failed(self, reason: str, error: BaseException | None = None) -> None:
        with self._lock:
            self._dropped += 1
            now = time.monotonic()
            if self._last_report and now - self._last_report < _LOKI_REPORT_INTERVAL:
                return
            dropped, self._dropped, self._last_report = self._dropped, 0, now
        record = logging.LogRecord(
            "LoggingConfig", logging.WARNING, __file__, 0,
            "Loki 推送失败，上次报告以来丢了 %s 条日志: %s %r",
            (dropped, reason, error), None,
        )
        try:
            self._console.handle(record)
        except Exception:
            pass


def _build_loki_handler(queue: Queue, reporter: _LokiFailureReporter, **kwargs: Any) -> logging.Handler:
    import requests
    from logging_loki import LokiQueueHandler

    class _TimeoutSession(requests.Session):
        def request(self, *args: Any, **kw: Any):
            kw.setdefault("timeout", _LOKI_TIMEOUT)
            return super().request(*args, **kw)

    class _Handler(LokiQueueHandler):
        def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
            reporter.failed("队列已满", sys.exc_info()[1])

    handler = _Handler(queue, version="1", **kwargs)
    loki = handler.handler
    loki.emitter.session_class = _TimeoutSession

    def _push_failed(record: logging.LogRecord) -> None:
        loki.emitter.close()
        reporter.failed("推送出错", sys.exc_info()[1])

    loki.handleError = _push_failed
    return handler
