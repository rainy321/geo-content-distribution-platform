from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


WEIBO_OFFICIAL_PACKAGE = "@weibo-ai/weibo-cli"
WEIBO_OFFICIAL_CLI_VERSION = "0.9.4"
WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE = (
    "需安装/授权官方连接器：请安装 @weibo-ai/weibo-cli，并运行 "
    "`weibo-cli auth login` 或 `weibo-cli auth login --device` 完成授权"
)
WEIBO_SINGLE_SESSION_MESSAGE = (
    "微博官方 CLI 当前仅支持一个默认授权会话，请重新连接已有微博账号"
)
WEIBO_MARKER_CONNECTOR = "weibo-official-cli"
WEIBO_MARKER_VERSION = 1
WEIBO_PRODUCT_TEXT_SAFE_LIMIT = 800

_WRITE_ACTION_ALLOWLIST = ("update/biz",)
_TIMELINE_ACTION_ALLOWLIST = ("user_timeline/biz",)
_PUBLIC_MOBILE_RE = re.compile(r"/detail/(?P<id>[1-9]\d*)/?")
_PUBLIC_DESKTOP_RE = re.compile(
    r"/(?P<uid>[1-9]\d*)/(?P<bid>[0-9A-Za-z]{5,32})/?"
)
_AI_TOKEN_RE = re.compile(r"(?:\bai\b|人工智能|aigc)", re.I)
_GENERATED_TOKEN_RE = re.compile(
    r"(?:生成|创作|合成|声明|标识|generated|created|synthetic|declare|label)",
    re.I,
)
_EXPLICIT_AI_FLAG_NAMES = frozenset(
    {
        "ai",
        "aigc",
        "ai_generated",
        "ai-generated",
        "is_ai",
        "is-ai",
        "is_ai_generated",
        "is-ai-generated",
        "ai_declaration",
        "ai-declaration",
    }
)


class WeiboConnectorError(RuntimeError):
    """Base class for secret-free official connector failures."""


class WeiboConnectorUnavailableError(WeiboConnectorError):
    pass


class WeiboAuthorizationError(WeiboConnectorError):
    pass


class WeiboCatalogError(WeiboConnectorError):
    pass


class WeiboPublishUncertainError(WeiboConnectorError):
    """Raised only after a write command has started and must not be retried."""


@dataclass(frozen=True)
class WeiboIdentity:
    uid: str
    username: str


@dataclass(frozen=True)
class WeiboCliRuntime:
    executable: str
    script: str = ""
    source: str = "external"
    version: str = ""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        return (self.executable, self.script) if self.script else (self.executable,)


def resolve_weibo_cli_runtime(
    explicit: str | os.PathLike[str] | None = None,
) -> WeiboCliRuntime:
    """Resolve a packaged or explicitly installed official CLI safely."""

    configured_candidates: list[str] = []
    if explicit is not None and str(explicit).strip():
        configured_candidates.append(str(explicit).strip())
    configured = str(os.environ.get("WEIBO_OFFICIAL_CLI_PATH") or "").strip()
    if configured:
        configured_candidates.append(configured)

    for candidate in configured_candidates:
        path = Path(candidate).expanduser()
        if (path.is_absolute() or path.parent != Path(".")) and path.is_file():
            runtime = _runtime_from_path(path.resolve(), source="configured")
            if runtime is not None:
                return runtime
        resolved = shutil.which(candidate)
        if resolved:
            runtime = _runtime_from_path(Path(resolved).resolve(), source="configured")
            if runtime is not None:
                return runtime

    packaged = _packaged_weibo_runtime()
    if packaged is not None:
        return packaged

    for candidate in ("weibo-cli", "weibo", "wb"):
        resolved = shutil.which(candidate)
        if resolved:
            runtime = _runtime_from_path(Path(resolved).resolve(), source="external")
            if runtime is not None:
                return runtime
    raise WeiboConnectorUnavailableError(WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE)


