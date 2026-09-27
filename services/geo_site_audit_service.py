from __future__ import annotations

import hashlib
import ipaddress
import json
import queue
import re
import socket
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import requests
import urllib3


GEO_SITE_AUDIT_VERSION = "geo-site-audit-1.0.0"
MAX_URL_LENGTH = 2_048
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_PINNED_ADDRESS_ATTEMPTS = 4
MAX_RESOLVED_ADDRESSES = 32
MAX_AUDIT_SECONDS = 45.0
MAX_DNS_RESOLUTION_SECONDS = 5.0
DEFAULT_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 50
IN_PROGRESS_EXPIRY_SECONDS = 15 * 60
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
_IP_MAPPING_DOMAINS = ("nip.io", "sslip.io", "localtest.me", "lvh.me")
_RECOMMENDED_SCHEMA_TYPES = {
    "Article",
    "BlogPosting",
    "FAQPage",
    "HowTo",
    "LocalBusiness",
    "NewsArticle",
    "Organization",
    "Person",
    "Product",
    "SoftwareApplication",
    "WebPage",
    "WebSite",
}
_CRAWLERS = (
    ("OAI-SearchBot", "OpenAI 搜索爬虫", "搜索与引用发现"),
    ("ChatGPT-User", "ChatGPT 用户触发抓取", "用户请求时访问页面"),
    ("PerplexityBot", "Perplexity 爬虫", "搜索与引用发现"),
    ("Perplexity-User", "Perplexity 用户触发抓取", "用户请求时访问页面"),
    ("Claude-SearchBot", "Claude 搜索爬虫", "搜索结果发现"),
    ("Claude-User", "Claude 用户触发抓取", "用户请求时访问页面"),
    ("Googlebot", "Google 搜索爬虫", "Google 搜索索引，可能支撑 AI 搜索体验"),
    ("ClaudeBot", "Anthropic 训练爬虫", "模型训练控制，不等于搜索收录"),
    ("GPTBot", "OpenAI 训练爬虫", "训练数据控制，不等于搜索收录"),
    ("Google-Extended", "Google AI 扩展控制", "生成式 AI 使用控制，不等于 Googlebot"),
)
_RETRIEVAL_CRAWLERS = {
    "OAI-SearchBot",
    "PerplexityBot",
    "Claude-SearchBot",
    "Googlebot",
}
_AI_RETRIEVAL_CRAWLERS = {
    "OAI-SearchBot",
    "PerplexityBot",
    "Claude-SearchBot",
}
_DNS_RESOLUTION_SLOTS = threading.BoundedSemaphore(4)


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _coalesced_response_headers(source: Any) -> dict[str, str]:
    """Preserve repeated security-relevant headers instead of last-write wins."""

    result: dict[str, str] = {}
    for key, value in source.items():
        normalized_key = str(key).casefold()
        normalized_value = str(value)
        previous = result.get(normalized_key)
        result[normalized_key] = (
            f"{previous}, {normalized_value}" if previous else normalized_value
        )
    return result


class GEOSiteAuditError(ValueError):
    pass


class GEOSiteAuditTimeoutError(GEOSiteAuditError):
    pass


class GEOSiteAuditDNSTimeoutError(GEOSiteAuditTimeoutError):
    pass


class GEOSiteAuditNotFoundError(LookupError):
    pass


class GEOSiteAuditConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class _FetchResult:
    requested_url: str
    final_url: str
    status_code: int | None
    headers: Mapping[str, str]
    body: bytes
    redirects: tuple[str, ...]
    error: str

    @property
    def ok(self) -> bool:
        return (
            self.error == ""
            and self.status_code is not None
            and 200 <= self.status_code < 300
        )

    @property
    def text(self) -> str:
        if not self.body:
            return ""
        content_type = str(self.headers.get("content-type") or "")
        charset_match = re.search(r"charset\s*=\s*['\"]?([^\s;'\"]+)", content_type, re.I)
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class _PinnedResponse:
    def __init__(
        self,
        response: Any,
        pool: Any,
        *,
        deadline: float | None,
        read_timeout: float,
    ) -> None:
        self.status_code = int(response.status)
        self.headers = response.headers
        self._response = response
        self._pool = pool
        self._deadline = deadline
        self._read_timeout = float(read_timeout)
        self._closed = False

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        try:
            while True:
                remaining = _remaining_seconds(self._deadline)
                if remaining is not None:
                    if remaining <= 0:
                        raise requests.Timeout("site audit deadline exceeded")
                    connection = getattr(self._response, "connection", None)
                    sock = getattr(connection, "sock", None)
                    if sock is not None:
                        sock.settimeout(min(self._read_timeout, max(0.05, remaining)))
                chunk = self._response.read(
                    amt=chunk_size,
                    decode_content=True,
                )
                if not chunk:
                    break
                yield chunk
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise requests.ConnectionError(
                f"pinned response read failed: {type(exc).__name__}"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            self._pool.close()


class _AuditDnsCache:
    """Keep one validated DNS answer per host stable for a single audit."""

    def __init__(self, resolver: Callable[..., Any]) -> None:
        self._resolver = resolver
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int, Any], tuple[Any, ...]] = {}

    def __call__(
        self,
        hostname: str,
        port: int,
        *,
        type: Any = socket.SOCK_STREAM,
    ) -> tuple[Any, ...]:
        key = (str(hostname).rstrip(".").casefold(), int(port), type)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        resolved = tuple(self._resolver(hostname, port, type=type))
        with self._lock:
            return self._cache.setdefault(key, resolved)


