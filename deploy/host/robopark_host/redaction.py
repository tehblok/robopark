"""Remove secret values before state is exposed in logs or diagnostics."""

_SECRET_MARKERS = (
    "token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "access_key",
)
_REDACTED = "[REDACTED]"


def _normalize_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


_NORMALIZED_SECRET_MARKERS = tuple(_normalize_key(marker) for marker in _SECRET_MARKERS)


def _is_secret_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return any(marker in normalized for marker in _NORMALIZED_SECRET_MARKERS)


def redact(value: object) -> object:
    """Copy structured values while replacing values stored under secret keys."""

    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_secret_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, set):
        return {redact(item) for item in value}
    return value
