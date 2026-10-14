import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from threading import Lock

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return "$".join(["pbkdf2_sha256", str(ITERATIONS), salt, digest])


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Single-process limit. Replace with Redis before multi-worker exposure."""

    def __init__(self, maximum=10, window=60, capacity=4096):
        self.maximum, self.window, self.capacity = maximum, window, capacity
        self.entries = OrderedDict()
        self.lock = Lock()

    def allow(self, key: str) -> bool:
        with self.lock:
            now = time.monotonic()
            for old_key, (start, _) in list(self.entries.items()):
                if now - start >= self.window:
                    del self.entries[old_key]
            if key not in self.entries and len(self.entries) >= self.capacity:
                return False
            start, count = self.entries.get(key, (now, 0))
            if count >= self.maximum:
                return False
            self.entries[key] = (start, count + 1)
            return True
