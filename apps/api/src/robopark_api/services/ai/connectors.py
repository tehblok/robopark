"""Fixed HTTPS destinations, public DNS pinning, no redirects or ambient proxy."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import parse_qsl, quote, urlsplit

import certifi
from fastapi import HTTPException

from robopark_api.crypto import decrypt_secret


class DeliveryFailure(Exception):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.uncertain = uncertain


def validate_url(url):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(
                key.casefold()
                in {
                    "token",
                    "access_token",
                    "api_key",
                    "apikey",
                    "key",
                    "secret",
                    "password",
                    "authorization",
                    "signature",
                    "sig",
                }
                for key, _ in parse_qsl(parsed.query)
            )
            or parsed.fragment
            or parsed.port not in {None, 443}
            or "{" in parsed.netloc + parsed.query
            or "}" in parsed.netloc + parsed.query
            or "{" in parsed.path.replace("{{issue_key}}", "")
            or "}" in parsed.path.replace("{{issue_key}}", "")
            or any(ord(c) <= 32 for c in url)
            or hostname.lower() in {"localhost", "localhost.localdomain"}
            or hostname.lower().endswith((".local", ".internal", ".localhost"))
        ):
            raise ValueError()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            hostname.encode("idna")
        else:
            if not public(address):
                raise ValueError()
        return parsed
    except (ValueError, UnicodeError):
        raise HTTPException(422, "ai_connector_url_invalid") from None


def public(address):
    return (
        address.is_global
        and not (address.is_multicast or address.is_reserved or address.is_unspecified)
        and not getattr(address, "ipv4_mapped", None)
    )


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(
            host, 443, timeout=10, context=ssl.create_default_context(cafile=certifi.where())
        )
        self.address = address

    def connect(self):
        # The TLS certificate and SNI still use the configured hostname. DNS is
        # resolved/validated once; connecting cannot rebind to an internal IP.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def deliver(settings, connector, payload, event_key, event=None):
    parsed = validate_url(connector["url"])
    if "{{issue_key}}" in parsed.path:
        issue_key = (event or {}).get("issue_key")
        if not isinstance(issue_key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", issue_key):
            raise DeliveryFailure("ai_connector_issue_key_missing")
        parsed = parsed._replace(
            path=parsed.path.replace("{{issue_key}}", quote(issue_key, safe=""))
        )
    hostname = parsed.hostname.encode("idna").decode("ascii")
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if (
            not addresses
            or len(addresses) > 32
            or any(not public(ipaddress.ip_address(ip)) for ip in addresses)
        ):
            raise DeliveryFailure("ai_connector_address_denied")
    except (OSError, ValueError) as exc:
        raise DeliveryFailure("ai_connector_dns_failed") from exc
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > 65536:
        raise DeliveryFailure("ai_payload_too_large")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Idempotency-Key": event_key,
    }
    token = decrypt_secret(connector["encrypted_token"], settings.secret_key)
    if token:
        headers["Authorization"] = "Bearer " + token
    connection = PinnedHTTPS(hostname, addresses[0])
    sent = False
    try:
        # Complete TCP/TLS before considering the operation possibly delivered.
        connection.connect()
        sent = True
        connection.request(
            connector["method"],
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
            body=body if connector["method"] != "GET" else None,
            headers=headers,
        )
        response = connection.getresponse()
        deadline = time.monotonic() + 10
        size = 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            part = response.read(4096)
            size += len(part)
            if size > 65536:
                raise DeliveryFailure("ai_connector_response_too_large", uncertain=True)
            if not part:
                break
        if not 200 <= response.status < 300:
            # Includes redirects, which are never followed; response bodies may
            # contain credentials/PII so neither store nor log them.
            raise DeliveryFailure("ai_connector_http_error", uncertain=True)
        return {"http_status": response.status, "response_bytes": size}
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise DeliveryFailure(
            "ai_connector_delivery_unknown" if sent else "ai_connector_unreachable", uncertain=sent
        ) from exc
    finally:
        connection.close()