def resolve_weibo_cli(explicit: str | os.PathLike[str] | None = None) -> str:
    """Return the resolved process executable for compatibility probes."""

    return resolve_weibo_cli_runtime(explicit).executable


def _packaged_weibo_runtime() -> WeiboCliRuntime | None:
    if not bool(getattr(sys, "frozen", False)):
        return None
    root = Path(str(getattr(sys, "_MEIPASS", "") or "")) / "weibo_cli_runtime"
    node = root / "node.exe"
    script = root / "node_modules" / "@weibo-ai" / "weibo-cli" / "dist" / "index.js"
    if node.is_file() and script.is_file():
        return WeiboCliRuntime(
            executable=str(node.resolve()),
            script=str(script.resolve()),
            source="packaged",
            version=WEIBO_OFFICIAL_CLI_VERSION,
        )
    return None


def _runtime_from_path(path: Path, *, source: str) -> WeiboCliRuntime | None:
    suffix = path.suffix.casefold()
    if suffix in {".js", ".mjs", ".cjs"}:
        node = shutil.which("node")
        if node:
            return WeiboCliRuntime(str(Path(node).resolve()), str(path), source=source)
        return None
    if suffix in {".cmd", ".bat"}:
        script = (
            path.parent
            / "node_modules"
            / "@weibo-ai"
            / "weibo-cli"
            / "dist"
            / "index.js"
        )
        node = shutil.which("node")
        if node and script.is_file():
            return WeiboCliRuntime(
                str(Path(node).resolve()),
                str(script.resolve()),
                source=source,
            )
        return None
    return WeiboCliRuntime(str(path), source=source)


def is_weibo_public_status_url(value: Any) -> bool:
    return bool(normalize_weibo_public_status_url(value))


def build_weibo_status_text(
    title: Any,
    content: Any,
    tags: Iterable[Any] = (),
) -> str:
    """Compose the exact CLI text whose length is checked by every layer."""

    normalized_tags = [str(tag or "").strip() for tag in tags]
    parts = [str(title or "").strip(), str(content or "").strip()]
    hashtags = " ".join(f"#{tag}#" for tag in normalized_tags if tag)
    if hashtags:
        parts.append(hashtags)
    return "\n\n".join(part for part in parts if part).strip()


