"""Per-user AI configuration with OS-protected credential persistence.

The desktop product uses one OpenAI-compatible model for content generation,
optimization and controlled AI-visibility sampling.  Endpoint, model and
provider labels are not secrets and may be stored as JSON.  The API key is
never persisted directly: on Windows it is protected with DPAPI for the
current user before the atomic config file is written.

Non-Windows runtimes deliberately fail closed unless a protector is injected
(for example by a platform integration or unit test).  Server deployments keep
their existing ``AI_*`` environment-variable configuration path.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import stat
import tempfile
import threading
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from services.ai_service import (
    AIConfigurationError,
    AISettings,
    provider_label_for_settings,
)


SCHEMA_VERSION = 1
MAX_PROVIDER_LENGTH = 64
_DPAPI_DESCRIPTION = "GEO Content Platform unified AI credential"
_DPAPI_ENTROPY = b"geo-content-platform:unified-ai-config:v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class UnifiedAIConfigError(RuntimeError):
    """Base class for expected unified-config failures."""


class UnifiedAIConfigStorageUnavailableError(UnifiedAIConfigError):
    """Raised when the host cannot safely protect an API key."""


class UnifiedAIConfigDataError(UnifiedAIConfigError):
    """Raised when the persisted config is unreadable or invalid."""


class SecretProtector(Protocol):
    """Minimal platform secret-protection contract."""

    name: str

    def protect(self, plaintext: bytes) -> bytes:
        ...

    def unprotect(self, ciphertext: bytes) -> bytes:
        ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        ),
        buffer,
    )


class WindowsDPAPIProtector:
    """Protect secrets for the currently signed-in Windows user via DPAPI."""

    name = "windows_dpapi_current_user"

    def __init__(self) -> None:
        if os.name != "nt":
            raise UnifiedAIConfigStorageUnavailableError(
                "当前系统不支持 Windows DPAPI"
            )
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self._kernel32.LocalFree.restype = wintypes.HLOCAL

    def protect(self, plaintext: bytes) -> bytes:
        if not isinstance(plaintext, bytes) or not plaintext:
            raise UnifiedAIConfigDataError("待保护的 AI 凭据不能为空")
        input_blob, input_buffer = _blob(plaintext)
        entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
        output_blob = _DataBlob()
        try:
            accepted = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                _DPAPI_DESCRIPTION,
                ctypes.byref(entropy_blob),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            # Keep the input buffers alive until the native call completes.
            _ = (input_buffer, entropy_buffer)
            if not accepted:
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        except OSError as exc:
            raise UnifiedAIConfigStorageUnavailableError(
                "Windows 无法保护 AI 凭据"
            ) from exc
        finally:
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, wintypes.HLOCAL)
                )

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not isinstance(ciphertext, bytes) or not ciphertext:
            raise UnifiedAIConfigDataError("AI 凭据密文为空")
        input_blob, input_buffer = _blob(ciphertext)
        entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        try:
            accepted = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                ctypes.byref(description),
                ctypes.byref(entropy_blob),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            _ = (input_buffer, entropy_buffer)
            if not accepted:
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        except OSError as exc:
            raise UnifiedAIConfigDataError(
                "AI 凭据无法由当前 Windows 用户解密"
            ) from exc
        finally:
            if description:
                self._kernel32.LocalFree(
                    ctypes.cast(description, wintypes.HLOCAL)
                )
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, wintypes.HLOCAL)
                )


def create_platform_secret_protector() -> SecretProtector | None:
    """Return the host protector, or ``None`` when secure storage is absent."""

    if os.name != "nt":
        return None
    try:
        return WindowsDPAPIProtector()
    except (OSError, UnifiedAIConfigStorageUnavailableError):
        return None


@dataclass(frozen=True)
class UnifiedAIConfigStatus:
    configured: bool
    save_supported: bool
    provider: str
    base_url: str
    model: str
    api_key_configured: bool
    credential_storage: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        """Return only fields that are safe for an authenticated API response."""

        return {
            "configured": self.configured,
            "save_supported": self.save_supported,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": self.api_key_configured,
            "credential_storage": self.credential_storage,
            "updated_at": self.updated_at,
        }


class UnifiedAIConfigService:
    """Own one protected, per-user OpenAI-compatible model configuration."""

    def __init__(
        self,
        config_path: str | os.PathLike[str],
        *,
        protector: SecretProtector | None = None,
        use_platform_protector: bool = True,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.protector = (
            create_platform_secret_protector()
            if protector is None and use_platform_protector
            else protector
        )
        self._lock = threading.RLock()

    def status(self) -> UnifiedAIConfigStatus:
        with self._lock:
            if not self.config_path.exists():
                return UnifiedAIConfigStatus(
                    configured=False,
                    save_supported=self.protector is not None,
                    provider="",
                    base_url="",
                    model="",
                    api_key_configured=False,
                    credential_storage=(
                        self.protector.name if self.protector is not None else "unavailable"
                    ),
                    updated_at="",
                )
            payload = self._read_unlocked()
            credential = payload["credential"]
            return UnifiedAIConfigStatus(
                configured=True,
                save_supported=self.protector is not None,
                provider=payload["provider"],
                base_url=payload["base_url"],
                model=payload["model"],
                api_key_configured=bool(credential["protected_data"]),
                credential_storage=credential["protection"],
                updated_at=payload["updated_at"],
            )

    def load_settings(self) -> AISettings:
        with self._lock:
            if not self.config_path.exists():
                raise AIConfigurationError("尚未保存统一 AI 配置")
            payload = self._read_unlocked()
            protector = self._require_matching_protector(payload)
            try:
                ciphertext = base64.b64decode(
                    payload["credential"]["protected_data"],
                    validate=True,
                )
                plaintext = protector.unprotect(ciphertext)
                api_key = plaintext.decode("utf-8")
            except (ValueError, UnicodeError, UnifiedAIConfigError) as exc:
                raise UnifiedAIConfigDataError("统一 AI 凭据无法解密") from exc
            return AISettings.from_mapping(
                {
                    "base_url": payload["base_url"],
                    "api_key": api_key,
                    "model": payload["model"],
                    "provider": payload["provider"],
                }
            )

    def save(
        self,
        *,
        provider: object,
        base_url: object,
        model: object,
        api_key: object = None,
    ) -> UnifiedAIConfigStatus:
        """Validate and atomically save config with a protected credential."""

        with self._lock:
            protector = self.protector
            if protector is None:
                raise UnifiedAIConfigStorageUnavailableError(
                    "当前运行环境没有可用的系统级凭据保护，未保存 API Key"
                )

            normalized_api_key = str(api_key or "").strip()
            if not normalized_api_key and self.config_path.exists():
                normalized_api_key = self.load_settings().api_key
            settings = AISettings.from_mapping(
                {
                    "base_url": base_url,
                    "api_key": normalized_api_key,
                    "model": model,
                    "provider": provider,
                }
            )
            normalized_provider = _normalize_provider(provider)
            if not normalized_provider:
                normalized_provider = provider_label_for_settings(settings)
            now = _utc_now()
            created_at = now
            if self.config_path.exists():
                try:
                    created_at = self._read_unlocked()["created_at"]
                except UnifiedAIConfigDataError:
                    # A complete replacement with a new credential is a safe
                    # recovery path for a corrupted local file. Reusing an old
                    # key already failed above before reaching this branch.
                    if not str(api_key or "").strip():
                        raise
            protected_data = protector.protect(settings.api_key.encode("utf-8"))
            payload = {
                "schema_version": SCHEMA_VERSION,
                "provider": normalized_provider,
                "base_url": settings.base_url,
                "model": settings.model,
                "credential": {
                    "protection": protector.name,
                    "protected_data": base64.b64encode(protected_data).decode("ascii"),
                },
                "created_at": created_at,
                "updated_at": now,
            }
            self._write_atomic_unlocked(payload)
            return self.status()

    def clear(self) -> UnifiedAIConfigStatus:
        with self._lock:
            try:
                self.config_path.unlink(missing_ok=True)
            except OSError as exc:
                raise UnifiedAIConfigDataError("无法清除统一 AI 配置") from exc
            return self.status()

    def _require_matching_protector(self, payload: dict[str, Any]) -> SecretProtector:
        protector = self.protector
        if protector is None:
            raise UnifiedAIConfigStorageUnavailableError(
                "当前运行环境无法解密已保存的 AI 凭据"
            )
        stored_name = payload["credential"]["protection"]
        if protector.name != stored_name:
            raise UnifiedAIConfigStorageUnavailableError(
                "已保存 AI 凭据的保护方式与当前系统不匹配"
            )
        return protector

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            return _validate_payload(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise UnifiedAIConfigDataError("统一 AI 配置文件无效") from exc

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
            descriptor, raw_path = tempfile.mkstemp(
                dir=str(self.config_path.parent),
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(raw_path)
            try:
                os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
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
            raise UnifiedAIConfigDataError("无法安全写入统一 AI 配置") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _normalize_provider(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    if len(normalized) > MAX_PROVIDER_LENGTH or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]*", normalized
    ):
        raise AIConfigurationError("统一 AI 服务商标识格式无效")
    return normalized


def _validate_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    provider = _normalize_provider(value.get("provider"))
    if not provider:
        raise ValueError("provider is missing")
    base_url = value.get("base_url")
    model = value.get("model")
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url is missing")
    if not isinstance(model, str) or not model:
        raise ValueError("model is missing")
    try:
        validated_settings = AISettings.from_mapping(
            {
                "base_url": base_url,
                "api_key": "protected-credential-placeholder",
                "model": model,
                "provider": provider,
            }
        )
    except AIConfigurationError as exc:
        raise ValueError("stored AI endpoint or model is invalid") from exc
    credential = value.get("credential")
    if not isinstance(credential, dict):
        raise ValueError("credential is missing")
    protection = credential.get("protection")
    protected_data = credential.get("protected_data")
    if not isinstance(protection, str) or not protection:
        raise ValueError("protection is missing")
    if not isinstance(protected_data, str) or not protected_data:
        raise ValueError("protected_data is missing")
    try:
        decoded = base64.b64decode(protected_data, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("protected_data is invalid") from exc
    if not decoded:
        raise ValueError("protected_data is empty")
    created_at = value.get("created_at")
    updated_at = value.get("updated_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("created_at is missing")
    if not isinstance(updated_at, str) or not updated_at:
        raise ValueError("updated_at is missing")
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "base_url": validated_settings.base_url,
        "model": validated_settings.model,
        "credential": {
            "protection": protection,
            "protected_data": protected_data,
        },
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "SecretProtector",
    "UnifiedAIConfigDataError",
    "UnifiedAIConfigError",
    "UnifiedAIConfigService",
    "UnifiedAIConfigStatus",
    "UnifiedAIConfigStorageUnavailableError",
    "WindowsDPAPIProtector",
    "create_platform_secret_protector",
]
