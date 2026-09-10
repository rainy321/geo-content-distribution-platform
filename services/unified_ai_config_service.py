"""Per-user AI role configuration with OS-protected credential persistence.

The desktop product keeps ``writer`` as its backwards-compatible default and
can optionally route review/optimization, evaluation, and image-generation to
separate OpenAI-compatible services.  An unconfigured optional role resolves
explicitly to ``writer``; callers can surface that fallback instead of silently
pretending independent models are in use.  Endpoint, model and provider labels
are not secrets and may be stored as JSON.  API keys are never persisted
directly: on Windows they are protected with DPAPI for the current user before
the atomic config file is written.

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


SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
MAX_PROVIDER_LENGTH = 64
AI_ROLE_IDS = ("writer", "reviewer", "evaluator", "image")
OPTIONAL_AI_ROLE_IDS = ("reviewer", "evaluator", "image")
AI_ROLE_ALIASES = {"optimizer": "reviewer"}
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
class UnifiedAIRoleStatus:
    role: str
    configured: bool
    effective_configured: bool
    provider: str
    base_url: str
    model: str
    api_key_configured: bool
    credential_storage: str
    updated_at: str
    fallback: bool
    source_role: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "configured": self.configured,
            "effective_configured": self.effective_configured,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": self.api_key_configured,
            "credential_storage": self.credential_storage,
            "updated_at": self.updated_at,
            "fallback": self.fallback,
            "source_role": self.source_role,
        }


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
    roles: tuple[UnifiedAIRoleStatus, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        """Return only fields that are safe for an authenticated API response."""

        payload = {
            "configured": self.configured,
            "save_supported": self.save_supported,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": self.api_key_configured,
            "credential_storage": self.credential_storage,
            "updated_at": self.updated_at,
        }
        payload["roles"] = {item.role: item.public_dict() for item in self.roles}
        writer = payload["roles"].get("writer")
        reviewer = payload["roles"].get("reviewer")
        if writer and reviewer:
            reviewer["same_as_writer"] = bool(
                reviewer["effective_configured"]
                and writer["effective_configured"]
                and reviewer["provider"] == writer["provider"]
                and reviewer["base_url"] == writer["base_url"]
                and reviewer["model"] == writer["model"]
            )
        return payload


class UnifiedAIConfigService:
    """Own protected, per-user OpenAI-compatible role configurations."""

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
                roles = tuple(
                    _empty_role_status(
                        role,
                        save_supported=self.protector is not None,
                    )
                    for role in AI_ROLE_IDS
                )
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
                    roles=roles,
                )
            payload = self._read_unlocked()
            credential = payload["credential"]
            writer_status = UnifiedAIRoleStatus(
                role="writer",
                configured=True,
                effective_configured=True,
                provider=payload["provider"],
                base_url=payload["base_url"],
                model=payload["model"],
                api_key_configured=bool(credential["protected_data"]),
                credential_storage=credential["protection"],
                updated_at=payload["updated_at"],
                fallback=False,
                source_role="writer",
            )
            optional_statuses = []
            configured_roles = payload.get("roles", {})
            for role in OPTIONAL_AI_ROLE_IDS:
                record = configured_roles.get(role)
                if record is None:
                    optional_statuses.append(
                        UnifiedAIRoleStatus(
                            role=role,
                            configured=False,
                            effective_configured=True,
                            provider=writer_status.provider,
                            base_url=writer_status.base_url,
                            model=writer_status.model,
                            api_key_configured=writer_status.api_key_configured,
                            credential_storage=writer_status.credential_storage,
                            updated_at=writer_status.updated_at,
                            fallback=True,
                            source_role="writer",
                        )
                    )
                    continue
                optional_statuses.append(
                    UnifiedAIRoleStatus(
                        role=role,
                        configured=True,
                        effective_configured=True,
                        provider=record["provider"],
                        base_url=record["base_url"],
                        model=record["model"],
                        api_key_configured=bool(record["credential"]["protected_data"]),
                        credential_storage=record["credential"]["protection"],
                        updated_at=record["updated_at"],
                        fallback=False,
                        source_role=role,
                    )
                )
            return UnifiedAIConfigStatus(
                configured=True,
                save_supported=self.protector is not None,
                provider=payload["provider"],
                base_url=payload["base_url"],
                model=payload["model"],
                api_key_configured=bool(credential["protected_data"]),
                credential_storage=credential["protection"],
                updated_at=payload["updated_at"],
                roles=(writer_status, *optional_statuses),
            )

    def load_settings(self, role: object = "writer") -> AISettings:
        with self._lock:
            if not self.config_path.exists():
                raise AIConfigurationError("尚未保存统一 AI 配置")
            payload = self._read_unlocked()
            normalized_role = _normalize_role(role)
            record = payload
            if normalized_role != "writer":
                record = payload.get("roles", {}).get(normalized_role) or payload
            api_key = self._decrypt_record(record)
            return AISettings.from_mapping(
                {
                    "base_url": record["base_url"],
                    "api_key": api_key,
                    "model": record["model"],
                    "provider": record["provider"],
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
            # Validate and normalize the candidate connection before deciding
            # whether an existing secret is safe to reuse.  A key saved for one
            # endpoint must never be silently forwarded to another provider.
            candidate_settings = AISettings.from_mapping(
                {
                    "base_url": base_url,
                    "api_key": normalized_api_key or "credential-reuse-check",
                    "model": model,
                    "provider": provider,
                }
            )
            normalized_provider = _normalize_provider(provider)
            if not normalized_provider:
                normalized_provider = provider_label_for_settings(candidate_settings)
            existing_payload: dict[str, Any] | None = None
            if self.config_path.exists():
                try:
                    existing_payload = self._read_unlocked()
                except UnifiedAIConfigDataError:
                    # Supplying a new credential is the only safe recovery path
                    # for a corrupt local config.
                    if not normalized_api_key:
                        raise
            if not normalized_api_key and existing_payload is not None:
                same_connection = bool(
                    normalized_provider == existing_payload["provider"]
                    and candidate_settings.base_url.rstrip("/")
                    == existing_payload["base_url"].rstrip("/")
                )
                if same_connection:
                    normalized_api_key = self._decrypt_record(existing_payload)
            settings = AISettings.from_mapping(
                {
                    "base_url": candidate_settings.base_url,
                    "api_key": normalized_api_key,
                    "model": candidate_settings.model,
                    "provider": candidate_settings.provider,
                }
            )
            now = _utc_now()
            created_at = (
                existing_payload["created_at"] if existing_payload is not None else now
            )
            protected_data = protector.protect(settings.api_key.encode("utf-8"))
            preserved_roles: dict[str, Any] = dict(
                existing_payload.get("roles", {}) if existing_payload is not None else {}
            )
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
                "roles": preserved_roles,
            }
            self._write_atomic_unlocked(payload)
            return self.status()

    def save_role(
        self,
        role: object,
        *,
        provider: object,
        base_url: object,
        model: object,
        api_key: object = None,
        reuse_writer_credential: bool = True,
    ) -> UnifiedAIConfigStatus:
        """Save one optional role while retaining every other configuration."""

        normalized_role = _normalize_role(role)
        if normalized_role == "writer":
            return self.save(
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
            )
        with self._lock:
            if self.protector is None:
                raise UnifiedAIConfigStorageUnavailableError(
                    "当前运行环境没有可用的系统级凭据保护，未保存 API Key"
                )
            if not self.config_path.exists():
                raise UnifiedAIConfigDataError("请先配置默认写稿模型")
            payload = self._read_unlocked()
            existing = payload.get("roles", {}).get(normalized_role)
            normalized_api_key = str(api_key or "").strip()
            credential_source = normalized_role
            candidate_provider = _normalize_provider(provider)
            candidate_base_url = str(base_url or "").strip().rstrip("/")
            existing_connection_matches = bool(
                existing is not None
                and candidate_provider == existing["provider"]
                and candidate_base_url == existing["base_url"].rstrip("/")
            )
            if not normalized_api_key and existing_connection_matches:
                normalized_api_key = self._decrypt_record(existing)
                credential_source = str(
                    existing.get("credential_source") or normalized_role
                )
            if not normalized_api_key and reuse_writer_credential:
                writer_settings = AISettings.from_mapping(
                    {
                        "provider": payload["provider"],
                        "base_url": payload["base_url"],
                        "model": payload["model"],
                        "api_key": self._decrypt_record(payload),
                    }
                )
                if (
                    candidate_provider == payload["provider"]
                    and candidate_base_url == writer_settings.base_url.rstrip("/")
                ):
                    normalized_api_key = writer_settings.api_key
                    credential_source = "writer"
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
            created_at = existing["created_at"] if existing is not None else now
            protected_data = self.protector.protect(settings.api_key.encode("utf-8"))
            roles = dict(payload.get("roles", {}))
            roles[normalized_role] = {
                "provider": normalized_provider,
                "base_url": settings.base_url,
                "model": settings.model,
                "credential": {
                    "protection": self.protector.name,
                    "protected_data": base64.b64encode(protected_data).decode("ascii"),
                },
                "credential_source": credential_source,
                "created_at": created_at,
                "updated_at": now,
            }
            payload["schema_version"] = SCHEMA_VERSION
            payload["roles"] = roles
            payload["updated_at"] = now
            self._write_atomic_unlocked(payload)
            return self.status()

    def clear_role(self, role: object) -> UnifiedAIConfigStatus:
        normalized_role = _normalize_role(role)
        if normalized_role == "writer":
            return self.clear()
        with self._lock:
            if not self.config_path.exists():
                return self.status()
            payload = self._read_unlocked()
            roles = dict(payload.get("roles", {}))
            roles.pop(normalized_role, None)
            payload["schema_version"] = SCHEMA_VERSION
            payload["roles"] = roles
            payload["updated_at"] = _utc_now()
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

    def _decrypt_record(self, record: dict[str, Any]) -> str:
        protector = self._require_matching_protector(record)
        try:
            ciphertext = base64.b64decode(
                record["credential"]["protected_data"],
                validate=True,
            )
            plaintext = protector.unprotect(ciphertext)
            return plaintext.decode("utf-8")
        except (ValueError, UnicodeError, UnifiedAIConfigError) as exc:
            raise UnifiedAIConfigDataError("统一 AI 凭据无法解密") from exc

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            return _validate_payload(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            AIConfigurationError,
        ) as exc:
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


def _normalize_role(value: object) -> str:
    normalized = str(value or "writer").strip().lower()
    normalized = AI_ROLE_ALIASES.get(normalized, normalized)
    if normalized not in AI_ROLE_IDS:
        raise AIConfigurationError("AI 角色仅支持 writer、reviewer、evaluator、image")
    return normalized


def _empty_role_status(
    role: str,
    *,
    save_supported: bool,
) -> UnifiedAIRoleStatus:
    return UnifiedAIRoleStatus(
        role=role,
        configured=False,
        effective_configured=False,
        provider="",
        base_url="",
        model="",
        api_key_configured=False,
        credential_storage=("unavailable" if not save_supported else ""),
        updated_at="",
        fallback=role != "writer",
        source_role="writer" if role != "writer" else "",
    )


def _validate_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") not in {
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
    }:
        raise ValueError("unsupported schema")
    root = _validate_config_record(value)
    roles_value = value.get("roles", {})
    if not isinstance(roles_value, dict):
        raise ValueError("roles is invalid")
    roles: dict[str, Any] = {}
    for raw_role, raw_record in roles_value.items():
        role = _normalize_role(raw_role)
        if role == "writer":
            raise ValueError("writer must use the root configuration")
        roles[role] = _validate_config_record(raw_record)
        roles[role]["credential_source"] = str(
            raw_record.get("credential_source") or role
        )
    return {
        "schema_version": SCHEMA_VERSION,
        **root,
        "roles": roles,
    }


def _validate_config_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("config record is invalid")
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
    "AI_ROLE_IDS",
    "OPTIONAL_AI_ROLE_IDS",
    "SecretProtector",
    "UnifiedAIConfigDataError",
    "UnifiedAIConfigError",
    "UnifiedAIConfigService",
    "UnifiedAIConfigStatus",
    "UnifiedAIRoleStatus",
    "UnifiedAIConfigStorageUnavailableError",
    "WindowsDPAPIProtector",
    "create_platform_secret_protector",
]
