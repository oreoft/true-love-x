# -*- coding: utf-8 -*-
"""
本机的 tailnet 地址

server 只通过 tailnet 回调 base，所以 base 报给 server 的回调地址只能用 tailnet 地址（100.64.0.0/10）。
"""

import ipaddress
import socket

TAILNET = ipaddress.ip_network("100.64.0.0/10")
# Tailscale 的 MagicDNS 地址，只用来让系统选出走 tailnet 的本机地址，UDP connect 不会真的发包
_TAILNET_PROBE = ("100.100.100.100", 53)


def tailnet_ip() -> str:
    """
    本机在 tailnet 里的地址

    Raises:
        RuntimeError: 本机没有连上 tailnet
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(_TAILNET_PROBE)
            ip = probe.getsockname()[0]
        except OSError as e:
            raise RuntimeError(f"no route to the tailnet: {e}") from e
    if ipaddress.ip_address(ip) not in TAILNET:
        raise RuntimeError(f"local address {ip} is not a tailnet address, is Tailscale running?")
    return ip