class _PinnedPublicHttpClient:
    """Connect to the exact address set that passed public-address validation."""

    def __init__(
        self,
        dns_resolver: Callable[..., Any],
        *,
        allow_proxy_fake_ip: bool,
        deadline: float | None = None,
    ) -> None:
        self._dns_resolver = dns_resolver
        self._allow_proxy_fake_ip = allow_proxy_fake_ip
        self._deadline = deadline

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: tuple[float, float] | float = (5.0, 15.0),
        allow_redirects: bool = False,
        stream: bool = True,
    ) -> _PinnedResponse:
        del allow_redirects, stream
        try:
            normalized_url, addresses = _validated_public_url(
                url,
                dns_resolver=self._dns_resolver,
                allow_proxy_fake_ip=self._allow_proxy_fake_ip,
                deadline=self._deadline,
            )
        except GEOSiteAuditError as exc:
            raise requests.ConnectionError("validated address changed before connect") from exc
        parsed = urlsplit(normalized_url)
        hostname = str(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        request_headers = dict(headers or {})
        request_headers["Host"] = parsed.netloc
        if isinstance(timeout, tuple):
            connect_timeout, read_timeout = timeout
        else:
            connect_timeout = read_timeout = timeout
        last_error: Exception | None = None
        for address in addresses[:MAX_PINNED_ADDRESS_ATTEMPTS]:
            remaining = _remaining_seconds(self._deadline)
            if remaining is not None and remaining <= 0:
                raise requests.Timeout("site audit deadline exceeded")
            bounded_connect = float(connect_timeout)
            bounded_read = float(read_timeout)
            if remaining is not None:
                bounded_connect = min(bounded_connect, max(0.1, remaining))
                bounded_read = min(bounded_read, max(0.1, remaining))
            urllib_timeout = urllib3.Timeout(
                total=remaining,
                connect=bounded_connect,
                read=bounded_read,
            )
            pool: Any
            if parsed.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(
                    str(address),
                    port,
                    server_hostname=hostname,
                    assert_hostname=hostname,
                    cert_reqs="CERT_REQUIRED",
                    ca_certs=requests.certs.where(),
                    maxsize=1,
                    block=True,
                )
            else:
                pool = urllib3.HTTPConnectionPool(
                    str(address),
                    port,
                    maxsize=1,
                    block=True,
                )
            try:
                response = pool.urlopen(
                    "GET",
                    request_target,
                    headers=request_headers,
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    timeout=urllib_timeout,
                )
                return _PinnedResponse(
                    response,
                    pool,
                    deadline=self._deadline,
                    read_timeout=bounded_read,
                )
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                last_error = exc
                pool.close()
        error_name = type(last_error).__name__ if last_error is not None else "NoAddress"
        raise requests.ConnectionError(f"pinned connection failed: {error_name}")


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_text_parts: list[str] = []
        self.main_text_parts: list[str] = []
        self.json_ld_blocks: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical_urls: list[str] = []
        self.html_lang = ""
        self.heading_counts = {"h1": 0, "h2": 0, "h3": 0}
        self.has_main = False
        self.has_article = False
        self._title_depth = 0
        self._hidden_depth = 0
        self._main_depth = 0
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        if normalized_tag == "html":
            self.html_lang = attributes.get("lang", "").strip()
        if normalized_tag == "title":
            self._title_depth += 1
        if normalized_tag in {"main", "article"}:
            self._main_depth += 1
            self.has_main = self.has_main or normalized_tag == "main"
            self.has_article = self.has_article or normalized_tag == "article"
        if normalized_tag in self.heading_counts:
            self.heading_counts[normalized_tag] += 1
        if normalized_tag == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").casefold()
            content = attributes.get("content", "").strip()
            if key and content:
                previous = self.meta.get(key, "")
                self.meta[key] = f"{previous}, {content}" if previous else content
        if normalized_tag == "link":
            rel_tokens = {item.casefold() for item in attributes.get("rel", "").split()}
            if "canonical" in rel_tokens and attributes.get("href"):
                self.canonical_urls.append(attributes["href"].strip())
        if normalized_tag == "script":
            if attributes.get("type", "").split(";", 1)[0].strip().casefold() == "application/ld+json":
                self._json_ld_depth += 1
                self._json_ld_parts = []
            self._hidden_depth += 1
        elif normalized_tag in {"style", "noscript", "template", "svg"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title" and self._title_depth:
            self._title_depth -= 1
        if normalized_tag in {"main", "article"} and self._main_depth:
            self._main_depth -= 1
        if normalized_tag == "script":
            if self._json_ld_depth:
                self.json_ld_blocks.append("".join(self._json_ld_parts).strip())
                self._json_ld_parts = []
                self._json_ld_depth -= 1
            if self._hidden_depth:
                self._hidden_depth -= 1
        elif normalized_tag in {"style", "noscript", "template", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_parts.append(data)
        if self._title_depth:
            self.title_parts.append(data)
        if self._hidden_depth:
            return
        normalized = " ".join(str(data or "").split())
        if not normalized:
            return
        self.visible_text_parts.append(normalized)
        if self._main_depth:
            self.main_text_parts.append(normalized)


def audit_geo_site(
    url: Any,
    *,
    http_client: Any | None = None,
    dns_resolver: Callable[..., Any] = socket.getaddrinfo,
    allow_proxy_fake_ip: bool = False,
    now: datetime | None = None,
    _deadline: float | None = None,
) -> dict[str, Any]:
    """Audit deterministic public-site readiness without executing JavaScript.

    The audit intentionally measures only fetchability and machine-readable page
    signals.  It does not infer index membership, model retrieval, or citations.
    """

    audit_deadline = _deadline or (time.monotonic() + MAX_AUDIT_SECONDS)
    if not isinstance(dns_resolver, _AuditDnsCache):
        dns_resolver = _AuditDnsCache(dns_resolver)
    if http_client is None:
        # The production client connects to the exact validated address instead
        # of resolving the hostname again inside requests/urllib3.  This closes
        # the DNS rebinding gap while retaining TLS SNI and hostname checks.
        http_client = _PinnedPublicHttpClient(
            dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=audit_deadline,
        )

    normalized_url = _normalize_public_url(
        url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    session = http_client
    page = _safe_fetch(
        session,
        normalized_url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    origin = _origin_url(page.final_url or normalized_url)
    target_url = page.final_url or normalized_url

    robots_url = urljoin(origin, "/robots.txt")
    llms_url = urljoin(origin, "/llms.txt")
    robots = _try_safe_fetch(
        session,
        robots_url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    llms = _try_safe_fetch(
        session,
        llms_url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    crawler_access, robots_resource, sitemap_candidates = _analyze_robots(
        robots,
        target_url=target_url,
    )

    default_sitemap = urljoin(origin, "/sitemap.xml")
    # Always audit the conventional same-site location.  Untrusted external
    # declarations must not consume every bounded sitemap slot.
    sitemap_urls = list(dict.fromkeys([default_sitemap, *sitemap_candidates]))[:3]
    sitemap_fetches = [
        _try_same_site_fetch(
            session,
            candidate,
            site_url=target_url,
            dns_resolver=dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=audit_deadline,
        )
        for candidate in sitemap_urls
    ]
    sitemap_resources = [_analyze_sitemap(item) for item in sitemap_fetches]

    parser = _PageParser()
    parser_error = ""
    if page.body:
        try:
            parser.feed(page.text)
            parser.close()
        except (ValueError, TypeError) as exc:
            parser_error = type(exc).__name__

    structured_data = _structured_data_summary(parser.json_ld_blocks)
    checks = _build_checks(
        page=page,
        parser=parser,
        parser_error=parser_error,
        crawler_access=crawler_access,
        sitemap_resources=sitemap_resources,
        structured_data=structured_data,
    )
    raw_score = sum(int(item["score"]) for item in checks)
    maximum = sum(int(item["max_score"]) for item in checks)
    raw_score = round((raw_score / maximum) * 100) if maximum else 0
    blocking_keys = {
        item["key"]
        for item in checks
        if item["status"] == "fail"
        and (
            item["key"] in {
                "page_access",
                "indexability",
                "crawler_access",
            }
            or item.get("blocking") is True
        )
    }
    readiness_warning = any(
        item["key"] == "crawler_access" and item["status"] == "warning"
        for item in checks
    ) or urlsplit(page.final_url or normalized_url).scheme != "https"
    score_cap_reasons: list[str] = []
    if blocking_keys:
        status = "blocked"
        score = min(raw_score, 59)
        score_cap_reasons.append(
            "blocking_checks:" + ",".join(sorted(blocking_keys))
        )
    elif readiness_warning:
        score = min(raw_score, 79)
        status = "needs_work" if score >= 60 else "limited"
        if any(
            item["key"] == "crawler_access" and item["status"] == "warning"
            for item in checks
        ):
            score_cap_reasons.append("crawler_access_unconfirmed_or_partial")
        if urlsplit(page.final_url or normalized_url).scheme != "https":
            score_cap_reasons.append("transport_not_https")
    elif raw_score >= 80:
        status = "ready"
        score = raw_score
    elif raw_score >= 60:
        status = "needs_work"
        score = raw_score
    else:
        status = "limited"
        score = raw_score

    llms_resource = _resource_summary(llms, kind="llms_txt")
    llms_content_type = str(llms.headers.get("content-type") or "").casefold()
    llms_text = llms.text.strip()
    llms_present = bool(
        llms.ok
        and llms_text
        and (
            "text/plain" in llms_content_type
            or "text/markdown" in llms_content_type
        )
        and not re.search(r"<\s*(?:!doctype\s+html|html|body)\b", llms_text[:1024], re.I)
    )
    if llms.ok and not llms_present:
        llms_resource["status"] = "invalid"
        llms_resource["error"] = "unexpected_or_empty_llms_content"
    llms_resource.update(
        {
            "present": llms_present,
            "proposal_only": True,
            "score_weight": 0,
        }
    )
    recommendations = _recommendations(
        checks,
        llms_present=llms_present,
        requested_scheme=urlsplit(normalized_url).scheme,
    )
    audited_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    audited_at_text = audited_at.isoformat().replace("+00:00", "Z")
    result: dict[str, Any] = {
        "id": None,
        "project_id": None,
        "requested_url": normalized_url,
        "final_url": page.final_url or normalized_url,
        "status": status,
        "score": score,
        "raw_score": raw_score,
        "score_cap_reasons": score_cap_reasons,
        "grade": _grade(score, blocked=bool(blocking_keys)),
        "checks": checks,
        "crawler_access": crawler_access,
        "structured_data": structured_data,
        "resources": {
            "page": _resource_summary(page, kind="page"),
            "robots": robots_resource,
            "sitemaps": sitemap_resources,
            "llms_txt": llms_resource,
        },
        "recommendations": recommendations,
        "boundaries": [
            "这是当前时点的公开 HTTP 技术准备度快照，不是搜索引擎或生成式 AI 的收录证明。",
            "robots.txt 只表达站点规则；允许抓取不等于爬虫实际到访、索引或引用。",
            "本审计不执行浏览器 JavaScript；客户端渲染内容可能需要另做渲染后审计。",
            "llms.txt 仍是非强制提案，因此只显示存在性，不计入分数。",
            "没有调用任何模型，也没有测量消费者 App、网页回答或真实引用率。",
            *(
                [
                    "存在关键阻断或未确认项，展示分已按准备度门禁封顶；"
                    "raw_score 仅保留各检查项原始加权和。"
                ]
                if score_cap_reasons
                else []
            ),
            *(
                [
                    "本机已显式启用 198.18.0.0/15 Fake-IP DNS/TUN 兼容；"
                    "每次连接仍固定到当次校验地址，但该模式应只在受信网络配置中使用。"
                ]
                if allow_proxy_fake_ip
                else []
            ),
        ],
        "methodology": {
            "logical_http_requests": sum(
                _fetch_attempt_count(item)
                for item in [page, robots, llms, *sitemap_fetches]
            ),
            "javascript_executed": False,
            "external_model_used": False,
            "full_page_content_persisted": False,
            "dns_address_pinning": http_client.__class__ is _PinnedPublicHttpClient,
            "per_audit_dns_pinning": isinstance(dns_resolver, _AuditDnsCache),
            "proxy_fake_ip_compatibility": bool(allow_proxy_fake_ip),
            "redirect_hops": list(page.redirects),
            "response_byte_limit": MAX_RESPONSE_BYTES,
            "audit_deadline_seconds": MAX_AUDIT_SECONDS,
            "max_pinned_address_attempts": MAX_PINNED_ADDRESS_ATTEMPTS,
        },
        "audit_version": GEO_SITE_AUDIT_VERSION,
        "audited_at": audited_at_text,
    }
    digest_source = {
        "requested_url": result["requested_url"],
        "final_url": result["final_url"],
        "checks": checks,
        "crawler_access": crawler_access,
        "resources": result["resources"],
        "audit_version": GEO_SITE_AUDIT_VERSION,
        "audited_at": audited_at_text,
    }
    result["evidence_digest"] = hashlib.sha256(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return result


def run_geo_site_audit(
    database_path: str | Path,
    *,
    project_id: Any,
    url: Any = None,
    persist: Any = True,
    confirm_external_fetch: Any = False,
    request_id: Any,
    idempotency_key: Any,
    http_client: Any | None = None,
    dns_resolver: Callable[..., Any] = socket.getaddrinfo,
    allow_proxy_fake_ip: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    audit_deadline = time.monotonic() + MAX_AUDIT_SECONDS
    if not isinstance(dns_resolver, _AuditDnsCache):
        dns_resolver = _AuditDnsCache(dns_resolver)
    normalized_project_id = _positive_integer(project_id, "project_id")
    _require_external_fetch_confirmation(confirm_external_fetch)
    normalized_request_id = _request_identifier(request_id, "request_id")
    normalized_idempotency_key = _request_identifier(
        idempotency_key,
        "idempotency_key",
    )
    with closing(_connect(database_path)) as conn:
        project = conn.execute(
            "SELECT id, website FROM projects WHERE id = ?",
            (normalized_project_id,),
        ).fetchone()
    if project is None:
        raise GEOSiteAuditError("品牌项目不存在")
    project_url = str(project["website"] or "").strip()
    if not project_url:
        raise GEOSiteAuditError("品牌项目尚未填写公开网站地址")
    normalized_project_url = _normalize_public_url(
        project_url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    target_url = str(url or normalized_project_url).strip()
    normalized_target_url = _normalize_public_url(
        target_url,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=audit_deadline,
    )
    if not _same_site(normalized_project_url, normalized_target_url):
        raise GEOSiteAuditError("站点审计 URL 必须与品牌项目官网属于同一站点")
    should_persist = _boolean(persist, "persist")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "project_id": normalized_project_id,
                "url": normalized_target_url,
                "audit_version": GEO_SITE_AUDIT_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if should_persist:
        replay = _begin_or_replay_audit(
            database_path,
            project_id=normalized_project_id,
            request_id=normalized_request_id,
            idempotency_key=normalized_idempotency_key,
            request_fingerprint=fingerprint,
            requested_url=normalized_target_url,
        )
        if replay is not None:
            return replay
    result = audit_geo_site(
        normalized_target_url,
        http_client=http_client,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        now=now,
        _deadline=audit_deadline,
    )
    result["project_id"] = normalized_project_id
    result["request_id"] = normalized_request_id
    result["idempotency_key"] = normalized_idempotency_key
    result["request_fingerprint"] = fingerprint
    if not should_persist:
        return result

    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    run_status = _stored_run_status(result)
    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute(
                """
                UPDATE geo_site_audit_runs
                SET final_url = ?, status = ?, result_json = ?, updated_at = ?
                WHERE request_id = ? AND idempotency_key = ?
                """,
                (
                    result["final_url"],
                    run_status,
                    encoded,
                    result["audited_at"],
                    normalized_request_id,
                    normalized_idempotency_key,
                ),
            )
            row = conn.execute(
                "SELECT id FROM geo_site_audit_runs WHERE request_id = ?",
                (normalized_request_id,),
            ).fetchone()
            if row is None:
                raise GEOSiteAuditError("站点审计记录未能保存")
            result["id"] = int(row["id"])
            result["run_status"] = run_status
            conn.execute(
                "UPDATE geo_site_audit_runs SET result_json = ? WHERE id = ?",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    result["id"],
                ),
            )
    return result


def list_geo_site_audit_runs(
    database_path: str | Path,
    *,
    project_id: Any,
    limit: Any = DEFAULT_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    normalized_project_id = _positive_integer(project_id, "project_id")
    normalized_limit = _history_limit(limit)
    with closing(_connect(database_path)) as conn:
        if conn.execute(
            "SELECT 1 FROM projects WHERE id = ?",
            (normalized_project_id,),
        ).fetchone() is None:
            raise GEOSiteAuditError("品牌项目不存在")
        rows = conn.execute(
            """
            SELECT *
            FROM geo_site_audit_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_project_id, normalized_limit),
        ).fetchall()
    return [_decode_audit_row(row) for row in rows]


def get_geo_site_audit_run(
    database_path: str | Path,
    run_id: Any,
) -> dict[str, Any]:
    normalized_run_id = _positive_integer(run_id, "run_id")
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM geo_site_audit_runs
            WHERE id = ?
            """,
            (normalized_run_id,),
        ).fetchone()
    if row is None:
        raise GEOSiteAuditNotFoundError("站点审计记录不存在")
    return _decode_audit_row(row)


def _safe_fetch(
    http_client: Any,
    url: str,
    *,
    dns_resolver: Callable[..., Any],
    allow_proxy_fake_ip: bool,
    deadline: float | None = None,
) -> _FetchResult:
    if deadline is not None and time.monotonic() >= deadline:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "audit_deadline_exceeded",
        )
    try:
        requested_url = _normalize_public_url(
            url,
            dns_resolver=dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=deadline,
        )
    except GEOSiteAuditDNSTimeoutError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "dns_resolution_timeout",
        )
    except GEOSiteAuditTimeoutError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "audit_deadline_exceeded",
        )
    current = requested_url
    redirects: list[str] = []
    for _hop in range(MAX_REDIRECTS + 1):
        remaining = _remaining_seconds(deadline)
        if remaining is not None and remaining <= 0:
            return _FetchResult(
                requested_url,
                current,
                None,
                {},
                b"",
                tuple(redirects),
                "audit_deadline_exceeded",
            )
        connect_timeout = 5.0
        read_timeout = 15.0
        if remaining is not None:
            connect_timeout = min(connect_timeout, max(0.1, remaining))
            read_timeout = min(read_timeout, max(0.1, remaining))
        try:
            response = http_client.get(
                current,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.1",
                    "User-Agent": f"GEO-MVP-SiteAudit/{GEO_SITE_AUDIT_VERSION}",
                },
                timeout=(connect_timeout, read_timeout),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            error = (
                "audit_deadline_exceeded"
                if deadline is not None and time.monotonic() >= deadline
                else f"network_error:{type(exc).__name__}"
            )
            return _FetchResult(
                requested_url,
                current,
                None,
                {},
                b"",
                tuple(redirects),
                error,
            )
        status_code: int | None = None
        headers: dict[str, str] = {}
        try:
            status_code = int(response.status_code)
            headers = _coalesced_response_headers(response.headers)
            if status_code in _REDIRECT_STATUSES:
                location = str(headers.get("location") or "").strip()
                if not location:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "redirect_without_location",
                    )
                try:
                    next_url = _normalize_public_url(
                        urljoin(current, location),
                        dns_resolver=dns_resolver,
                        allow_proxy_fake_ip=allow_proxy_fake_ip,
                        deadline=deadline,
                    )
                except GEOSiteAuditDNSTimeoutError:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "dns_resolution_timeout",
                    )
                except GEOSiteAuditTimeoutError:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "audit_deadline_exceeded",
                    )
                except GEOSiteAuditError:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "unsafe_redirect_target",
                    )
                if (
                    urlsplit(current).scheme == "https"
                    and urlsplit(next_url).scheme == "http"
                ):
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "insecure_redirect_downgrade",
                    )
                if not _same_site(requested_url, next_url):
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "cross_site_redirect_rejected",
                    )
                redirects.append(next_url)
                current = next_url
                continue
            length = _optional_non_negative_integer(headers.get("content-length"))
            if length is not None and length > MAX_RESPONSE_BYTES:
                return _FetchResult(
                    requested_url,
                    current,
                    status_code,
                    headers,
                    b"",
                    tuple(redirects),
                    "response_too_large",
                )
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if deadline is not None and time.monotonic() >= deadline:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "audit_deadline_exceeded",
                    )
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    return _FetchResult(
                        requested_url,
                        current,
                        status_code,
                        headers,
                        b"",
                        tuple(redirects),
                        "response_too_large",
                    )
            return _FetchResult(
                requested_url,
                current,
                status_code,
                headers,
                bytes(body),
                tuple(redirects),
                "",
            )
        except requests.RequestException as exc:
            error = (
                "audit_deadline_exceeded"
                if deadline is not None and time.monotonic() >= deadline
                else f"network_error:{type(exc).__name__}"
            )
            return _FetchResult(
                requested_url,
                current,
                status_code,
                headers,
                b"",
                tuple(redirects),
                error,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    return _FetchResult(
        requested_url,
        current,
        None,
        {},
        b"",
        tuple(redirects),
        "too_many_redirects",
    )


def _try_safe_fetch(
    http_client: Any,
    url: str,
    *,
    dns_resolver: Callable[..., Any],
    allow_proxy_fake_ip: bool,
    deadline: float | None = None,
) -> _FetchResult:
    try:
        return _safe_fetch(
            http_client,
            url,
            dns_resolver=dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=deadline,
        )
    except GEOSiteAuditError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "unsafe_or_unresolvable_url",
        )


