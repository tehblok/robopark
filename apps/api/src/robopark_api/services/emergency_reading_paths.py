"""One sensitive-path policy for discovery, writes and persisted projection."""

from robopark_api.services.diagnostic_rules import diagnostic_source_parts


def safe_reading_key(key: str) -> bool:
    return "." not in key and not any(
        word in key.casefold() for word in ("hud", "sdcoptions", "cookie", "token")
    )


def safe_reading_path(path: str) -> bool:
    parts = diagnostic_source_parts(path)
    return parts is not None and len(parts) <= 12 and all(safe_reading_key(part) for part in parts)
