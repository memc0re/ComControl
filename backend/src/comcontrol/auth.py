"""Local administrator account, sessions and login rate limiting.

* Passwords: Argon2id with an individual salt per hash (argon2-cffi).
* Sessions: random opaque token in an HttpOnly cookie; the server stores only
  its SHA-256 hash.  Each session has its own CSRF token.  Sessions expire
  a fixed time after login; logout deletes them immediately.
* The default account admin/admin is created only if no administrator exists
  at all; restarts and updates never reset a changed password.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .db import Database, iso, utc_now

log = logging.getLogger("comcontrol.auth")

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    token_hash: str
    csrf_token: str
    created_at: float
    expires_at: float


class PasswordPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthService:
    def __init__(
        self,
        db: Database,
        hasher: PasswordHasher,
        *,
        session_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.hasher = hasher
        self.session_seconds = session_seconds
        self.clock = clock
        # Verified against when the username is wrong, so timing does not
        # reveal whether the username exists.
        self._dummy_hash = hasher.hash(secrets.token_hex(16))

    def ensure_default_admin(self) -> bool:
        """Create admin/admin only if no administrator exists.  Returns True if created."""
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM admin").fetchone():
                return False
            c.execute(
                "INSERT INTO admin (id, username, password_hash, is_default_password, updated_at)"
                " VALUES (1, ?, ?, 1, ?)",
                (DEFAULT_USERNAME, self.hasher.hash(DEFAULT_PASSWORD), iso(utc_now())),
            )
        self.db.add_event("admin_created", username=DEFAULT_USERNAME)
        log.warning("created default administrator account; change its password")
        return True

    def admin(self) -> dict:
        row = self.db.one("SELECT username, is_default_password FROM admin WHERE id = 1")
        assert row is not None, "administrator missing"
        return {"username": row["username"], "password_is_default": bool(row["is_default_password"])}

    def _verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def check_credentials(self, username: str, password: str) -> bool:
        row = self.db.one("SELECT username, password_hash FROM admin WHERE id = 1")
        if row is None or len(password) > MAX_PASSWORD_LENGTH:
            self._verify(self._dummy_hash, "x")
            return False
        name_ok = hmac.compare_digest(row["username"].encode(), username.encode())
        pw_ok = self._verify(row["password_hash"] if name_ok else self._dummy_hash, password)
        if name_ok and pw_ok and self.hasher.check_needs_rehash(row["password_hash"]):
            with self.db.tx() as c:
                c.execute("UPDATE admin SET password_hash = ? WHERE id = 1", (self.hasher.hash(password),))
        return name_ok and pw_ok

    # --- sessions -----------------------------------------------------
    def create_session(self) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        session = Session(_hash_token(token), secrets.token_urlsafe(32), now, now + self.session_seconds)
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            c.execute(
                "INSERT INTO sessions (token_hash, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (session.token_hash, session.csrf_token, session.created_at, session.expires_at),
            )
        return token, session

    def get_session(self, token: str | None) -> Session | None:
        if not token or len(token) > 128:
            return None
        row = self.db.one("SELECT * FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        if row is None:
            return None
        if row["expires_at"] <= self.clock():
            with self.db.tx() as c:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            return None
        return Session(row["token_hash"], row["csrf_token"], row["created_at"], row["expires_at"])

    def end_session(self, session: Session) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (session.token_hash,))

    def change_password(self, session: Session, current: str, new: str) -> None:
        """Raises PasswordPolicyError; ends all other sessions on success."""
        row = self.db.one("SELECT password_hash FROM admin WHERE id = 1")
        assert row is not None
        if len(current) > MAX_PASSWORD_LENGTH or not self._verify(row["password_hash"], current):
            raise PasswordPolicyError("current_password_wrong")
        if len(new) < MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError("password_too_short")
        if len(new) > MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError("password_too_long")
        if new == current or new == DEFAULT_PASSWORD:
            raise PasswordPolicyError("password_not_changed")
        with self.db.tx() as c:
            c.execute(
                "UPDATE admin SET password_hash = ?, is_default_password = 0, updated_at = ? WHERE id = 1",
                (self.hasher.hash(new), iso(utc_now())),
            )
            c.execute("DELETE FROM sessions WHERE token_hash != ?", (session.token_hash,))
        self.db.add_event("password_changed")
        log.info("administrator password changed")


class LoginRateLimiter:
    """Sliding-window limit of failed logins, per client address and overall.

    The client address is the TCP peer; forwarded headers are not trusted.
    """

    def __init__(
        self,
        max_failures: int,
        window_s: float,
        *,
        global_factor: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.global_max = max_failures * global_factor
        self.clock = clock
        self._per_client: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window_s:
            q.popleft()

    def retry_after(self, client: str) -> float | None:
        """Seconds to wait if the client is currently blocked, else None."""
        now = self.clock()
        with self._lock:
            q = self._per_client.get(client, deque())
            self._prune(q, now)
            self._prune(self._global, now)
            waits = []
            if len(q) >= self.max_failures:
                waits.append(q[0] + self.window_s - now)
            if len(self._global) >= self.global_max:
                waits.append(self._global[0] + self.window_s - now)
            return max(waits) if waits else None

    def failure(self, client: str) -> None:
        now = self.clock()
        with self._lock:
            self._per_client.setdefault(client, deque()).append(now)
            self._global.append(now)
            if len(self._per_client) > 10_000:  # bound memory
                for key in [k for k, q in self._per_client.items() if not q or q[-1] <= now - self.window_s]:
                    del self._per_client[key]

    def success(self, client: str) -> None:
        with self._lock:
            self._per_client.pop(client, None)
