"""Persistent local authentication primitives for the Windows desktop app.

This module deliberately has no Flask or desktop-runtime dependencies.  The
desktop host can obtain a stable session-signing secret before importing the
web application, while the API layer can use the same store for first-run
password setup and login verification.

Only salted password digests are written to disk.  Password values are never
logged, serialized, or retained on the service instance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PASSWORD_ALGORITHM = "pbkdf2_hmac_sha256"
DEFAULT_PBKDF2_ITERATIONS = 600_000
MIN_PBKDF2_ITERATIONS = 100_000
MAX_PBKDF2_ITERATIONS = 2_000_000
MIN_PASSWORD_LENGTH = 12
PASSPHRASE_LENGTH = 16
MAX_PASSWORD_LENGTH = 256
SALT_BYTES = 16
SESSION_SECRET_BYTES = 48


class LocalAuthError(RuntimeError):
    """Base class for expected local-authentication errors."""


class LocalAuthConfigurationError(LocalAuthError):
    """Raised when the persisted authentication file is invalid or unreadable."""


class PasswordPolicyError(ValueError):
    """Raised when a proposed password does not meet the local policy."""

    def __init__(self, message: str, *, code: str = "weak_password"):
        super().__init__(message)
        self.code = code


class PasswordAlreadyConfiguredError(LocalAuthError):
    """Raised when unauthenticated first-run setup is attempted more than once."""


class InvalidCurrentPasswordError(LocalAuthError):
    """Raised when an authenticated password change has invalid credentials."""


@dataclass(frozen=True)
class LocalAuthStatus:
    """Non-sensitive state suitable for an authentication status endpoint."""

    configured: bool
    setup_required: bool
    created_at: str
    updated_at: str


def validate_password(password: str) -> None:
    """Validate a desktop administrator password without transforming it.

    A 12--15 character password needs three character classes.  A password or
    passphrase of at least 16 characters is accepted with one class because
    length contributes substantial entropy and this keeps non-Latin phrases
    usable.  Leading/trailing whitespace and control characters are rejected
    to prevent invisible copy/paste mistakes.
    """

    if not isinstance(password, str):
        raise PasswordPolicyError("密码必须是文本", code="invalid_type")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符",
            code="too_short",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"密码不能超过 {MAX_PASSWORD_LENGTH} 个字符",
            code="too_long",
        )
    if password != password.strip():
        raise PasswordPolicyError(
            "密码首尾不能包含空格",
            code="surrounding_whitespace",
        )
    if any(unicodedata.category(character).startswith("C") for character in password):
        raise PasswordPolicyError(
            "密码不能包含控制字符",
            code="control_character",
        )
    if len(set(password)) < 4:
        raise PasswordPolicyError(
            "密码包含的不同字符过少",
            code="low_variety",
        )

    character_classes = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
            any(
                character.isalpha()
                and not character.islower()
                and not character.isupper()
                for character in password
            ),
        )
    )
    if len(password) < PASSPHRASE_LENGTH and character_classes < 3:
        raise PasswordPolicyError(
            "少于 16 个字符时，密码需包含字母大小写、数字或符号中的至少三类",
            code="insufficient_character_classes",
        )


class LocalAuthService:
    """Own the desktop authentication file and password lifecycle.

    Public integration surface:

    * :meth:`get_session_secret` initializes the file and returns the stable
      Flask session-signing secret.
    * :meth:`status` reports whether first-run setup is required.
    * :meth:`set_initial_password` performs the one-time unauthenticated setup.
    * :meth:`verify_password` checks login attempts in constant time.
    * :meth:`change_password` rotates the digest after verifying the old value.
    """

    def __init__(
        self,
        config_path: str | os.PathLike[str],
        *,
        iterations: int = DEFAULT_PBKDF2_ITERATIONS,
    ):
        normalized_iterations = int(iterations)
        if not MIN_PBKDF2_ITERATIONS <= normalized_iterations <= MAX_PBKDF2_ITERATIONS:
            raise ValueError(
                "PBKDF2 iterations must be between "
                f"{MIN_PBKDF2_ITERATIONS} and {MAX_PBKDF2_ITERATIONS}"
            )
        self.config_path = Path(config_path).expanduser().resolve()
        self.iterations = normalized_iterations
        self._lock = threading.RLock()

    def get_session_secret(self) -> str:
        """Return a stable random secret, creating the config on first use."""

        with self._lock:
            payload = self._load_or_initialize_unlocked()
            return str(payload["session_secret"])

    def status(self) -> LocalAuthStatus:
        """Return non-sensitive setup state, initializing storage if needed."""

        with self._lock:
            payload = self._load_or_initialize_unlocked()
            configured = payload["password"] is not None
            return LocalAuthStatus(
                configured=configured,
                setup_required=not configured,
                created_at=str(payload["created_at"]),
                updated_at=str(payload["updated_at"]),
            )

    def set_initial_password(self, password: str) -> LocalAuthStatus:
        """Persist the first administrator password exactly once.

        Subsequent changes must use :meth:`change_password`, so an attacker who
        can only reach the setup route cannot overwrite existing credentials.
        """

        validate_password(password)
        with self._lock:
            payload = self._load_or_initialize_unlocked()
            if payload["password"] is not None:
                raise PasswordAlreadyConfiguredError(
                    "管理员密码已经设置，请使用修改密码流程"
                )
            payload["password"] = self._password_record(password)
            payload["updated_at"] = _utc_now()
            self._write_atomic_unlocked(payload)
            return self._status_from_payload(payload)

    def verify_password(self, password: object) -> bool:
        """Verify a login password without exposing parsing or policy errors."""

        if not isinstance(password, str) or not password:
            return False
        # Bound attacker-controlled work and memory before encoding.
        if len(password) > MAX_PASSWORD_LENGTH:
            return False

        with self._lock:
            payload = self._load_or_initialize_unlocked()
            record = payload["password"]
            if record is None:
                return False
            return self._verify_record(password, record)

    def change_password(self, current_password: object, new_password: str) -> LocalAuthStatus:
        """Replace the digest after validating the current administrator password."""

        validate_password(new_password)
        with self._lock:
            payload = self._load_or_initialize_unlocked()
            record = payload["password"]
            if record is None or not self._verify_record(current_password, record):
                raise InvalidCurrentPasswordError("当前管理员密码不正确")
            payload["password"] = self._password_record(new_password)
            payload["updated_at"] = _utc_now()
            self._write_atomic_unlocked(payload)
            return self._status_from_payload(payload)

    def _load_or_initialize_unlocked(self) -> dict[str, Any]:
        if self.config_path.exists():
            return self._read_unlocked()

        now = _utc_now()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_secret": secrets.token_urlsafe(SESSION_SECRET_BYTES),
            "password": None,
            "created_at": now,
            "updated_at": now,
        }
        self._write_atomic_unlocked(payload)
        return payload

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw_payload = self.config_path.read_text(encoding="utf-8")
            payload = json.loads(raw_payload)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalAuthConfigurationError(
                f"无法读取本地认证配置：{self.config_path}"
            ) from exc

        try:
            return self._validate_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalAuthConfigurationError(
                f"本地认证配置格式无效：{self.config_path}"
            ) from exc

    def _validate_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("root must be an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")

        session_secret = payload["session_secret"]
        if not isinstance(session_secret, str) or len(session_secret) < 43:
            raise ValueError("invalid session secret")
        try:
            decoded_secret = base64.urlsafe_b64decode(
                session_secret + "=" * (-len(session_secret) % 4)
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid session secret encoding") from exc
        if len(decoded_secret) < 32:
            raise ValueError("session secret is too short")

        created_at = payload["created_at"]
        updated_at = payload["updated_at"]
        if not isinstance(created_at, str) or not created_at:
            raise ValueError("invalid created_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError("invalid updated_at")

        password_record = payload["password"]
        if password_record is not None:
            self._validate_password_record(password_record)

        return payload

    @staticmethod
    def _validate_password_record(record: Any) -> None:
        if not isinstance(record, dict):
            raise TypeError("password must be an object or null")
        if record.get("algorithm") != PASSWORD_ALGORITHM:
            raise ValueError("unsupported password algorithm")
        iterations = record.get("iterations")
        if isinstance(iterations, bool) or not isinstance(iterations, int):
            raise TypeError("iterations must be an integer")
        if not MIN_PBKDF2_ITERATIONS <= iterations <= MAX_PBKDF2_ITERATIONS:
            raise ValueError("unsafe PBKDF2 iteration count")

        salt = _decode_base64(record.get("salt"), name="salt")
        digest = _decode_base64(record.get("digest"), name="digest")
        if not SALT_BYTES <= len(salt) <= 64:
            raise ValueError("invalid salt length")
        if len(digest) != hashlib.sha256().digest_size:
            raise ValueError("invalid digest length")

    def _password_record(self, password: str) -> dict[str, Any]:
        salt = secrets.token_bytes(SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self.iterations,
        )
        return {
            "algorithm": PASSWORD_ALGORITHM,
            "iterations": self.iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "digest": base64.b64encode(digest).decode("ascii"),
        }

    @staticmethod
    def _verify_record(password: object, record: dict[str, Any]) -> bool:
        if not isinstance(password, str) or not password:
            return False
        if len(password) > MAX_PASSWORD_LENGTH:
            return False
        salt = _decode_base64(record["salt"], name="salt")
        expected_digest = _decode_base64(record["digest"], name="digest")
        supplied_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(record["iterations"]),
        )
        return hmac.compare_digest(supplied_digest, expected_digest)

    @staticmethod
    def _status_from_payload(payload: dict[str, Any]) -> LocalAuthStatus:
        configured = payload["password"] is not None
        return LocalAuthStatus(
            configured=configured,
            setup_required=not configured,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )

    def _write_atomic_unlocked(self, payload: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

        temporary_path: Path | None = None
        try:
            descriptor, raw_temporary_path = tempfile.mkstemp(
                dir=str(self.config_path.parent),
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(raw_temporary_path)
            try:
                os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                # Windows ACLs are inherited from the containing user profile.
                pass
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.config_path)
            temporary_path = None
            try:
                os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        except OSError as exc:
            raise LocalAuthConfigurationError(
                f"无法安全写入本地认证配置：{self.config_path}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _decode_base64(value: object, *, name: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty string")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid {name} encoding") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "DEFAULT_PBKDF2_ITERATIONS",
    "InvalidCurrentPasswordError",
    "LocalAuthConfigurationError",
    "LocalAuthError",
    "LocalAuthService",
    "LocalAuthStatus",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PasswordAlreadyConfiguredError",
    "PasswordPolicyError",
    "validate_password",
]
