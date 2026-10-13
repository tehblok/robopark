import base64
import hashlib
import hmac


def derived_key(secret_key: str | None) -> bytes:
    if not secret_key:
        raise RuntimeError("secret_key_required")
    return hmac.new(
        secret_key.encode("utf-8"), b"robopark-emergency-bridge-rpc-v1", hashlib.sha256
    ).digest()


def body_signature(secret_key: str | None, timestamp: str, nonce: str, body: bytes) -> str:
    message = f"{timestamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
    return base64.b64encode(
        hmac.new(derived_key(secret_key), message, hashlib.sha256).digest()
    ).decode("ascii")


def verify_body_signature(
    secret_key: str | None, timestamp: str, nonce: str, body: bytes, signature: str
) -> bool:
    return hmac.compare_digest(body_signature(secret_key, timestamp, nonce, body), signature)