def _try_same_site_fetch(
    http_client: Any,
    url: str,
    *,
    site_url: str,
    dns_resolver: Callable[..., Any],
    allow_proxy_fake_ip: bool,
    deadline: float | None = None,
) -> _FetchResult:
    try:
        normalized = _normalize_public_url(
            url,
            dns_resolver=dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=deadline,
        )
        if not _same_site(site_url, normalized):
            raise GEOSiteAuditError("resource must remain on the audited site")
        return _safe_fetch(
            http_client,
            normalized,
            dns_resolver=dns_resolver,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            deadline=deadline,
        )
    except GEOSiteAuditDNSTimeoutError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "dns_resolution_timeout",
        )
    except GEOSiteAuditTimeoutError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "audit_deadline_exceeded",
        )
    except GEOSiteAuditError:
        return _FetchResult(
            str(url or ""),
            str(url or ""),
            None,
            {},
            b"",
            (),
            "cross_site_or_unsafe_resource",
        )


def _fetch_attempt_count(result: _FetchResult) -> int:
    if (
        result.status_code is None
        and not result.redirects
        and result.error in {
            "audit_deadline_exceeded",
            "cross_site_or_unsafe_resource",
            "dns_resolution_timeout",
            "unsafe_or_unresolvable_url",
        }
    ):
        return 0
    if result.error == "too_many_redirects":
        return len(result.redirects)
    return len(result.redirects) + 1