def normalize_weibo_public_status_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = str(parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return ""
    if hostname == "m.weibo.cn":
        match = _PUBLIC_MOBILE_RE.fullmatch(parsed.path)
        if match is None or parsed.query:
            return ""
        return f"https://m.weibo.cn/detail/{match.group('id')}"
    if hostname in {"weibo.com", "www.weibo.com"}:
        match = _PUBLIC_DESKTOP_RE.fullmatch(parsed.path)
        if match is None or parsed.query:
            return ""
        return urlunsplit(
            (
                "https",
                "weibo.com",
                f"/{match.group('uid')}/{match.group('bid')}",
                "",
                "",
            )
        )
    return ""


class WeiboOfficialConnector:
    """Fail-closed bridge to the official, runtime-described Weibo CLI.

    The adapter never installs the CLI, never reads keychain credentials and
    never guesses an undocumented action.  A write action and its AI-content
    declaration flag must both appear in the authorized runtime catalog.
    """

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        timeout_seconds: float = 45,
        runner: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("微博官方连接器超时时间必须大于 0")
        self._configured_executable = executable
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner or subprocess.run

    @property
    def executable(self) -> str:
        return resolve_weibo_cli_runtime(self._configured_executable).executable

    @property
    def command_prefix(self) -> tuple[str, ...]:
        return resolve_weibo_cli_runtime(self._configured_executable).command_prefix

    def is_available(self) -> bool:
        try:
            self.executable
        except WeiboConnectorUnavailableError:
            return False
        return True

    def read_identity(self) -> WeiboIdentity:
        payload = self._run_json(
            ("auth", "whoami"),
            error_type=WeiboAuthorizationError,
            error_message=(
                "微博官方连接器尚未授权或授权已失效；"
                + WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE
            ),
        )
        identity = _extract_identity(payload)
        if identity is None:
            raise WeiboAuthorizationError(
                "微博官方连接器未返回可绑定的账号标识；"
                + WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE
            )
        return identity

    def doctor(self) -> bool:
        self._run(
            ("doctor", "--output", "json"),
            error_type=WeiboAuthorizationError,
            error_message=(
                "微博官方连接器健康检查未通过；"
                + WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE
            ),
        )
        return True

    def verify_marker(self, marker_path: str | Path) -> WeiboIdentity:
        marker = _read_marker(marker_path)
        identity = self.read_identity()
        self.doctor()
        if marker.get("uid") != identity.uid:
            raise WeiboAuthorizationError(
                "微博官方连接器当前授权账号与任务绑定账号不一致；"
                "请重新连接已有微博账号"
            )
        return identity

    def write_marker(
        self,
        marker_path: str | Path,
        identity: WeiboIdentity,
    ) -> None:
        target = Path(marker_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "connector": WEIBO_MARKER_CONNECTOR,
            "version": WEIBO_MARKER_VERSION,
            "uid": identity.uid,
            "username": identity.username,
        }
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".weibo-marker-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
                temporary_name = handle.name
            os.replace(temporary_name, target)
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def publish_status(self, text: str, *, image_path: str = "") -> dict[str, str]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise ValueError("微博正文不能为空")
        if len(normalized_text) > WEIBO_PRODUCT_TEXT_SAFE_LIMIT:
            raise ValueError(
                f"微博发布文本超过产品侧 {WEIBO_PRODUCT_TEXT_SAFE_LIMIT} 字保守安全线；"
                "该安全线不是微博官方配额"
            )
        action, flags = self._discover_command(
            _WRITE_ACTION_ALLOWLIST,
            required_flags=("text",),
        )
        ai_flag = _select_ai_declaration_flag(flags)
        command = ["statuses", action, "--text", normalized_text]
        command.extend(_affirmative_flag_arguments(ai_flag))

        normalized_image = str(image_path or "").strip()
        if normalized_image:
            image = Path(normalized_image).expanduser().resolve()
            if not image.is_file():
                raise ValueError("微博图片文件不存在")
            if not _has_flag(flags, "pic"):
                raise WeiboCatalogError(
                    "微博官方连接器当前命令目录未声明图片参数，需人工发布或更新官方连接器"
                )
            command.extend(("--pic", str(image)))

        payload = self._run_json(
            tuple(command),
            error_type=WeiboPublishUncertainError,
            error_message=(
                "微博官方写入命令未返回确定结果，平台最终状态未知；"
                "请先人工核验，系统不会自动重试"
            ),
        )

        url = _extract_public_url(payload)
        status_id = _extract_status_id(payload)
        if not url and status_id:
            url = f"https://m.weibo.cn/detail/{status_id}"
        if url:
            return {"status": "success", "url": url, "status_id": status_id}
        return {
            "status": "processing",
            "url": "",
            "status_id": "",
            "message": (
                "微博官方连接器已执行提交，但未返回可核验公开链接或作品编号；"
                "请人工核验，系统不会自动重试"
            ),
        }

    def recent_status_rows(self, title: str, *, count: int = 20) -> list[dict[str, Any]]:
        normalized_title = str(title or "").strip()
        if not normalized_title:
            return []
        action, flags = self._discover_command(_TIMELINE_ACTION_ALLOWLIST)
        command = ["statuses", action]
        if _has_flag(flags, "count"):
            command.extend(("--count", str(max(1, min(int(count), 50)))))
        payload = self._run_json(
            tuple(command),
            error_type=WeiboConnectorError,
            error_message="微博官方连接器只读核验失败，请稍后重试或人工核验",
        )
        rows: list[dict[str, Any]] = []
        for status in _extract_statuses(payload):
            rendered_text = _normalize_status_text(status.get("text"))
            if not _status_has_exact_title(rendered_text, normalized_title):
                continue
            status_id = _status_id_from_mapping(status)
            url = _public_url_from_mapping(status)
            intervals = _status_time_intervals(status)
            explicitly_public = _status_is_explicitly_public(status)
            published = bool(url and explicitly_public)
            visibility_text = (
                "已发布"
                if published
                else "公开可见性或公开作品链接未确认"
            )
            rows.append(
                {
                    "text": f"{normalized_title} {visibility_text} status_id={status_id}",
                    "urls": (url,) if url else (),
                    "external_ids": (status_id,) if status_id else (),
                    "time_intervals": tuple(intervals),
                    "public_visibility_confirmed": published,
                    "title_match_confirmed": True,
                }
            )
        return rows

    def _discover_command(
        self,
        allowed_actions: Sequence[str],
        *,
        required_flags: Sequence[str] = (),
    ) -> tuple[str, tuple[Mapping[str, Any], ...]]:
        payload = self._run_json(
            ("commands", "list", "--group", "statuses", "--available"),
            error_type=WeiboCatalogError,
            error_message=(
                "无法读取微博官方连接器的授权命令目录；需人工发布或重新授权官方连接器"
            ),
        )
        commands = _extract_commands(payload)
        available = {
            str(item.get("action") or "").strip()
            for item in commands
            if str(item.get("group") or "").strip() == "statuses"
        }
        action = next((item for item in allowed_actions if item in available), "")
        if not action:
            raise WeiboCatalogError(
                "微博官方连接器当前授权命令目录未开放所需 statuses 操作，需人工处理"
            )
        detail = self._run_json(
            ("commands", "show", "statuses", action),
            error_type=WeiboCatalogError,
            error_message="无法确认微博官方连接器命令参数，需人工处理",
        )
        flags = _extract_flags(detail)
        missing = [name for name in required_flags if not _has_flag(flags, name)]
        if missing:
            raise WeiboCatalogError(
                "微博官方连接器命令目录缺少必需参数，需人工发布或更新官方连接器"
            )
        return action, flags

    def _run_json(
        self,
        arguments: Sequence[str],
        *,
        error_type: type[WeiboConnectorError],
        error_message: str,
    ) -> Any:
        result = self._run(
            (*arguments, "--output", "json"),
            error_type=error_type,
            error_message=error_message,
        )
        try:
            return _parse_json_output(getattr(result, "stdout", ""))
        except WeiboConnectorError as exc:
            raise error_type(error_message) from exc

    def _run(
        self,
        arguments: Sequence[str],
        *,
        error_type: type[WeiboConnectorError],
        error_message: str,
    ) -> Any:
        command = [*self.command_prefix, *[str(item) for item in arguments]]
        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise WeiboConnectorUnavailableError(
                WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE
            ) from exc
        except subprocess.TimeoutExpired as exc:
            if issubclass(error_type, WeiboPublishUncertainError):
                raise WeiboPublishUncertainError(
                    "微博官方写入命令超时，平台最终状态未知；"
                    "请先人工核验，系统不会自动重试"
                ) from exc
            raise error_type(error_message) from exc
        except OSError as exc:
            raise error_type(error_message) from exc
        if int(getattr(result, "returncode", 1)) != 0:
            raise error_type(error_message)
        return result


def _parse_json_output(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        raise WeiboConnectorError("微博官方连接器未返回 JSON 结果")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line.strip())
            except (json.JSONDecodeError, TypeError):
                continue
    raise WeiboConnectorError("微博官方连接器返回了无法解析的 JSON 结果")


def _extract_identity(payload: Any) -> WeiboIdentity | None:
    mappings = _known_mappings(payload, keys=("user", "account", "data", "result"))
    for item in mappings:
        uid = _first_string(item, ("uid", "idstr", "id", "user_id", "userId"))
        username = _first_string(
            item,
            ("screen_name", "screenName", "username", "name", "nickname"),
        )
        if uid and re.fullmatch(r"[1-9]\d*", uid):
            return WeiboIdentity(uid=uid, username=username or f"微博用户 {uid}")
    return None


def _read_marker(marker_path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(marker_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise WeiboAuthorizationError("微博账号绑定标记不存在或已损坏，请重新连接") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("connector") != WEIBO_MARKER_CONNECTOR
        or payload.get("version") != WEIBO_MARKER_VERSION
        or re.fullmatch(r"[1-9]\d*", str(payload.get("uid") or "")) is None
    ):
        raise WeiboAuthorizationError("微博账号绑定标记无效，请重新连接")
    return payload


def _extract_commands(payload: Any) -> tuple[Mapping[str, Any], ...]:
    candidates: Any = payload.get("commands") if isinstance(payload, dict) else None
    if candidates is None and isinstance(payload, dict):
        data = payload.get("data")
        candidates = data.get("commands") if isinstance(data, dict) else data
    if not isinstance(candidates, list):
        raise WeiboCatalogError("微博官方连接器命令目录格式无法确认，需人工处理")
    return tuple(item for item in candidates if isinstance(item, dict))


def _extract_flags(payload: Any) -> tuple[Mapping[str, Any], ...]:
    command: Any = payload.get("command") if isinstance(payload, dict) else None
    if command is None and isinstance(payload, dict):
        data = payload.get("data")
        command = data.get("command") if isinstance(data, dict) else data
    flags = command.get("flags") if isinstance(command, dict) else None
    if not isinstance(flags, list):
        raise WeiboCatalogError("微博官方连接器命令参数格式无法确认，需人工处理")
    return tuple(item for item in flags if isinstance(item, dict))


def _has_flag(flags: Iterable[Mapping[str, Any]], name: str) -> bool:
    wanted = str(name).strip().lstrip("-").casefold()
    return any(
        str(item.get("name") or "").strip().lstrip("-").casefold() == wanted
        for item in flags
    )


def _select_ai_declaration_flag(
    flags: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    candidates: list[Mapping[str, Any]] = []
    for item in flags:
        name = str(item.get("name") or "").strip().lstrip("-")
        description = str(item.get("description") or "").strip()
        normalized_name = name.casefold()
        combined = f"{name} {description}"
        if normalized_name in _EXPLICIT_AI_FLAG_NAMES or (
            _AI_TOKEN_RE.search(combined) and _GENERATED_TOKEN_RE.search(combined)
        ):
            candidates.append(item)
    if len(candidates) != 1:
        raise WeiboCatalogError(
            "微博官方连接器命令目录未唯一声明 AI 生成内容标识参数，需人工发布"
        )
    return candidates[0]


def _affirmative_flag_arguments(flag: Mapping[str, Any]) -> list[str]:
    name = str(flag.get("name") or "").strip().lstrip("-")
    if not name:
        raise WeiboCatalogError("微博官方连接器 AI 声明参数名称无效，需人工发布")
    flag_type = str(flag.get("type") or "").strip().casefold()
    if flag_type in {"bool", "boolean", "flag"}:
        return [f"--{name}"]
    values = flag.get("enum") or flag.get("values") or flag.get("choices")
    if isinstance(values, (tuple, list)):
        affirmative = {
            "true",
            "1",
            "yes",
            "ai",
            "aigc",
            "generated",
            "ai_generated",
            "ai-generated",
        }
        matches = [str(value) for value in values if str(value).casefold() in affirmative]
        if len(matches) == 1:
            return [f"--{name}", matches[0]]
    raise WeiboCatalogError(
        "微博官方连接器 AI 声明参数取值无法安全确认，需人工发布"
    )


def _extract_public_url(payload: Any) -> str:
    for item in _known_mappings(payload, keys=("status", "data", "result")):
        url = _public_url_from_mapping(item)
        if url:
            return url
    return ""


def _public_url_from_mapping(item: Mapping[str, Any]) -> str:
    for key in ("url", "status_url", "statusUrl", "permalink", "scheme"):
        normalized = normalize_weibo_public_status_url(item.get(key))
        if normalized:
            return normalized
    return ""


def _extract_status_id(payload: Any) -> str:
    for item in _known_mappings(payload, keys=("status", "data", "result")):
        status_id = _status_id_from_mapping(item)
        if status_id:
            return status_id
    return ""


def _status_id_from_mapping(item: Mapping[str, Any]) -> str:
    # A generic ``id`` may identify the authorized user, request or operation.
    # Only fields with explicit Weibo-status semantics can prove a work ID.
    value = _first_string(item, ("status_id", "statusId", "mid", "idstr"))
    return value if re.fullmatch(r"[1-9]\d*", value) else ""


def _status_is_explicitly_public(status: Mapping[str, Any]) -> bool:
    for key in ("is_public", "isPublic", "public"):
        if status.get(key) is True:
            return True
    for key in ("visibility", "privacy", "visible_scope", "visibleScope"):
        value = status.get(key)
        if isinstance(value, str) and value.strip().casefold() in {
            "public",
            "everyone",
            "unrestricted",
            "open",
            "公开",
            "所有人",
        }:
            return True
    visible = status.get("visible")
    if isinstance(visible, Mapping):
        # The official status payload uses type 0 for an unrestricted status.
        value = visible.get("type")
        return not isinstance(value, bool) and value in {0, "0"}
    return False


def _known_mappings(payload: Any, *, keys: Sequence[str]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if isinstance(payload, dict):
        result.append(payload)
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                result.append(value)
                for nested_key in keys:
                    nested = value.get(nested_key)
                    if isinstance(nested, dict):
                        result.append(nested)
    return result


def _first_string(item: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            normalized = str(value).strip()
            if normalized:
                return normalized
    return ""


def _extract_statuses(payload: Any) -> list[Mapping[str, Any]]:
    values: list[Any] = []
    if isinstance(payload, list):
        values.append(payload)
    if isinstance(payload, dict):
        values.extend(payload.get(key) for key in ("statuses", "items", "list"))
        data = payload.get("data")
        if isinstance(data, list):
            values.append(data)
        elif isinstance(data, dict):
            values.extend(data.get(key) for key in ("statuses", "items", "list"))
    for value in values:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_status_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\u200b", "")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _status_has_exact_title(text: str, title: str) -> bool:
    if not text or not title:
        return False
    first_line = text.split("\n", 1)[0].strip()
    return first_line == title


def _status_time_intervals(status: Mapping[str, Any]) -> list[tuple[str, str]]:
    for key in ("created_at", "createdAt", "created", "timestamp"):
        raw = status.get(key)
        if raw in {None, ""}:
            continue
        parsed: datetime | None = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            number = float(raw)
            if number > 10_000_000_000:
                number /= 1000
            try:
                parsed = datetime.fromtimestamp(number, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                parsed = None
        else:
            text = str(raw).strip()
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(text)
                except (TypeError, ValueError):
                    parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            start = parsed.astimezone(timezone.utc)
            return [(start.isoformat(), (start + timedelta(seconds=1)).isoformat())]
    return []


__all__ = [
    "WEIBO_MARKER_CONNECTOR",
    "WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE",
    "WEIBO_OFFICIAL_CLI_VERSION",
    "WEIBO_OFFICIAL_PACKAGE",
    "WEIBO_PRODUCT_TEXT_SAFE_LIMIT",
    "WEIBO_SINGLE_SESSION_MESSAGE",
    "WeiboAuthorizationError",
    "WeiboCatalogError",
    "WeiboConnectorError",
    "WeiboConnectorUnavailableError",
    "WeiboIdentity",
    "WeiboCliRuntime",
    "WeiboOfficialConnector",
    "WeiboPublishUncertainError",
    "build_weibo_status_text",
    "is_weibo_public_status_url",
    "normalize_weibo_public_status_url",
    "resolve_weibo_cli",
    "resolve_weibo_cli_runtime",
]
