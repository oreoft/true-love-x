# -*- coding: utf-8 -*-
"""Unified outbound HTTP client with trace propagation and logging."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from true_love_common.observability.sanitize import sanitize_json_text, sanitize_text
from true_love_common.observability.trace import GCP_TRACE_HEADER, get_gcp_trace_header

LOG = logging.getLogger("HttpClient")


@dataclass
class HttpResult:
    method: str
    url: str
    ok: bool
    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes = b""
    data: Any = None
    error: str = ""
    error_type: str = ""
    cost_ms: float = 0.0

    def raise_for_status(self) -> None:
        if self.ok:
            return
        raise RuntimeError(self.error or f"HTTP {self.status_code}")


def trace_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    headers[GCP_TRACE_HEADER] = get_gcp_trace_header()
    return headers


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: Any = None,
    client: httpx.Client | None = None,
    quiet: bool = False,
    **kwargs: Any,
) -> HttpResult:
    """quiet=True 时只在出错时记日志（后台管理这类高频、没排查价值的调用）"""
    method = method.upper()
    merged_headers = trace_headers(headers)
    httpx_timeout = _normalize_timeout(timeout)
    if not quiet:
        _log_start(method, url, kwargs)
    start = time.perf_counter()
    try:
        if client is not None:
            response = client.request(method, url, headers=merged_headers, timeout=httpx_timeout, **kwargs)
        else:
            with httpx.Client(timeout=httpx_timeout) as active_client:
                response = active_client.request(method, url, headers=merged_headers, **kwargs)
        return _ok_result(method, url, response, start, quiet)
    except Exception as exc:
        return _error_result(method, url, exc, start)


def get(url: str, **kwargs: Any) -> HttpResult:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> HttpResult:
    return request("POST", url, **kwargs)


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> HttpResult:
    return post(url, json=payload, **kwargs)


async def async_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: Any = None,
    quiet: bool = False,
    **kwargs: Any,
) -> HttpResult:
    """quiet=True 时只在出错时记日志"""
    method = method.upper()
    merged_headers = trace_headers(headers)
    httpx_timeout = _normalize_timeout(timeout)
    if not quiet:
        _log_start(method, url, kwargs)
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx_timeout) as client:
            response = await client.request(method, url, headers=merged_headers, **kwargs)
        return _ok_result(method, url, response, start, quiet)
    except Exception as exc:
        return _error_result(method, url, exc, start)


async def async_get(url: str, **kwargs: Any) -> HttpResult:
    return await async_request("GET", url, **kwargs)


async def async_post(url: str, **kwargs: Any) -> HttpResult:
    return await async_request("POST", url, **kwargs)


async def async_post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> HttpResult:
    return await async_post(url, json=payload, **kwargs)


def _ok_result(method: str, url: str, response: Any, start: float, quiet: bool = False) -> HttpResult:
    result = _result_from_httpx_response(method, url, response, (time.perf_counter() - start) * 1000)
    if not quiet:
        _log_end(result)
    return result


def _error_result(method: str, url: str, exc: Exception, start: float) -> HttpResult:
    result = HttpResult(
        method=method,
        url=url,
        ok=False,
        error=repr(exc),
        error_type=exc.__class__.__name__,
        cost_ms=(time.perf_counter() - start) * 1000,
    )
    _log_error(result)
    return result


def _result_from_httpx_response(method: str, url: str, response: Any, cost_ms: float) -> HttpResult:
    try:
        text = response.text or ""
    except Exception:
        text = ""
    return HttpResult(
        method=method,
        url=url,
        ok=200 <= response.status_code < 400,
        status_code=response.status_code,
        headers=dict(response.headers),
        text=text,
        content=response.content or b"",
        data=_safe_json(text),
        cost_ms=cost_ms,
    )


def _safe_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _normalize_timeout(timeout: Any) -> httpx.Timeout | float | None:
    if timeout is None or isinstance(timeout, (int, float, httpx.Timeout)):
        return timeout
    if isinstance(timeout, tuple) and len(timeout) == 2:
        connect_timeout, read_timeout = timeout
        return httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
    return timeout


def _log_start(method: str, url: str, kwargs: dict[str, Any]) -> None:
    body = kwargs.get("json")
    if body is None:
        body = kwargs.get("data")
    LOG.info(
        "HTTP OUT start method=%s url=%s body=%s",
        method,
        url,
        _format_body(body),
        extra={"event": "http.out.start", "direction": "out", "method": method, "url": url},
    )


def _log_end(result: HttpResult) -> None:
    LOG.info(
        "HTTP OUT end method=%s url=%s status=%s cost_ms=%.0f body=%s",
        result.method,
        result.url,
        result.status_code,
        result.cost_ms,
        _format_body(result.data if result.data is not None else result.text, max_length=500),
        extra={
            "event": "http.out.end",
            "direction": "out",
            "method": result.method,
            "url": result.url,
            "status_code": result.status_code,
            "cost_ms": round(result.cost_ms),
        },
    )


def _log_error(result: HttpResult) -> None:
    LOG.error(
        "HTTP OUT error method=%s url=%s cost_ms=%.0f error_type=%s error=%s",
        result.method,
        result.url,
        result.cost_ms,
        result.error_type,
        result.error,
        extra={
            "event": "http.out.error",
            "direction": "out",
            "method": result.method,
            "url": result.url,
            "cost_ms": round(result.cost_ms),
            "error_type": result.error_type,
        },
    )


def _format_body(body: Any, max_length: int = 500) -> str:
    if body is None:
        return "empty"
    if isinstance(body, (bytes, bytearray)):
        return f"[bytes {len(body)}]"
    if isinstance(body, (dict, list)):
        return sanitize_json_text(json.dumps(body, ensure_ascii=False), max_text_length=max_length)
    return sanitize_text(str(body), max_length=max_length)