def _normalize_public_url(
    value: Any,
    *,
    dns_resolver: Callable[..., Any],
    allow_proxy_fake_ip: bool = False,
    deadline: float | None = None,
) -> str:
    normalized, _addresses = _validated_public_url(
        value,
        dns_resolver=dns_resolver,
        allow_proxy_fake_ip=allow_proxy_fake_ip,
        deadline=deadline,
    )
    return normalized


def _validated_public_url(
    value: Any,
    *,
    dns_resolver: Callable[..., Any],
    allow_proxy_fake_ip: bool = False,
    deadline: float | None = None,
) -> tuple[str, tuple[Any, ...]]:
    remaining = _remaining_seconds(deadline)
    if remaining is not None and remaining <= 0:
        raise GEOSiteAuditTimeoutError("站点审计已超过总时限")
    candidate = str(value or "").strip()
    if not candidate:
        raise GEOSiteAuditError("站点 URL 不能为空")
    if len(candidate) > MAX_URL_LENGTH:
        raise GEOSiteAuditError("站点 URL 过长")
    if "\\" in candidate or any(ord(character) < 32 for character in candidate):
        raise GEOSiteAuditError("站点 URL 格式无效")
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError as exc:
        raise GEOSiteAuditError("站点 URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise GEOSiteAuditError("站点 URL 必须是完整的 http 或 https 公网地址")
    if parsed.username or parsed.password:
        raise GEOSiteAuditError("站点 URL 不允许包含用户名或密码")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise GEOSiteAuditError("站点 URL 只允许标准 HTTP/HTTPS 端口")
    if hostname == "localhost" or hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        raise GEOSiteAuditError("站点 URL 不能指向本机或内部网络")
    if any(hostname == item or hostname.endswith(f".{item}") for item in _IP_MAPPING_DOMAINS):
        raise GEOSiteAuditError("站点 URL 使用了高风险 IP 映射域名")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_address(literal) or _is_proxy_fake_ip(literal):
            raise GEOSiteAuditError("站点 URL 不能指向私网或保留地址")
        addresses = [literal]
    else:
        addresses = _resolve_addresses(
            hostname,
            expected_port,
            dns_resolver=dns_resolver,
            deadline=deadline,
        )
        if not addresses:
            raise GEOSiteAuditError("无法安全解析站点域名")
        if not all(
            _is_public_address(item)
            or (allow_proxy_fake_ip and _is_proxy_fake_ip(item))
            for item in addresses
        ):
            raise GEOSiteAuditError("站点域名解析到了私网或保留地址")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise GEOSiteAuditError("站点域名格式无效") from exc
    rendered_hostname = (
        f"[{ascii_hostname}]"
        if literal is not None and literal.version == 6
        else ascii_hostname
    )
    netloc = rendered_hostname if port is None else f"{rendered_hostname}:{port}"
    path = parsed.path or "/"
    return (
        urlunsplit((parsed.scheme, netloc, path, parsed.query, "")),
        tuple(addresses),
    )


def _resolve_addresses(
    hostname: str,
    port: int,
    *,
    dns_resolver: Callable[..., Any],
    deadline: float | None = None,
) -> list[Any]:
    remaining = _remaining_seconds(deadline)
    if remaining is not None and remaining <= 0:
        raise GEOSiteAuditTimeoutError("站点审计已超过总时限")
    if deadline is None:
        try:
            rows = dns_resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError:
            return []
    else:
        if not _DNS_RESOLUTION_SLOTS.acquire(blocking=False):
            raise GEOSiteAuditError("DNS 解析资源繁忙，请稍后重试")
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                resolved = dns_resolver(hostname, port, type=socket.SOCK_STREAM)
                result_queue.put_nowait(("ok", resolved))
            except Exception as exc:  # resolver runs behind a failure-closed boundary
                result_queue.put_nowait(("error", exc))
            finally:
                _DNS_RESOLUTION_SLOTS.release()

        threading.Thread(
            target=resolve,
            name="geo-site-audit-dns",
            daemon=True,
        ).start()
        timeout_seconds = min(MAX_DNS_RESOLUTION_SECONDS, max(0.001, remaining or 0.0))
        try:
            outcome, payload = result_queue.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise GEOSiteAuditDNSTimeoutError("站点审计 DNS 解析超时") from exc
        if outcome == "error":
            if isinstance(payload, OSError):
                return []
            raise GEOSiteAuditError("无法安全解析站点域名") from payload
        rows = payload
    addresses: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        try:
            value = str(row[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(value)
            address = getattr(address, "ipv4_mapped", None) or address
        except (IndexError, TypeError, ValueError):
            return []
        if str(address) not in seen:
            seen.add(str(address))
            addresses.append(address)
            if len(addresses) > MAX_RESOLVED_ADDRESSES:
                return []
    return addresses


def _is_public_address(address: Any) -> bool:
    return bool(
        address.is_global
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
    )


def _is_proxy_fake_ip(address: Any) -> bool:
    return address.version == 4 and address in _PROXY_FAKE_IP_NETWORK


def _origin_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _parse_robots_groups(
    text: str,
) -> list[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]]]:
    groups: list[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    rules_started = False

    def flush() -> None:
        nonlocal agents, rules, rules_started
        if agents:
            groups.append((tuple(dict.fromkeys(agents)), tuple(rules)))
        agents = []
        rules = []
        rules_started = False

    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, raw_value = line.split(":", 1)
        field = field.strip().casefold()
        value = raw_value.strip()
        if field == "user-agent":
            if rules_started:
                flush()
            if value:
                agents.append(value.casefold())
            continue
        if field not in {"allow", "disallow"} or not agents:
            continue
        rules_started = True
        # An empty Disallow has no blocking effect.  Empty Allow is likewise
        # not a meaningful path rule.
        if value:
            rules.append((field == "allow", value))
    flush()
    return groups


def _normalize_robots_octets(value: str) -> str:
    """Normalize URI octets for RFC 9309 matching while retaining * and $."""

    output: list[str] = []
    index = 0
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    while index < len(value):
        character = value[index]
        if (
            character == "%"
            and index + 2 < len(value)
            and re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3])
        ):
            byte_value = int(value[index + 1 : index + 3], 16)
            decoded = chr(byte_value)
            if byte_value < 128 and decoded in unreserved:
                output.append(decoded)
            else:
                output.append(f"%{byte_value:02X}")
            index += 3
            continue
        if ord(character) > 127:
            output.extend(f"%{item:02X}" for item in character.encode("utf-8"))
        elif character == "%":
            output.append("%25")
        else:
            output.append(character)
        index += 1
    return "".join(output)


