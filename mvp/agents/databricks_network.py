from __future__ import annotations

import ipaddress
import socket
import threading
from urllib.parse import urlparse

_LOCK = threading.Lock()
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_PATCH_INSTALLED = False
_HOST_OVERRIDES: dict[str, list[tuple[int, str]]] = {}


def _normalize_hostname(host: str) -> str:
    parsed = urlparse(host if "://" in host else f"https://{host}")
    return (parsed.hostname or host).strip().lower()


def _is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _derive_privatelink_hostname(hostname: str) -> str | None:
    if hostname.endswith(".privatelink.azuredatabricks.net"):
        return hostname
    suffix = ".azuredatabricks.net"
    if not hostname.endswith(suffix):
        return None
    return f"{hostname[:-len(suffix)]}.privatelink.azuredatabricks.net"


def _patched_getaddrinfo(
    host: str | bytes | None,
    port: str | int | None,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
):
    hostname = host.decode() if isinstance(host, bytes) else host
    if not isinstance(hostname, str):
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)

    normalized = hostname.strip().lower()
    with _LOCK:
        overrides = list(_HOST_OVERRIDES.get(normalized, []))

    if not overrides:
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)

    results = []
    for resolved_family, ip in overrides:
        if family not in (0, resolved_family):
            continue
        try:
            results.extend(
                _ORIGINAL_GETADDRINFO(
                    ip,
                    port,
                    resolved_family,
                    type,
                    proto,
                    flags,
                )
            )
        except OSError:
            continue

    return results or _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)


def enable_private_databricks_resolution(host: str) -> str | None:
    hostname = _normalize_hostname(host)
    privatelink_hostname = _derive_privatelink_hostname(hostname)
    if not privatelink_hostname:
        return None

    try:
        resolved = _ORIGINAL_GETADDRINFO(privatelink_hostname, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None

    private_ips: list[tuple[int, str]] = []
    for family, _, _, _, sockaddr in resolved:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = sockaddr[0]
        if _is_private_ip(ip):
            private_ips.append((family, ip))

    if not private_ips:
        return None

    global _PATCH_INSTALLED
    with _LOCK:
        _HOST_OVERRIDES[hostname] = private_ips
        _HOST_OVERRIDES[privatelink_hostname] = private_ips
        if not _PATCH_INSTALLED:
            socket.getaddrinfo = _patched_getaddrinfo
            _PATCH_INSTALLED = True
    return private_ips[0][1]
