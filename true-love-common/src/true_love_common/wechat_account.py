# -*- coding: utf-8 -*-
"""
读出这台电脑上当前登录的微信号

新版微信客户端上 SDK 的 GetMyInfo 拿不到 wxid，这里改读微信的数据目录：
Documents\\xwechat_files 下每个登录过的号有一个 wxid_xxx_yyyy 目录，微信在线时会不断写里面的数据库文件，
所以最近有写入的目录就是当前登录的号。目录结构没有文档，判断不出来时直接报错，不去猜。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# wxid_abc123def456_8537 -> wxid_abc123def456
_ACCOUNT_DIR = re.compile(r"^(wxid_[0-9a-z]+)_[0-9a-z]{4}$", re.IGNORECASE)


class WxidNotFound(RuntimeError):
    """判断不出当前登录的是哪个号"""


def default_files_dir() -> Path:
    return Path.home() / "Documents" / "xwechat_files"


def _last_write(account_dir: Path) -> float:
    """目录里文件最近一次写入的时间，数据库都在 db_storage 下"""
    latest = account_dir.stat().st_mtime
    storage = account_dir / "db_storage"
    if storage.is_dir():
        for path in storage.rglob("*"):
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
    return latest


def current_wxid(files_dir: Optional[Path] = None) -> str:
    """
    当前登录的微信号（不带目录名末尾的 _xxxx），和 GetMyInfo 返回的 id 格式一致

    Raises:
        WxidNotFound: 没有登录过的号，或者几个号最近写入的时间一样分不出来
    """
    files_dir = files_dir or default_files_dir()
    if not files_dir.is_dir():
        raise WxidNotFound(f"找不到微信数据目录: {files_dir}")

    accounts = []
    for child in files_dir.iterdir():
        match = _ACCOUNT_DIR.match(child.name)
        if match and child.is_dir():
            accounts.append((_last_write(child), match.group(1), child.name))
    if not accounts:
        raise WxidNotFound(f"{files_dir} 下没有 wxid_ 开头的账号目录")

    accounts.sort(reverse=True)
    if len(accounts) > 1 and accounts[0][0] == accounts[1][0]:
        raise WxidNotFound(f"{accounts[0][2]} 和 {accounts[1][2]} 最近写入的时间相同，分不出当前登录的号")
    return accounts[0][1]