def _robots_rule_matches(rule: str, path_with_query: str) -> bool:
    normalized_rule = _normalize_robots_octets(rule)
    normalized_target = _normalize_robots_octets(path_with_query)
    anchored = normalized_rule.endswith("$")
    pattern = normalized_rule[:-1] if anchored else normalized_rule
    expression = re.escape(pattern).replace(r"\*", ".*")
    expression = "^" + expression + ("$" if anchored else "")
    return re.search(expression, normalized_target) is not None


def _robots_rule_specificity(rule: str) -> int:
    normalized = _normalize_robots_octets(rule).rstrip("$").replace("*", "")
    return len(re.findall(r"%[0-9A-F]{2}|.", normalized))


def _robots_can_fetch(
    groups: list[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]]],
    user_agent: str,
    target_url: str,
) -> bool:
    normalized_agent = user_agent.casefold()
    matched: list[tuple[int, tuple[tuple[bool, str], ...]]] = []
    wildcard_rules: list[tuple[tuple[bool, str], ...]] = []
    for agents, rules in groups:
        specific_lengths = [
            len(agent)
            for agent in agents
            if agent != "*" and agent in normalized_agent
        ]
        if specific_lengths:
            matched.append((max(specific_lengths), rules))
        elif "*" in agents:
            wildcard_rules.append(rules)
    if matched:
        best_agent_length = max(item[0] for item in matched)
        selected_rules = [
            rules for length, rules in matched if length == best_agent_length
        ]
    else:
        selected_rules = wildcard_rules
    parsed = urlsplit(target_url)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    matches: list[tuple[int, bool]] = []
    for rules in selected_rules:
        for allowed, rule in rules:
            if _robots_rule_matches(rule, target):
                specificity = _robots_rule_specificity(rule)
                matches.append((specificity, allowed))
    if not matches:
        return True
    best_specificity = max(item[0] for item in matches)
    # RFC 9309 gives Allow precedence when equally specific rules conflict.
    return any(allowed for specificity, allowed in matches if specificity == best_specificity)


