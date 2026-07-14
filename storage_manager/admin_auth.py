from __future__ import annotations

import hashlib
import hmac


_ADMIN_PIN_SALT = bytes.fromhex("e0d18898b28fd28f357eb2f4b3f69a52")
_ADMIN_PIN_ITERATIONS = 240_000
_ADMIN_PIN_DIGEST = bytes.fromhex(
    "e63f4f0d3a3c25d850fce7170dc5a68b494ef56bde13a9acafd97c82dc058b29"
)


def verify_admin_pin(candidate: str) -> bool:
    if not isinstance(candidate, str) or not candidate:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        candidate.encode("utf-8"),
        _ADMIN_PIN_SALT,
        _ADMIN_PIN_ITERATIONS,
    )
    return hmac.compare_digest(digest, _ADMIN_PIN_DIGEST)
