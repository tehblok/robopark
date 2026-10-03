"""Read Tracker through Robopark's authenticated internal gateway.

The Telegram service never owns a Tracker token or contacts Startrek directly.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_TIMEOUT = 35
DEFAULT_PER_PAGE = 50
DEFAULT_MAX_PAGES = 10
MAX_CHANGELOG_ENTRIES = 200
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_KEY_BYTES = 256


class TrackerConfigError(RuntimeError):
    pass


def reset_client_cache() -> None:
    """Retained for the old Telegram admin command; credentials are never cached."""


def _bridge_key() -> str:
    location = os.environ.get("ROBOPARK_BOT_BRIDGE_KEY_FILE", "").strip()
    if not location:
        raise TrackerConfigError("Не настроен внутренний доступ к Robopark")
    path = Path(location)
    if path.is_symlink():
        raise TrackerConfigError("Некорректный файл доступа к Robopark")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise TrackerConfigError("Некорректные права файла доступа к Robopark")
        raw = os.read(descriptor, MAX_KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if not raw or len(raw) > MAX_KEY_BYTES:
        raise TrackerConfigError("Некорректный файл доступа к Robopark")
    try:
        key = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise TrackerConfigError("Некорректный файл доступа к Robopark") from exc
    if not key:
        raise TrackerConfigError("Некорректный файл доступа к Robopark")
    return key


def _api_base() -> str:
    return os.environ.get("ROBOPARK_BOT_API_BASE", "http://api:8000/internal/bot/tracker").rstrip("/")


def _request(method: str, suffix: str, *, payload: dict[str, Any] | None = None) -> Any:
    try:
        response = requests.request(
            method,
            f"{_api_base()}/{suffix}",
            headers={"X-Robopark-Bot-Key": _bridge_key()},
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
    except TrackerConfigError:
        raise
    except (OSError, requests.RequestException) as exc:
        raise TrackerConfigError("Нет связи с Robopark API") from exc
    if not response.ok:
        raise TrackerConfigError(f"Robopark API: {response.status_code}")
    body = getattr(response, "content", None)
    if body is not None and len(body) > MAX_RESPONSE_BYTES:
        raise TrackerConfigError("Ответ Robopark API слишком велик")
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise TrackerConfigError("Некорректный ответ Robopark API") from exc


def issue_to_dict(issue: Any) -> dict:
    return issue if isinstance(issue, dict) else {}


def _order_list(order: str | list[str] | None) -> list[str]:
    if order is None:
        return ["+created"]
    if isinstance(order, str):
        return [order]
    return list(order)


def search_issues(
    query: str,
    *,
    order: str | list[str] = "+created",
    per_page: int = DEFAULT_PER_PAGE,
    max_pages: int | None = None,
) -> tuple[list[dict], str | None]:
    try:
        pages = DEFAULT_MAX_PAGES if max_pages is None else int(max_pages)
        if not 1 <= pages <= 40 or not 1 <= int(per_page) <= 100:
            raise TrackerConfigError("Некорректный размер выборки Tracker")
        result = _request(
            "POST",
            "search",
            payload={"query": query, "order": _order_list(order), "per_page": int(per_page), "max_pages": pages},
        )
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TrackerConfigError("Некорректный ответ Robopark API")
        return result, None
    except TrackerConfigError as exc:
        return [], str(exc)


def get_issue(issue_key: str) -> tuple[dict | None, str | None]:
    try:
        result = _request("GET", f"issue/{quote(issue_key, safe='')}")
        if not isinstance(result, dict):
            raise TrackerConfigError("Некорректный ответ Robopark API")
        return result, None
    except TrackerConfigError as exc:
        return None, str(exc)


def get_issue_links(issue_key: str) -> tuple[list[dict], str | None]:
    try:
        result = _request("GET", f"issue/{quote(issue_key, safe='')}/links")
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TrackerConfigError("Некорректный ответ Robopark API")
        return result, None
    except TrackerConfigError as exc:
        return [], str(exc)


def get_status_changelog(issue_key: str) -> list[dict]:
    result = _request("GET", f"issue/{quote(issue_key, safe='')}/status-changelog")
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise TrackerConfigError("Некорректный ответ Robopark API")
    return result[:MAX_CHANGELOG_ENTRIES]