def _analyze_robots(
    result: _FetchResult,
    *,
    target_url: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    resource = _resource_summary(result, kind="robots")
    sitemap_urls: list[str] = []
    if result.status_code == 404:
        resource["status"] = "absent"
        source = "implicit_allow_no_robots"
        allowed_by_agent = {item[0]: True for item in _CRAWLERS}
    elif result.ok:
        text = result.text
        try:
            if re.search(
                r"<\s*(?:!doctype\s+html|html|body)\b",
                text[:1024],
                re.I,
            ):
                raise ValueError("robots endpoint returned HTML")
            groups = _parse_robots_groups(text)
            allowed_by_agent = {
                item[0]: _robots_can_fetch(groups, item[0], target_url)
                for item in _CRAWLERS
            }
            source = "robots_txt"
            resource["status"] = "present"
            for line in text.splitlines():
                match = re.match(r"^\s*sitemap\s*:\s*(\S+)\s*$", line, re.I)
                if match:
                    sitemap_urls.append(urljoin(result.final_url, match.group(1)))
        except (TypeError, ValueError):
            allowed_by_agent = {item[0]: None for item in _CRAWLERS}
            source = "robots_parse_error"
            resource["status"] = "invalid"
    else:
        allowed_by_agent = {item[0]: None for item in _CRAWLERS}
        source = "robots_unavailable"
        resource["status"] = "unavailable"
    crawler_access = [
        {
            "user_agent": user_agent,
            "label": label,
            "purpose": purpose,
            "allowed": allowed_by_agent[user_agent],
            "source": source,
        }
        for user_agent, label, purpose in _CRAWLERS
    ]
    return crawler_access, resource, sitemap_urls


def _analyze_sitemap(result: _FetchResult) -> dict[str, Any]:
    resource = _resource_summary(result, kind="sitemap")
    resource["valid_xml"] = False
    resource["root_type"] = ""
    if result.error == "cross_site_or_unsafe_resource":
        resource["status"] = "not_audited"
        resource["reason"] = "outside_same_site_security_boundary"
        return resource
    if not result.ok:
        resource["status"] = "absent" if result.status_code == 404 else "unavailable"
        return resource
    lowered = result.body[:16_384].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        resource["status"] = "invalid"
        resource["error"] = "xml_declarations_not_allowed"
        return resource
    try:
        root = ElementTree.fromstring(result.body)
    except ElementTree.ParseError:
        resource["status"] = "invalid"
        return resource
    root_type = str(root.tag).rsplit("}", 1)[-1].casefold()
    resource["root_type"] = root_type
    resource["valid_xml"] = root_type in {"urlset", "sitemapindex"}
    resource["status"] = "present" if resource["valid_xml"] else "invalid"
    return resource


def _resource_summary(result: _FetchResult, *, kind: str) -> dict[str, Any]:
    status = "present" if result.ok else "unavailable"
    if result.status_code == 404:
        status = "absent"
    return {
        "kind": kind,
        "url": result.final_url,
        "status": status,
        "http_status": result.status_code,
        "content_type": str(result.headers.get("content-type") or "").split(";", 1)[0],
        "bytes": len(result.body),
        "error": result.error,
        "sha256": hashlib.sha256(result.body).hexdigest() if result.body else "",
    }


def _structured_data_summary(blocks: Iterable[str]) -> dict[str, Any]:
    valid_blocks = 0
    invalid_blocks = 0
    types: set[str] = set()
    recommended_entity_complete = False
    for raw in blocks:
        if not raw:
            invalid_blocks += 1
            continue
        try:
            payload = json.loads(raw)
            if not _has_schema_context(payload):
                raise ValueError("missing schema.org @context")
            _collect_schema_types(payload, types)
            recommended_entity_complete = (
                recommended_entity_complete
                or _has_meaningful_recommended_entity(payload)
            )
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
            invalid_blocks += 1
            continue
        valid_blocks += 1
    return {
        "valid_blocks": valid_blocks,
        "invalid_blocks": invalid_blocks,
        "types": sorted(types),
        "recommended_type_present": bool(types & _RECOMMENDED_SCHEMA_TYPES),
        "recommended_entity_complete": recommended_entity_complete,
    }


def _has_schema_context(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_schema_context(item) for item in value)
    if not isinstance(value, Mapping):
        return False
    context = value.get("@context")
    candidates: list[Any]
    if isinstance(context, list):
        candidates = list(context)
    else:
        candidates = [context]
    for candidate in candidates:
        if isinstance(candidate, str) and _is_schema_context_reference(candidate):
            return True
        if (
            isinstance(candidate, Mapping)
            and isinstance(candidate.get("@vocab"), str)
            and _is_schema_context_reference(candidate["@vocab"])
        ):
            return True
    return False


def _is_schema_context_reference(value: str) -> bool:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() in {"http", "https"}
        and hostname in {"schema.org", "www.schema.org"}
        and parsed.username is None
        and parsed.password is None
        and (port is None or port == (443 if parsed.scheme.casefold() == "https" else 80))
    )


