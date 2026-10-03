"""Keep isolated demo Python processes on loopback, including HTTP clients."""

from __future__ import annotations

import errno
import os
import socket

INTEGRATION_HOSTS = frozenset(
    {"st-api.yandex-team.ru", "emergency.sdc.yandex-team.ru"}
)
_resolved_integration_ips: set[str] = set()


def is_loopback_host(host: object) -> bool:
    return str(host).lower() in {"127.0.0.1", "localhost", "::1", "[::1]"}


def _check(address: object) -> None:
    if not isinstance(address, tuple) or not address:
        return
    if is_loopback_host(address[0]):
        return
    if (
        os.environ.get("ROBOPARK_ISOLATED_DEMO_INTEGRATIONS") == "1"
        and len(address) > 1
        and address[1] == 443
        and str(address[0]) in _resolved_integration_ips
    ):
        return
    raise PermissionError(errno.EACCES, "isolated demo permits loopback connections only")


if os.environ.get("ROBOPARK_ISOLATED_DEMO") == "1":
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex
    _getaddrinfo = socket.getaddrinfo
    _gethostbyname = socket.gethostbyname

    def guarded_connect(self: socket.socket, address: object) -> None:
        _check(address)
        _connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        try:
            _check(address)
        except PermissionError:
            return errno.EACCES
        return _connect_ex(self, address)

    def guarded_getaddrinfo(host: object, port: object, *args: object, **kwargs: object):
        if host is None or is_loopback_host(host):
            return _getaddrinfo(host, port, *args, **kwargs)
        if (
            os.environ.get("ROBOPARK_ISOLATED_DEMO_INTEGRATIONS") == "1"
            and str(host).lower() in INTEGRATION_HOSTS
            and port in (443, "443", "https")
        ):
            resolved = _getaddrinfo(host, port, *args, **kwargs)
            _resolved_integration_ips.update(str(item[4][0]) for item in resolved)
            return resolved
        raise PermissionError(errno.EACCES, "isolated demo permits loopback DNS only")

    def guarded_gethostbyname(host: str) -> str:
        if is_loopback_host(host):
            return _gethostbyname(host)
        if (
            os.environ.get("ROBOPARK_ISOLATED_DEMO_INTEGRATIONS") == "1"
            and host.lower() in INTEGRATION_HOSTS
        ):
            address = _gethostbyname(host)
            _resolved_integration_ips.add(address)
            return address
        raise PermissionError(errno.EACCES, "isolated demo permits loopback DNS only")

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
