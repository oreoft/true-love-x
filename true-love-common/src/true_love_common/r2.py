# -*- coding: utf-8 -*-
"""
发给 base 的一次性文件走 Cloudflare R2 中转

AI 生成的图片、视频、语音和 server 定时任务的图片，先传到私有桶，再把预签名链接交给 base 下载。
美国到国内走 Tailscale 直连很慢（跨境 UDP 被限速），走 Cloudflare 是 TCP，快得多。
桶上配了生命周期规则，对象上传 1 天后自动删除；链接默认 1 小时有效。
base 收到的媒体还是存在 base 上，别的服务回源 base 下载（见 media.py），不走这里。

签名按 S3 的 SigV4 手写，不引入 boto3。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from true_love_common.http.client import async_request

LOG = logging.getLogger("R2")

_REGION = "auto"
_SERVICE = "s3"


@dataclass(frozen=True)
class R2Config:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str

    @property
    def host(self) -> str:
        return f"{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_dict(cls, data: dict | None) -> "R2Config":
        """配置文件里的 r2 段。Raises: ValueError"""
        data = data or {}
        missing = [k for k in ("account_id", "bucket", "access_key_id", "secret_access_key") if not data.get(k)]
        if missing:
            raise ValueError(f"配置里缺 r2.{', r2.'.join(missing)}")
        return cls(data["account_id"], data["bucket"], data["access_key_id"], data["secret_access_key"])


def is_r2_url(url: str) -> bool:
    return ".r2.cloudflarestorage.com/" in url


async def upload(cfg: R2Config, local_path: str | Path, prefix: str, ttl: int = 3600) -> str:
    """上传本地文件，返回 ttl 秒内有效的下载链接；文件名保留，base 发文件时用得上。Raises: RuntimeError"""
    path = Path(local_path)
    data = path.read_bytes()
    key = f"{prefix}/{dt.datetime.now(dt.timezone.utc):%Y%m%d}/{uuid.uuid4().hex}/{path.name}"
    url, headers = _signed_put(cfg, key, data)
    result = await async_request("PUT", url, headers=headers, content=data, timeout=(10, 300), quiet=True)
    if not result.ok:
        raise RuntimeError(f"R2 上传失败: {key} {result.status_code} {result.error or result.text[:200]}")
    LOG.info("R2 上传: %s (%.2fMB)", key, len(data) / 1024 / 1024)
    return presign(cfg, key, ttl)


def presign(cfg: R2Config, key: str, ttl: int = 3600, now: dt.datetime | None = None) -> str:
    """GET 的预签名链接"""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date, scope = _dates(now)
    path = _path(cfg, key)
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{cfg.access_key_id}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(ttl),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
    canonical = f"GET\n{path}\n{canonical_query}\nhost:{cfg.host}\n\nhost\nUNSIGNED-PAYLOAD"
    signature = _sign(cfg, now, canonical)
    return f"https://{cfg.host}{path}?{canonical_query}&X-Amz-Signature={signature}"


def _signed_put(cfg: R2Config, key: str, data: bytes,
                now: dt.datetime | None = None) -> tuple[str, dict[str, str]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date, _ = _dates(now)
    path = _path(cfg, key)
    payload_hash = hashlib.sha256(data).hexdigest()
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = (f"PUT\n{path}\n\nhost:{cfg.host}\nx-amz-content-sha256:{payload_hash}\n"
                 f"x-amz-date:{amz_date}\n\n{signed}\n{payload_hash}")
    signature = _sign(cfg, now, canonical)
    _, scope = _dates(now)
    headers = {
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={cfg.access_key_id}/{scope}, "
                          f"SignedHeaders={signed}, Signature={signature}"),
    }
    return f"https://{cfg.host}{path}", headers


def _dates(now: dt.datetime) -> tuple[str, str]:
    day = now.strftime("%Y%m%d")
    return now.strftime("%Y%m%dT%H%M%SZ"), f"{day}/{_REGION}/{_SERVICE}/aws4_request"


def _path(cfg: R2Config, key: str) -> str:
    return "/" + quote(f"{cfg.bucket}/{key}", safe="/-_.~")


def _q(value: str) -> str:
    return quote(value, safe="-_.~")


def _sign(cfg: R2Config, now: dt.datetime, canonical: str) -> str:
    amz_date, scope = _dates(now)
    to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = f"AWS4{cfg.secret_access_key}".encode()
    for part in (now.strftime("%Y%m%d"), _REGION, _SERVICE, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