def _has_meaningful_recommended_entity(value: Any) -> bool:
    if isinstance(value, Mapping):
        schema_type = value.get("@type")
        types = (
            {schema_type.strip()}
            if isinstance(schema_type, str) and schema_type.strip()
            else {
                str(item).strip()
                for item in schema_type
                if str(item).strip()
            }
            if isinstance(schema_type, list)
            else set()
        )
        evidence_keys = {
            "@id",
            "name",
            "headline",
            "description",
            "url",
            "mainEntity",
            "itemListElement",
        }
        if types & _RECOMMENDED_SCHEMA_TYPES and any(
            key in value and value.get(key) not in (None, "", [], {})
            for key in evidence_keys
        ):
            return True
        return any(_has_meaningful_recommended_entity(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_recommended_entity(item) for item in value)
    return False


def _collect_schema_types(value: Any, target: set[str]) -> None:
    if isinstance(value, Mapping):
        schema_type = value.get("@type")
        if isinstance(schema_type, str) and schema_type.strip():
            target.add(schema_type.strip())
        elif isinstance(schema_type, list):
            target.update(str(item).strip() for item in schema_type if str(item).strip())
        for child in value.values():
            _collect_schema_types(child, target)
    elif isinstance(value, list):
        for child in value:
            _collect_schema_types(child, target)


def _build_checks(
    *,
    page: _FetchResult,
    parser: _PageParser,
    parser_error: str,
    crawler_access: list[dict[str, Any]],
    sitemap_resources: list[dict[str, Any]],
    structured_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    content_type = str(page.headers.get("content-type") or "").casefold()
    page_access_ok = page.ok and ("html" in content_type or page.text.lstrip().startswith("<"))
    page_evidence = (
        f"HTTP {page.status_code} · {len(page.body)} bytes · {content_type or 'content-type 未声明'}"
        if page.status_code is not None
        else f"请求失败：{page.error or 'unknown'}"
    )
    checks = [
        _check("page_access", "页面可公开获取", 25, 25 if page_access_ok else 0, page_evidence),
    ]

    robots_meta = " ".join(
        [parser.meta.get("robots", ""), parser.meta.get("googlebot", "")]
    ).casefold()
    x_robots = str(page.headers.get("x-robots-tag") or "").casefold()
    index_directives = {
        item
        for item in re.split(r"[^a-z0-9_-]+", f"{robots_meta} {x_robots}")
        if item
    }
    noindex = bool(index_directives & {"noindex", "none"})
    checks.append(
        _check(
            "indexability",
            "页面索引指令",
            15,
            0 if noindex else 15,
            "检测到 noindex 指令" if noindex else "未检测到 noindex 指令",
        )
    )

    retrieval = {
        item["user_agent"]: item
        for item in crawler_access
        if item["user_agent"] in _RETRIEVAL_CRAWLERS
    }
    ai_retrieval = [
        retrieval[item]
        for item in sorted(_AI_RETRIEVAL_CRAWLERS)
        if item in retrieval
    ]
    known_ai = [item for item in ai_retrieval if item["allowed"] is not None]
    denied_ai = [item for item in known_ai if item["allowed"] is False]
    google = retrieval.get("Googlebot")
    if len(known_ai) != len(_AI_RETRIEVAL_CRAWLERS):
        crawler_score = 5
        crawler_evidence = "robots.txt 无法确认，AI 检索爬虫规则未知"
    elif len(denied_ai) == len(_AI_RETRIEVAL_CRAWLERS):
        crawler_score = 0
        crawler_evidence = "三个专用 AI 搜索/引用爬虫均被拒绝"
    elif denied_ai:
        crawler_score = round(
            15
            * (len(_AI_RETRIEVAL_CRAWLERS) - len(denied_ai))
            / len(_AI_RETRIEVAL_CRAWLERS)
        )
        crawler_evidence = "部分专用 AI 搜索/引用爬虫被拒绝：" + "、".join(
            item["user_agent"] for item in denied_ai
        )
    elif google is not None and google["allowed"] is False:
        crawler_score = 12
        crawler_evidence = "专用 AI 搜索爬虫允许，但 Googlebot 被拒绝"
    else:
        crawler_score = 15
        crawler_evidence = "已检查的 AI 检索爬虫规则允许访问目标页"
    checks.append(_check("crawler_access", "AI 检索爬虫规则", 15, crawler_score, crawler_evidence))

    visible_text = " ".join(parser.visible_text_parts)
    main_text = " ".join(parser.main_text_parts)
    extractable_length = len(main_text or visible_text)
    extractable_score = 0
    if page_access_ok:
        extractable_score += 7 if extractable_length >= 500 else 4 if extractable_length >= 200 else 0
        extractable_score += 4 if parser.heading_counts["h1"] >= 1 else 0
        extractable_score += 2 if parser.has_main or parser.has_article else 0
        extractable_score += 2 if parser.heading_counts["h2"] + parser.heading_counts["h3"] >= 1 else 0
    checks.append(
        _check(
            "extractable_content",
            "无需 JavaScript 的正文可抽取性",
            15,
            extractable_score,
            f"可见正文约 {extractable_length} 字符 · H1 {parser.heading_counts['h1']} · H2/H3 {parser.heading_counts['h2'] + parser.heading_counts['h3']}"
            + (f" · HTML 解析异常 {parser_error}" if parser_error else ""),
        )
    )

    canonical_score, canonical_evidence, canonical_blocks = _canonical_check(
        page.final_url,
        parser.canonical_urls,
    )
    checks.append(
        _check(
            "canonical",
            "Canonical 主地址",
            10,
            canonical_score,
            canonical_evidence,
            blocking=canonical_blocks,
        )
    )

    valid_blocks = int(structured_data.get("valid_blocks") or 0)
    invalid_blocks = int(structured_data.get("invalid_blocks") or 0)
    if (
        valid_blocks
        and structured_data.get("recommended_type_present")
        and structured_data.get("recommended_entity_complete")
    ):
        schema_score = 10
    elif valid_blocks:
        schema_score = 6
    else:
        schema_score = 0
    schema_types = "、".join(structured_data.get("types") or []) or "无"
    checks.append(
        _check(
            "structured_data",
            "Schema.org JSON-LD",
            10,
            schema_score,
            f"有效 {valid_blocks} 块 · 无效 {invalid_blocks} 块 · 类型 {schema_types}",
        )
    )

    valid_sitemaps = [item for item in sitemap_resources if item.get("valid_xml")]
    checks.append(
        _check(
            "sitemap",
            "Sitemap 发现入口",
            5,
            5 if valid_sitemaps else 0,
            f"发现 {len(valid_sitemaps)} 个有效 Sitemap" if valid_sitemaps else "未发现有效 XML Sitemap",
        )
    )

    has_title = bool(" ".join(parser.title_parts).strip())
    has_description = bool(parser.meta.get("description", "").strip())
    metadata_points = 0
    metadata_points += 2 if has_title else 0
    metadata_points += 2 if has_description else 0
    metadata_points += 1 if parser.html_lang else 0
    checks.append(
        _check(
            "metadata",
            "基础语义元数据",
            5,
            metadata_points,
            f"title {'有' if has_title else '无'} · description {'有' if has_description else '无'} · lang {parser.html_lang or '无'}",
        )
    )
    return checks


def _canonical_check(
    page_url: str,
    canonical_urls: list[str],
) -> tuple[int, str, bool]:
    if not canonical_urls:
        return 0, "页面没有声明 canonical", False
    normalized_candidates: list[str] = []
    for raw_candidate in canonical_urls:
        candidate = urljoin(page_url, str(raw_candidate or "").strip())
        try:
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").rstrip(".").casefold()
            port = parsed.port
        except ValueError:
            return 0, "canonical URL 格式无效", True
        scheme = parsed.scheme.casefold()
        expected_port = 443 if scheme == "https" else 80
        if (
            scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and port != expected_port)
        ):
            return 0, "canonical 必须是无凭据的标准 HTTP/HTTPS URL", True
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return 0, "canonical URL 格式无效", True
        rendered_hostname = (
            f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
        )
        normalized_netloc = (
            rendered_hostname if port is None else f"{rendered_hostname}:{port}"
        )
        normalized_candidates.append(
            urlunsplit((scheme, normalized_netloc, parsed.path or "/", parsed.query, ""))
        )

    unique_candidates = list(dict.fromkeys(normalized_candidates))
    if len(unique_candidates) != 1:
        return 0, "页面声明了互相冲突的 canonical URL", True

    canonical = unique_candidates[0]
    page = urlsplit(page_url)
    page_host = (page.hostname or "").rstrip(".").removeprefix("www.").casefold()
    canonical_parts = urlsplit(canonical)
    canonical_host = (
        (canonical_parts.hostname or "").rstrip(".").removeprefix("www.").casefold()
    )
    if canonical_host != page_host:
        return 0, f"canonical 指向其他域名：{canonical}", True
    if page.scheme.casefold() == "https" and canonical_parts.scheme.casefold() != "https":
        return 0, f"canonical 将 HTTPS 页面降级到 HTTP：{canonical}", True
    return 10, f"canonical 与目标站点一致：{canonical}", False


def _check(
    key: str,
    label: str,
    maximum: int,
    score: int,
    evidence: str,
    *,
    blocking: bool = False,
) -> dict[str, Any]:
    bounded = max(0, min(maximum, int(score)))
    if bounded == maximum:
        status = "pass"
    elif bounded == 0:
        status = "fail"
    else:
        status = "warning"
    return {
        "key": key,
        "label": label,
        "status": status,
        "score": bounded,
        "max_score": maximum,
        "evidence": evidence,
        "blocking": bool(blocking),
    }


def _recommendations(
    checks: list[dict[str, Any]],
    *,
    llms_present: bool,
    requested_scheme: str,
) -> list[dict[str, str]]:
    by_key = {item["key"]: item for item in checks}
    recommendations: list[dict[str, str]] = []

    def add(priority: str, code: str, title: str, action: str) -> None:
        recommendations.append(
            {"priority": priority, "code": code, "title": title, "action": action}
        )

    if by_key["page_access"]["status"] == "fail":
        add("P0", "page.public_fetch", "修复公开页面访问", "确保目标 URL 匿名访问返回 2xx HTML，并避免登录墙、循环跳转或超大响应。")
    if by_key["indexability"]["status"] == "fail":
        add("P0", "page.noindex", "移除非预期 noindex", "仅在确实需要隐藏页面时保留 noindex；公开知识页应检查 meta robots 和 X-Robots-Tag。")
    if by_key["crawler_access"]["status"] != "pass":
        add("P0", "robots.ai_retrieval", "检查 AI 检索爬虫规则", "分别核对 OAI-SearchBot、PerplexityBot、Claude-SearchBot 与 Googlebot；用户触发抓取和训练爬虫策略应单独决策。")
    if by_key["extractable_content"]["status"] != "pass":
        add("P0", "content.server_rendered", "提供服务端可抽取正文", "让标题、答案摘要、H1/H2 和核心证据在初始 HTML 中可见，不要只依赖客户端 JavaScript。")
    if by_key["canonical"]["status"] != "pass":
        add(
            "P0" if by_key["canonical"].get("blocking") else "P1",
            "page.canonical",
            "修复 canonical 归属" if by_key["canonical"].get("blocking") else "补齐稳定 canonical",
            "为页面声明唯一、公开且与最终内容一致的同站 canonical URL。",
        )
    if by_key["structured_data"]["status"] != "pass":
        add("P1", "schema.jsonld", "补齐有效 JSON-LD", "根据真实页面类型输出 Organization、WebSite、Article、Product 或 FAQPage，并保持 @id 与页面事实一致。")
    if by_key["sitemap"]["status"] != "pass":
        add("P1", "discovery.sitemap", "提供 XML Sitemap", "发布可访问的 sitemap.xml，并在 robots.txt 中声明其绝对地址。")
    if by_key["metadata"]["status"] != "pass":
        add("P2", "page.metadata", "补齐基础语义元数据", "提供唯一 title、准确 description 和页面语言 lang。")
    if requested_scheme != "https":
        add("P0", "transport.https", "迁移到 HTTPS", "将公开页面和所有规范地址统一到 HTTPS。")
    if not llms_present:
        add("P2", "discovery.llms_txt", "评估发布 llms.txt", "可把 /llms.txt 作为低成本机器可读入口，但它仍是提案，不能替代 sitemap、Schema 或真实采样。")
    return recommendations


def _grade(score: int, *, blocked: bool) -> str:
    if blocked:
        return "D"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _begin_or_replay_audit(
    database_path: str | Path,
    *,
    project_id: int,
    request_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    requested_url: str,
) -> dict[str, Any] | None:
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM geo_site_audit_runs
            WHERE request_id = ? OR idempotency_key = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (request_id, idempotency_key),
        ).fetchone()
        if row is not None:
            conn.commit()
            same_identity = (
                str(row["request_id"]) == request_id
                and str(row["idempotency_key"]) == idempotency_key
                and str(row["request_fingerprint"]) == request_fingerprint
                and int(row["project_id"]) == project_id
            )
            if not same_identity:
                raise GEOSiteAuditConflictError(
                    "request_id 或 Idempotency-Key 已用于其他站点审计请求"
                )
            if row["result_json"]:
                return _decode_audit_row(row)
            checkpoint = _decode_audit_row(row)
            if checkpoint["in_progress_expired"]:
                raise GEOSiteAuditConflictError(
                    "原站点审计请求已超时且结果未知，不会使用旧 request_id 或 "
                    "Idempotency-Key 自动重发；人工确认后请使用新的请求身份"
                )
            raise GEOSiteAuditConflictError(
                "相同站点审计请求仍在执行；请稍后按 request_id 查询原记录"
            )
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn.execute(
            """
            INSERT INTO geo_site_audit_runs (
                project_id, request_id, idempotency_key, request_fingerprint,
                requested_url, final_url, status, result_json,
                audit_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'started', NULL, ?, ?, ?)
            """,
            (
                project_id,
                request_id,
                idempotency_key,
                request_fingerprint,
                requested_url,
                requested_url,
                GEO_SITE_AUDIT_VERSION,
                created_at,
                created_at,
            ),
        )
        conn.commit()
    return None


def _stored_run_status(result: Mapping[str, Any]) -> str:
    if result.get("status") == "blocked":
        return "need_action"
    resources = result.get("resources")
    if isinstance(resources, Mapping):
        optional_resources = [resources.get("robots"), resources.get("llms_txt")]
        sitemaps = resources.get("sitemaps")
        if isinstance(sitemaps, list):
            optional_resources.extend(sitemaps)
        if any(
            isinstance(item, Mapping) and item.get("status") == "unavailable"
            for item in optional_resources
        ):
            return "partial"
    return "completed"


def _same_site(left: str, right: str) -> bool:
    def identity(value: str) -> tuple[str, int]:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold().removeprefix("www.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return hostname, port

    left_host, left_port = identity(left)
    right_host, right_port = identity(right)
    standard_ports = {80, 443}
    return left_host == right_host and left_port in standard_ports and right_port in standard_ports


def _decode_audit_row(
    row: sqlite3.Row,
    *,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    if not row["result_json"] and str(row["status"]) == "started":
        age_seconds = _started_age_seconds(
            row["created_at"],
            current_time=current_time,
        )
        expired = age_seconds >= IN_PROGRESS_EXPIRY_SECONDS
        return {
            "id": int(row["id"]),
            "project_id": int(row["project_id"]),
            "request_id": str(row["request_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "request_fingerprint": str(row["request_fingerprint"]),
            "requested_url": str(row["requested_url"]),
            "final_url": str(row["final_url"]),
            "status": "outcome_unknown" if expired else "processing",
            "stored_status": "started",
            "run_status": "started",
            "result_available": False,
            "in_progress": not expired,
            "in_progress_age_seconds": age_seconds,
            "in_progress_expired": expired,
            "in_progress_expiry_seconds": IN_PROGRESS_EXPIRY_SECONDS,
            "outcome_unknown": expired,
            "score": None,
            "grade": "",
            "checks": [],
            "crawler_access": [],
            "structured_data": {},
            "resources": {},
            "recommendations": [],
            "boundaries": [
                (
                    "进程退出前没有写回最终结果；旧请求身份仅作为幂等墓碑保留，"
                    "不会自动重新访问站点。"
                    if expired
                    else "站点审计已建立本机检查点，最终结果尚未写回。"
                )
            ],
            "retry_policy": {
                "automatic_retry": False,
                "requires_new_identity": expired,
            },
            "audit_version": str(row["audit_version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
    try:
        payload = json.loads(row["result_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GEOSiteAuditError("站点审计历史无法读取") from exc
    if not isinstance(payload, dict):
        raise GEOSiteAuditError("站点审计历史格式无效")
    payload["id"] = int(row["id"])
    payload["project_id"] = int(row["project_id"])
    payload["audit_version"] = str(row["audit_version"])
    payload.setdefault("audited_at", str(row["created_at"]))
    return payload


def _started_age_seconds(
    created_at: Any,
    *,
    current_time: datetime | None = None,
) -> int:
    if not isinstance(created_at, str):
        raise GEOSiteAuditError("站点审计请求创建时间格式无效")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GEOSiteAuditError("站点审计请求创建时间格式无效") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise GEOSiteAuditError("站点审计请求创建时间必须使用 UTC 时区")
    observed_at = current_time or datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime):
        raise GEOSiteAuditError("current_time 必须是 datetime")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise GEOSiteAuditError("current_time 必须包含时区")
    return max(
        0,
        int((observed_at.astimezone(timezone.utc) - created).total_seconds()),
    )


def _positive_integer(value: Any, field: str) -> int:
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise GEOSiteAuditError(f"{field} 必须是正整数") from exc
    if normalized <= 0:
        raise GEOSiteAuditError(f"{field} 必须是正整数")
    return normalized


def _boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return True
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise GEOSiteAuditError(f"{field} 必须是布尔值")


def _require_external_fetch_confirmation(value: Any) -> None:
    confirmed = value is True or str(value).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not confirmed:
        raise GEOSiteAuditError(
            "必须明确确认本次站点审计将向品牌官网发送受限的公开 GET 请求"
        )


def _request_identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise GEOSiteAuditError(f"缺少 {field}")
    if len(normalized) > 200 or not re.fullmatch(r"[A-Za-z0-9._:-]+", normalized):
        raise GEOSiteAuditError(f"{field} 格式无效")
    return normalized


def _history_limit(value: Any) -> int:
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise GEOSiteAuditError("limit 必须是正整数") from exc
    if normalized <= 0:
        raise GEOSiteAuditError("limit 必须是正整数")
    return min(normalized, MAX_HISTORY_LIMIT)


def _optional_non_negative_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


__all__ = [
    "GEO_SITE_AUDIT_VERSION",
    "IN_PROGRESS_EXPIRY_SECONDS",
    "GEOSiteAuditError",
    "GEOSiteAuditConflictError",
    "GEOSiteAuditNotFoundError",
    "audit_geo_site",
    "get_geo_site_audit_run",
    "list_geo_site_audit_runs",
    "run_geo_site_audit",
]
