from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from conf import LOCAL_CHROME_PATH
from services.media_account_risk_service import classify_media_account_risk
from services.weibo_official_connector import (
    WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE,
    WeiboAuthorizationError,
    WeiboCatalogError,
    WeiboConnectorError,
    WeiboConnectorUnavailableError,
    WeiboOfficialConnector,
    normalize_weibo_public_status_url,
)


@dataclass(frozen=True)
class ReviewPagePolicy:
    name: str
    manage_url: str
    public_url_patterns: tuple[re.Pattern[str], ...]
    display_timezone: tzinfo | None
    read_rejection_details: bool = False


_CHINA_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


_POLICIES = {
    "toutiao": ReviewPagePolicy(
        "今日头条",
        "https://mp.toutiao.com/profile_v4/graphic/management",
        (re.compile(r"https?://(?:www\.)?toutiao\.com/(?:article|item)/\d+/?", re.I),),
        _CHINA_TIMEZONE,
    ),
    "baijiahao": ReviewPagePolicy(
        "百家号",
        "https://baijiahao.baidu.com/builder/rc/content?type=news",
        (re.compile(r"https?://baijiahao\.baidu\.com/s\?id=\d+", re.I),),
        _CHINA_TIMEZONE,
    ),
    "sohu": ReviewPagePolicy(
        "搜狐号",
        "https://mp.sohu.com/mpfe/v4/contentManagement/first/page",
        (re.compile(r"https?://(?:www\.)?sohu\.com/a/\d+_\d+/?", re.I),),
        _CHINA_TIMEZONE,
        read_rejection_details=True,
    ),
    "xiaohongshu": ReviewPagePolicy(
        "小红书",
        "https://creator.xiaohongshu.com/new/note-manager",
        (
            re.compile(
                r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-f0-9]{24}/?",
                re.I,
            ),
        ),
        _CHINA_TIMEZONE,
    ),
    "douyin": ReviewPagePolicy(
        "抖音",
        "https://creator.douyin.com/creator-micro/content/manage",
        (re.compile(r"https?://(?:www\.)?douyin\.com/(?:video|note)/\d+/?", re.I),),
        _CHINA_TIMEZONE,
    ),
    "kuaishou": ReviewPagePolicy(
        "快手",
        "https://cp.kuaishou.com/article/manage/video?status=2&from=publish",
        (re.compile(r"https?://(?:www\.)?kuaishou\.com/short-video/[0-9A-Za-z_-]{6,128}/?", re.I),),
        _CHINA_TIMEZONE,
    ),
    "bilibili": ReviewPagePolicy(
        "Bilibili",
        "https://member.bilibili.com/platform/upload-manager/article",
        (
            re.compile(
                r"https?://(?:www\.)?bilibili\.com/video/(?:BV[0-9A-Za-z]{10}|av\d+)/?",
                re.I,
            ),
        ),
        _CHINA_TIMEZONE,
    ),
    "tiktok": ReviewPagePolicy(
        "TikTok",
        "https://www.tiktok.com/tiktokstudio/content",
        (
            re.compile(
                r"https?://(?:www\.)?tiktok\.com/@[0-9A-Za-z_.]+/video/\d+/?",
                re.I,
            ),
        ),
        None,
    ),
}

_PUBLISHED_MARKERS = (
    "已公开",
    "已发布",
    "发布成功",
    "审核通过",
    "published",
    "approved",
    "live",
)
_NEGATED_PUBLISHED_MARKERS = (
    "未公开",
    "不公开",
    "非公开",
    "尚未公开",
    "暂未公开",
    "未发布",
    "尚未发布",
    "发布未成功",
    "not live",
    "not published",
    "not approved",
    "unpublished",
    "private",
    "draft",
)
_PROCESSING_MARKERS = (
    "审核中",
    "处理中",
    "待审核",
    "已提交",
    "平台处理",
    "auditing",
    "processing",
    "pending",
    "under review",
)
_REJECTED_MARKERS = (
    "未通过",
    "审核失败",
    "审核不通过",
    "已拒绝",
    "发布失败",
    "rejected",
    "not approved",
    "declined",
)
_LOGIN_URL_MARKERS = ("login", "signin", "passport", "auth")
_LOGIN_TEXT_MARKERS = (
    "登录后继续",
    "请登录",
    "重新登录",
    "扫码登录",
    "手机号登录",
    "log in",
    "sign in",
)
_VISIBLE_ALERT_SELECTORS = (
    '[role="alert"]:visible',
    '[class*="risk"]:visible',
    '[class*="warning"]:visible',
    '[class*="error"]:visible',
    '[class*="ban"]:visible',
    '[class*="verify"]:visible',
)
_REJECTION_DETAIL_ATTRIBUTES = (
    "title",
    "aria-label",
    "data-title",
    "data-original-title",
    "data-tooltip",
    "data-content",
)
_REJECTION_DETAIL_NODE_SELECTOR = ", ".join(
    f"[{attribute}]" for attribute in _REJECTION_DETAIL_ATTRIBUTES
)
_VISIBLE_TOOLTIP_SELECTOR = (
    '[role="tooltip"]:visible, '
    '.ant-tooltip:visible, '
    '.el-tooltip__popper:visible, '
    '[class*="tooltip"]:visible, '
    '[class*="popover"]:visible'
)
_SOHU_REJECTION_TEXT_SELECTOR = ".article-reson-text, .article-reason-text"
_REJECTION_HOVER_SELECTORS = (
    '[aria-describedby]',
    '[class*="question"]',
    '[class*="help"]',
    '[class*="tip"]',
    '[class*="reason"]',
    "svg",
    "i",
)
_GENERIC_REJECTION_LABELS = {
    "未通过",
    "审核未通过",
    "审核不通过",
    "审核失败",
    "已拒绝",
    "发布失败",
    "查看原因",
    "查看详情",
    "详情",
    "提示",
    "帮助",
    "问题说明",
    "编辑",
    "更多",
    "操作",
}


class PlatformReviewVerifier:
    """Read creator-side status without exposing a publish action.

    Every browser operation in this class is navigation or DOM read.  No click,
    form fill, keyboard operation, POST/PUT/PATCH/DELETE request or publisher
    adapter is available in the implementation.
    """

    def __init__(
        self,
        cookies_directory: str | Path,
        *,
        navigation_timeout_ms: int = 60_000,
        settle_ms: int = 2_500,
        weibo_connector_factory: Any = WeiboOfficialConnector,
    ):
        self.cookies_directory = Path(cookies_directory).expanduser().resolve()
        self.navigation_timeout_ms = int(navigation_timeout_ms)
        self.settle_ms = int(settle_ms)
        self.weibo_connector_factory = weibo_connector_factory
        if self.navigation_timeout_ms < 1_000:
            raise ValueError("只读核验导航超时不能少于 1000ms")
        if not 0 <= self.settle_ms <= 15_000:
            raise ValueError("只读核验等待时间必须在 0 到 15000ms")

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        platform = str(snapshot.get("platform") or "").strip().lower()
        title = str(snapshot.get("title") or "").strip()
        if not title:
            return {
                "state": "unsupported",
                "message": "任务缺少可用于精确匹配的发布标题，请人工核验",
            }
        if platform == "channels":
            return {
                "state": "unsupported",
                "message": "视频号当前没有可安全自动读取的稳定作品链接入口，请在视频号助手人工核验",
            }
        if platform == "zhihu":
            return self._verify_zhihu(snapshot, title)
        if platform == "weibo":
            return self._verify_weibo(snapshot, title)
        policy = _POLICIES.get(platform)
        if policy is None:
            return {
                "state": "unsupported",
                "message": f"{platform or '该平台'}尚未配置只读作品管理页核验器",
            }
        return self._verify_management_page(snapshot, title, policy)

    def _verify_weibo(
        self,
        snapshot: dict[str, Any],
        title: str,
    ) -> dict[str, Any]:
        marker_path = self._cookie_path(snapshot)
        if marker_path is None:
            return _session_expired_result("微博账号绑定标记不存在，请重新连接")
        if int(snapshot.get("account_status") or 0) != 1:
            return _session_expired_result("微博官方连接器授权已失效，请重新连接")
        try:
            connector = self.weibo_connector_factory()
            connector.verify_marker(marker_path)
            rows = connector.recent_status_rows(title, count=20)
        except WeiboConnectorUnavailableError:
            return {
                "state": "unsupported",
                "message": WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE,
            }
        except WeiboAuthorizationError as exc:
            return _session_expired_result(str(exc))
        except WeiboCatalogError as exc:
            return {"state": "unsupported", "message": str(exc)}
        except WeiboConnectorError as exc:
            return {
                "state": "error",
                "message": f"微博官方连接器只读核验失败：{exc}",
            }
        except Exception as exc:
            return {
                "state": "error",
                "message": f"微博官方连接器只读核验异常：{exc.__class__.__name__}",
            }
        if not rows:
            return {
                "state": "not_found",
                "message": "微博最近公开状态尚未出现本次精确标题",
            }
        guarded_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("title_match_confirmed") is not True:
                continue
            row_text = str(row.get("text") or "").strip()
            if not (
                row_text == title
                or row_text.startswith(f"{title} ")
                or row_text.startswith(f"{title}\n")
            ):
                continue
            public_urls = tuple(
                normalized
                for normalized in (
                    normalize_weibo_public_status_url(url)
                    for url in row.get("urls") or ()
                )
                if normalized
            )
            guarded = dict(row)
            if row.get("public_visibility_confirmed") is True and len(set(public_urls)) == 1:
                guarded["urls"] = tuple(dict.fromkeys(public_urls))
            else:
                guarded["text"] = f"{title} 公开可见性或公开作品链接未确认"
                guarded["urls"] = public_urls
            guarded_rows.append(guarded)
        if not guarded_rows:
            return {
                "state": "processing",
                "message": "微博只读核验没有返回可安全解释的状态条目，请人工核验",
            }
        return _resolve_exact_title_rows(
            snapshot,
            guarded_rows,
            platform_name="微博",
            public_url_patterns=(
                re.compile(r"https?://m\.weibo\.cn/detail/[1-9]\d*/?", re.I),
                re.compile(
                    r"https?://(?:www\.)?weibo\.com/[1-9]\d*/[0-9A-Za-z]{5,32}/?",
                    re.I,
                ),
            ),
            display_timezone=_CHINA_TIMEZONE,
        )

    def _verify_zhihu(
        self,
        snapshot: dict[str, Any],
        title: str,
    ) -> dict[str, Any]:
        cookie_path = self._cookie_path(snapshot)
        if cookie_path is None:
            return _session_expired_result("知乎账号 Cookie 文件不存在，请重新登录")
        if int(snapshot.get("account_status") or 0) != 1:
            return _session_expired_result("知乎登录会话已失效，请重新登录后恢复监测")

        # Import lazily so list/alert APIs never load Playwright.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_options())
            context = browser.new_context(
                storage_state=str(cookie_path),
                extra_http_headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.zhihu.com/creator",
                },
            )
            try:
                me = context.request.get(
                    "https://www.zhihu.com/api/v4/me",
                    timeout=self.navigation_timeout_ms,
                )
                if me.status in {401, 403}:
                    return _session_expired_result("知乎登录状态无效，请重新登录")
                if not me.ok:
                    return {
                        "state": "error",
                        "message": f"知乎身份只读接口暂不可用：HTTP {me.status}",
                    }
                me_data = me.json()
                token = str(me_data.get("url_token") or "").strip()
                if not token:
                    return _session_expired_result("知乎身份接口缺少账号标识，请重新登录")
                response = context.request.get(
                    f"https://www.zhihu.com/api/v4/members/{token}/articles"
                    "?offset=0&limit=20&sort_by=created",
                    timeout=self.navigation_timeout_ms,
                )
                if response.status in {401, 403}:
                    return _session_expired_result("知乎文章列表需要重新登录")
                if not response.ok:
                    return {
                        "state": "error",
                        "message": f"知乎文章列表只读接口暂不可用：HTTP {response.status}",
                    }
                payload = response.json()
                rows: list[dict[str, Any]] = []
                for article in payload.get("data") or ():
                    if str(article.get("title") or "").strip() != title:
                        continue
                    article_id = str(article.get("id") or "").strip()
                    url = str(article.get("url") or "").strip()
                    if not url and article_id.isdigit():
                        url = f"https://zhuanlan.zhihu.com/p/{article_id}"
                    intervals = []
                    for key in ("created", "created_time", "created_at"):
                        interval = _timestamp_value_interval(article.get(key))
                        if interval is not None:
                            intervals.append(interval)
                            break
                    rows.append(
                        {
                            "text": f"{title} 已发布 article_id={article_id}",
                            "urls": (url,) if url else (),
                            "external_ids": (article_id,) if article_id else (),
                            "time_intervals": tuple(intervals),
                        }
                    )
                if not rows:
                    return {
                        "state": "not_found",
                        "message": "知乎最近公开文章列表尚未出现精确标题",
                    }
                return _resolve_exact_title_rows(
                    snapshot,
                    rows,
                    platform_name="知乎",
                    public_url_patterns=(
                        re.compile(
                            r"https?://(?:zhuanlan|www)\.zhihu\.com/p/\d+/?",
                            re.I,
                        ),
                    ),
                    display_timezone=_CHINA_TIMEZONE,
                )
            except Exception as exc:
                return {
                    "state": "error",
                    "message": f"知乎只读核验异常：{exc.__class__.__name__}",
                }
            finally:
                context.close()
                browser.close()

    def _verify_management_page(
        self,
        snapshot: dict[str, Any],
        title: str,
        policy: ReviewPagePolicy,
    ) -> dict[str, Any]:
        cookie_path = self._cookie_path(snapshot)
        if cookie_path is None:
            return _session_expired_result(f"{policy.name}账号 Cookie 文件不存在，请重新登录")
        if int(snapshot.get("account_status") or 0) != 1:
            return _session_expired_result(f"{policy.name}登录会话已失效，请重新登录后恢复监测")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**self._launch_options())
            context = browser.new_context(storage_state=str(cookie_path))
            page = context.new_page()
            try:
                response = page.goto(
                    policy.manage_url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                if response is not None and response.status in {401, 403}:
                    return _session_expired_result(
                        f"{policy.name}作品管理页要求重新登录"
                    )
                if self.settle_ms:
                    page.wait_for_timeout(self.settle_ms)

                visible_risk = self._visible_account_risk(page)
                if visible_risk is not None:
                    return visible_risk

                current_url = str(page.url or "")
                if any(marker in current_url.lower() for marker in _LOGIN_URL_MARKERS):
                    return _session_expired_result(
                        f"{policy.name}作品管理页跳转登录，登录会话已失效"
                    )

                rows = self._wait_for_exact_title_rows(
                    page,
                    title,
                    policy.manage_url,
                    policy.public_url_patterns,
                    read_rejection_details=policy.read_rejection_details,
                )
                if not rows:
                    body = self._safe_text(page.locator("body"), limit=4_000)
                    if any(marker.lower() in body.lower() for marker in _LOGIN_TEXT_MARKERS):
                        return _session_expired_result(
                            f"{policy.name}作品管理页显示需要重新登录"
                        )
                    return {
                        "state": "not_found",
                        "message": f"{policy.name}当前作品管理页尚未出现精确标题",
                    }

                return _resolve_exact_title_rows(
                    snapshot,
                    rows,
                    platform_name=policy.name,
                    public_url_patterns=policy.public_url_patterns,
                    display_timezone=policy.display_timezone,
                )
            except Exception as exc:
                return {
                    "state": "error",
                    "message": f"{policy.name}只读核验异常：{exc.__class__.__name__}",
                }
            finally:
                context.close()
                browser.close()

    def _wait_for_exact_title_rows(
        self,
        page: Any,
        title: str,
        base_url: str,
        public_url_patterns: tuple[re.Pattern[str], ...],
        *,
        read_rejection_details: bool,
    ) -> list[dict[str, Any]]:
        """Poll the dynamic management list until the matching row has status.

        ``domcontentloaded`` is too early for creator pages whose article list is
        rendered after an API request.  Polling reads only the DOM and keeps the
        title match exact.  An unfinished row is returned when the bounded wait
        expires, so the caller stays conservative instead of guessing.
        """

        wait_budget_ms = min(12_000, max(1_000, self.navigation_timeout_ms // 4))
        deadline = monotonic() + wait_budget_ms / 1_000
        last_rows: list[dict[str, Any]] = []
        while True:
            rows = self._exact_title_rows(
                page,
                title,
                base_url,
                public_url_patterns,
            )
            if rows:
                last_rows = rows
                if any(_row_has_observable_status(row) for row in rows):
                    if read_rejection_details and any(
                        _contains_any(str(row.get("text") or ""), _REJECTED_MARKERS)
                        for row in rows
                    ):
                        detailed_rows = self._exact_title_rows(
                            page,
                            title,
                            base_url,
                            public_url_patterns,
                            read_rejection_details=True,
                        )
                        return detailed_rows or rows
                    return rows

            remaining_ms = int((deadline - monotonic()) * 1_000)
            if remaining_ms <= 0:
                return last_rows
            try:
                page.wait_for_timeout(min(400, remaining_ms))
            except Exception:
                return last_rows

    def _cookie_path(self, snapshot: dict[str, Any]) -> Path | None:
        value = str(snapshot.get("account_file") or "").strip()
        if not value:
            return None
        candidate = (self.cookies_directory / value).resolve()
        try:
            candidate.relative_to(self.cookies_directory)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _launch_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
                "--disable-infobars",
            ],
        }
        if LOCAL_CHROME_PATH:
            options["executable_path"] = LOCAL_CHROME_PATH
        return options

    @staticmethod
    def _visible_account_risk(page: Any) -> dict[str, Any] | None:
        for selector in _VISIBLE_ALERT_SELECTORS:
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 20)):
                    candidate = locator.nth(index)
                    if not candidate.is_visible():
                        continue
                    text = PlatformReviewVerifier._safe_text(candidate, limit=1_000)
                    risk = classify_media_account_risk(text)
                    if risk is not None and risk["category"] != "network_error":
                        return {
                            "state": "risk",
                            "message": risk["reason"],
                            "evidence": text,
                        }
            except Exception:
                continue
        return None

    @staticmethod
    def _exact_title_rows(
        page: Any,
        title: str,
        base_url: str,
        public_url_patterns: tuple[re.Pattern[str], ...],
        *,
        read_rejection_details: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            candidates = page.get_by_text(title, exact=True)
            count = min(candidates.count(), 10)
        except Exception:
            return []

        rows: list[dict[str, Any]] = []
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
            except Exception:
                continue
            status_candidates: list[tuple[int, Any, str, tuple[str, ...]]] = []
            url_fallbacks: list[tuple[int, Any, str, tuple[str, ...]]] = []
            for depth in range(1, 7):
                try:
                    node = candidate.locator("xpath=" + "/.." * depth)
                    text = PlatformReviewVerifier._safe_text(node, limit=2_000)
                    if title not in text or len(text) > 1_500:
                        continue
                    anchors = node.locator("a[href]")
                    urls = []
                    for anchor_index in range(min(anchors.count(), 20)):
                        href = str(anchors.nth(anchor_index).get_attribute("href") or "").strip()
                        if href:
                            urls.append(urljoin(base_url, href))
                    has_status = _contains_any(
                        text.lower(),
                        (*_PUBLISHED_MARKERS, *_PROCESSING_MARKERS, *_REJECTED_MARKERS),
                    )
                    normalized_urls = tuple(dict.fromkeys(urls))
                    if has_status:
                        status_candidates.append(
                            (depth, node, text, normalized_urls)
                        )
                    elif urls:
                        url_fallbacks.append((depth, node, text, normalized_urls))
                except Exception:
                    continue
            selected = None
            ranked = status_candidates or url_fallbacks
            if ranked:
                _depth, selected_node, selected_text, selected_urls = min(
                    ranked,
                    key=lambda item: item[0],
                )
                selected = {"text": selected_text, "urls": selected_urls}
                if (
                    read_rejection_details
                    and _contains_any(selected_text, _REJECTED_MARKERS)
                ):
                    rejection_reason = PlatformReviewVerifier._read_rejection_reason(
                        page,
                        selected_node,
                        title=title,
                        row_text=selected_text,
                    )
                    if rejection_reason:
                        selected["rejection_reason"] = rejection_reason
            if selected is None:
                selected = {
                    "text": PlatformReviewVerifier._safe_text(candidate, limit=500),
                    "urls": (),
                }
            rows.append(selected)
        return sorted(
            _dedupe_rows_by_stable_identity(rows, public_url_patterns),
            key=lambda item: len(item["text"]),
        )

    @staticmethod
    def _read_rejection_reason(
        page: Any,
        row: Any,
        *,
        title: str,
        row_text: str,
    ) -> str:
        """Read a rejection reason bound to one matched row without clicking.

        Sohu exposes the reason either as a DOM attribute or as a tooltip after
        hovering the question/help affordance.  Hover is intentionally the only
        interaction here: it neither submits nor changes publication state.
        """

        values: list[str] = []
        PlatformReviewVerifier._append_rejection_attributes(values, row)
        try:
            reason_nodes = row.locator(_SOHU_REJECTION_TEXT_SELECTOR)
            for index in range(min(reason_nodes.count(), 10)):
                reason_text = PlatformReviewVerifier._safe_text_content(
                    reason_nodes.nth(index),
                    limit=500,
                )
                if reason_text:
                    values.append(reason_text)
        except Exception:
            pass
        try:
            attribute_nodes = row.locator(_REJECTION_DETAIL_NODE_SELECTOR)
            for index in range(min(attribute_nodes.count(), 30)):
                PlatformReviewVerifier._append_rejection_attributes(
                    values,
                    attribute_nodes.nth(index),
                )
        except Exception:
            pass

        before_tooltips = set(
            PlatformReviewVerifier._visible_tooltip_texts(page)
        )
        hover_targets: list[Any] = []
        for marker in _REJECTED_MARKERS[:6]:
            try:
                status_nodes = row.get_by_text(marker, exact=True)
                for index in range(min(status_nodes.count(), 3)):
                    hover_targets.append(status_nodes.nth(index))
            except Exception:
                continue
        for selector in _REJECTION_HOVER_SELECTORS:
            try:
                nodes = row.locator(selector)
                for index in range(min(nodes.count(), 4)):
                    hover_targets.append(nodes.nth(index))
            except Exception:
                continue

        for target in hover_targets[:12]:
            try:
                if not target.is_visible():
                    continue
                target.hover(timeout=1_000)
                page.wait_for_timeout(120)
            except Exception:
                continue
            for tooltip_text in PlatformReviewVerifier._visible_tooltip_texts(page):
                if tooltip_text not in before_tooltips:
                    values.append(tooltip_text)

        for value in values:
            normalized = _normalize_rejection_detail(
                value,
                title=title,
                row_text=row_text,
            )
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _append_rejection_attributes(values: list[str], node: Any) -> None:
        for attribute in _REJECTION_DETAIL_ATTRIBUTES:
            try:
                value = str(node.get_attribute(attribute) or "").strip()
            except Exception:
                continue
            if value:
                values.append(value)

    @staticmethod
    def _visible_tooltip_texts(page: Any) -> tuple[str, ...]:
        values: list[str] = []
        try:
            tooltips = page.locator(_VISIBLE_TOOLTIP_SELECTOR)
            for index in range(min(tooltips.count(), 20)):
                tooltip = tooltips.nth(index)
                if not tooltip.is_visible():
                    continue
                text = PlatformReviewVerifier._safe_text(tooltip, limit=500)
                if text:
                    values.append(text)
        except Exception:
            pass
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _safe_text(locator: Any, *, limit: int) -> str:
        try:
            return " ".join(str(locator.inner_text() or "").split())[:limit]
        except Exception:
            return ""

    @staticmethod
    def _safe_text_content(locator: Any, *, limit: int) -> str:
        try:
            return " ".join(str(locator.text_content() or "").split())[:limit]
        except Exception:
            return ""


def create_platform_review_verifier(
    cookies_directory: str | Path,
) -> PlatformReviewVerifier:
    return PlatformReviewVerifier(cookies_directory)


def _session_expired_result(message: str) -> dict[str, Any]:
    return {
        "state": "risk",
        "message": message,
        "evidence": message,
    }


_EXTERNAL_ID_KEYS = (
    "submission_id",
    "work_id",
    "external_id",
    "remote_id",
    "article_id",
    "item_id",
    "note_id",
    "video_id",
)
_ATTEMPT_TIME_KEYS = (
    "started_at",
    "submitted_at",
    "submission_at",
    "attempt_started_at",
)
_OBSERVED_TIME_KEYS = (
    "observed_at",
    "checked_at",
    "last_checked_at",
)


def _dedupe_rows_by_stable_identity(
    rows: list[dict[str, Any]],
    public_url_patterns: tuple[re.Pattern[str], ...],
) -> list[dict[str, Any]]:
    """Collapse only content-equivalent DOM renderings of one durable identity.

    Text, status and minute-resolution timestamps are not identities.  Two
    distinct submissions can legitimately share all three, so anonymous rows
    must retain their multiplicity and force conservative reconciliation.
    Rows with the same work ID or public URL can still disagree while a page is
    updating (for example one responsive copy says ``published`` and another
    says ``processing``).  Such conflicting renderings must also be retained so
    the classifier resolves them conservatively instead of depending on DOM
    order.
    """

    deduped: list[dict[str, Any]] = []
    renderings_by_identity: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        explicit_ids = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in row.get("external_ids") or ()
                    if str(value).strip()
                }
            )
        )
        public_urls = tuple(
            sorted(
                {
                    _https_url(str(url))
                    for url in row.get("urls") or ()
                    if _public_url(str(url), public_url_patterns)
                }
            )
        )
        identity_parts = tuple(f"id:{value}" for value in explicit_ids) + tuple(
            f"url:{value}" for value in public_urls
        )
        if identity_parts:
            prior_renderings = renderings_by_identity.setdefault(identity_parts, [])
            if any(row == prior for prior in prior_renderings):
                continue
            prior_renderings.append(row)
        deduped.append(row)
    return deduped


def _resolve_exact_title_rows(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    platform_name: str,
    public_url_patterns: tuple[re.Pattern[str], ...],
    display_timezone: tzinfo | None,
) -> dict[str, Any]:
    """Resolve a title match only when it belongs to this publish attempt.

    A title is not an identity.  Reusing a title is common, so an old public or
    rejected work must never finish a newer job.  A provider work/submission ID
    is the strongest binding.  Without one, exactly one title candidate and a
    timestamp whose complete precision interval is inside the current attempt
    window are required.  Any ambiguity remains non-terminal.
    """

    external_ids = _snapshot_external_ids(snapshot)
    if external_ids:
        matched = [row for row in rows if _row_matches_any_id(row, external_ids)]
        if not matched:
            return _ambiguous_candidate_result(
                platform_name,
                "已保存本次提交/作品编号，但当前同标题条目均未匹配该编号",
                rows,
            )
        return _classify_bound_rows(
            matched,
            platform_name=platform_name,
            public_url_patterns=public_url_patterns,
            binding="稳定提交/作品编号",
        )

    if len(rows) != 1:
        return _ambiguous_candidate_result(
            platform_name,
            f"发现 {len(rows)} 个同标题候选，缺少稳定作品编号，无法归属到本次发布",
            rows,
        )

    attempt_start = _first_datetime(snapshot, _ATTEMPT_TIME_KEYS)
    if attempt_start is None:
        return _ambiguous_candidate_result(
            platform_name,
            "同标题候选缺少本次任务开始时间，无法排除旧条目",
            rows,
        )
    observed_at = _first_datetime(snapshot, _OBSERVED_TIME_KEYS) or datetime.now(
        timezone.utc
    )
    if observed_at < attempt_start:
        return _ambiguous_candidate_result(
            platform_name,
            "核验时间早于任务开始时间，无法可靠归属平台条目",
            rows,
        )

    intervals = _row_time_intervals(
        rows[0],
        observed_at=observed_at,
        display_timezone=display_timezone,
    )
    if len(intervals) != 1:
        return _ambiguous_candidate_result(
            platform_name,
            "唯一同标题候选没有唯一、可解析的完整日期时间，无法排除旧条目",
            rows,
        )
    interval_start, interval_end = intervals[0]
    if interval_start < attempt_start or interval_end > observed_at:
        return _ambiguous_candidate_result(
            platform_name,
            "同标题候选的显示时间不能可靠落在本次发布窗口内",
            rows,
        )
    return _classify_bound_rows(
        rows,
        platform_name=platform_name,
        public_url_patterns=public_url_patterns,
        binding="唯一标题与本次时间窗口",
    )


def _classify_bound_rows(
    rows: list[dict[str, Any]],
    *,
    platform_name: str,
    public_url_patterns: tuple[re.Pattern[str], ...],
    binding: str,
) -> dict[str, Any]:
    rejected_rows = [
        row for row in rows if _contains_any(str(row.get("text") or ""), _REJECTED_MARKERS)
    ]
    processing_rows = [
        row for row in rows if _contains_any(str(row.get("text") or ""), _PROCESSING_MARKERS)
    ]
    published_rows = [
        row for row in rows if _contains_positive_published(str(row.get("text") or ""))
    ]
    classified_count = len(
        {
            id(row)
            for row in (*rejected_rows, *processing_rows, *published_rows)
        }
    )
    terminal_kinds = int(bool(rejected_rows)) + int(bool(published_rows))
    if (
        processing_rows
        or terminal_kinds > 1
        or classified_count != len(rows)
        or (len(rows) > 1 and (rejected_rows or published_rows))
    ):
        return _ambiguous_candidate_result(
            platform_name,
            f"已按{binding}绑定，但候选状态重复、冲突或仍在审核",
            rows,
        )

    if rejected_rows:
        reason = str(rejected_rows[0].get("rejection_reason") or "").strip()
        message = f"{platform_name}已按{binding}核验本次条目未通过"
        if reason:
            message = f"{message}：{reason}"
        evidence = str(rejected_rows[0].get("text") or "").strip()
        if reason and reason not in evidence:
            evidence = f"{evidence} | 未通过原因：{reason}"
        return {
            "state": "rejected",
            "message": message[:500],
            "evidence": evidence[:500],
        }

    if published_rows:
        public_urls = {
            _https_url(url)
            for row in published_rows
            for url in row.get("urls") or ()
            if _public_url(str(url), public_url_patterns)
        }
        if len(public_urls) == 1:
            return {
                "state": "published",
                "result_url": next(iter(public_urls)),
                "message": f"{platform_name}已按{binding}核验本次条目已发布",
                "evidence": str(published_rows[0].get("text") or "")[:500],
            }
        return _ambiguous_candidate_result(
            platform_name,
            "条目显示已发布，但没有唯一可核验的公开作品链接",
            rows,
        )

    return {
        "state": "processing",
        "message": f"{platform_name}已绑定本次条目，但平台状态尚不明确",
        "evidence": str(rows[0].get("text") or "")[:500],
    }


def _ambiguous_candidate_result(
    platform_name: str,
    reason: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = " | ".join(
        str(row.get("text") or "")[:180] for row in rows[:3]
    )[:500]
    return {
        "state": "processing",
        "message": f"{platform_name}{reason}；保持监测，不会自动重发，请必要时人工核验",
        "evidence": evidence,
    }


def _snapshot_external_ids(snapshot: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in _EXTERNAL_ID_KEYS:
        raw = snapshot.get(key)
        if isinstance(raw, (str, int)):
            normalized = str(raw).strip()
            if normalized:
                values.append(normalized)
    for container_key in ("submission", "publish_evidence", "provider_evidence"):
        container = snapshot.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in _EXTERNAL_ID_KEYS:
            normalized = str(container.get(key) or "").strip()
            if normalized:
                values.append(normalized)
    for key in ("result_url", "submission_url", "work_url"):
        values.extend(_url_identifiers(str(snapshot.get(key) or "")))
    return tuple(dict.fromkeys(values))


def _row_matches_any_id(row: dict[str, Any], external_ids: tuple[str, ...]) -> bool:
    row_ids = {
        str(value).strip()
        for value in row.get("external_ids") or ()
        if str(value).strip()
    }
    urls = tuple(str(value or "").strip() for value in row.get("urls") or ())
    for url in urls:
        row_ids.update(_url_identifiers(url))
    text = str(row.get("text") or "")
    for external_id in external_ids:
        if external_id in row_ids:
            return True
        if any(_identifier_occurs(url, external_id) for url in urls):
            return True
        if _identifier_occurs(text, external_id):
            return True
    return False


def _identifier_occurs(text: str, identifier: str) -> bool:
    normalized = str(identifier or "").strip()
    if not normalized:
        return False
    return bool(
        re.search(
            rf"(?<![0-9A-Za-z_-]){re.escape(normalized)}(?![0-9A-Za-z_-])",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _url_identifiers(value: str) -> list[str]:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return []
    candidates = re.findall(
        r"(?:^|&)(?:id|item_id|article_id|note_id|video_id)=([^&#]+)",
        parsed.query,
    )
    candidates.extend(
        part
        for part in parsed.path.split("/")
        if re.fullmatch(r"(?:\d{6,}|[A-Fa-f0-9]{16,}|BV[0-9A-Za-z]{10}|[0-9A-Za-z_-]{12,})", part)
    )
    return list(dict.fromkeys(candidates))


def _first_datetime(snapshot: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        # publish_jobs timestamps originate from SQLite CURRENT_TIMESTAMP and
        # are therefore UTC even though the stored string has no offset.
        parsed = _parse_datetime(snapshot.get(key), naive_timezone=timezone.utc)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(
    value: Any,
    *,
    naive_timezone: tzinfo | None,
) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1_000
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        if naive_timezone is None:
            return None
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(timezone.utc)


def _timestamp_value_interval(value: Any) -> tuple[datetime, datetime] | None:
    # Provider epoch/ISO timestamps are instants.  If a provider returns a
    # naive ISO timestamp here, treating it as UTC is the conservative API
    # contract; management-page display strings follow their policy below.
    parsed = _parse_datetime(value, naive_timezone=timezone.utc)
    if parsed is None:
        return None
    return parsed, parsed + timedelta(seconds=1)


def _row_time_intervals(
    row: dict[str, Any],
    *,
    observed_at: datetime,
    display_timezone: tzinfo | None,
) -> list[tuple[datetime, datetime]]:
    supplied = []
    for raw in row.get("time_intervals") or ():
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            continue
        start = _parse_datetime(raw[0], naive_timezone=display_timezone)
        end = _parse_datetime(raw[1], naive_timezone=display_timezone)
        if start is not None and end is not None and start < end:
            supplied.append((start, end))
    if supplied:
        return list(dict.fromkeys(supplied))

    text = str(row.get("text") or "")
    intervals: list[tuple[datetime, datetime]] = []
    occupied: list[tuple[int, int]] = []
    full_pattern = re.compile(
        r"(?<!\d)(?P<year>20\d{2})[年./-](?P<month>0?[1-9]|1[0-2])[月./-]"
        r"(?P<day>0?[1-9]|[12]\d|3[01])日?\s+"
        r"(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)"
        r"(?::(?P<second>[0-5]\d))?(?!\d)"
    )
    for match in full_pattern.finditer(text):
        try:
            start = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour")),
                int(match.group("minute")),
                int(match.group("second") or 0),
            )
        except ValueError:
            continue
        if display_timezone is None:
            continue
        precision = timedelta(seconds=1 if match.group("second") else 60)
        aware_start = start.replace(tzinfo=display_timezone).astimezone(timezone.utc)
        intervals.append((aware_start, aware_start + precision))
        occupied.append(match.span())

    date_pattern = re.compile(
        r"(?<!\d)(?P<year>20\d{2})[年./-](?P<month>0?[1-9]|1[0-2])[月./-]"
        r"(?P<day>0?[1-9]|[12]\d|3[01])日?(?!\d)"
    )
    for match in date_pattern.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        try:
            start = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        if display_timezone is None:
            continue
        aware_start = start.replace(tzinfo=display_timezone).astimezone(timezone.utc)
        intervals.append((aware_start, aware_start + timedelta(days=1)))
    return list(dict.fromkeys(intervals))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(_contains_marker(normalized, marker) for marker in markers)


def _row_has_observable_status(row: dict[str, Any]) -> bool:
    return _contains_any(
        str(row.get("text") or ""),
        (*_PUBLISHED_MARKERS, *_PROCESSING_MARKERS, *_REJECTED_MARKERS),
    )


def _normalize_rejection_detail(
    value: Any,
    *,
    title: str,
    row_text: str,
) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized or normalized in {title, row_text}:
        return ""
    normalized = re.sub(
        r"^(?:未通过|审核未通过|审核不通过|审核失败|已拒绝|发布失败)"
        r"\s*(?:原因|详情)?\s*[:：\-—]?\s*",
        "",
        normalized,
        count=1,
    ).strip()
    if not normalized or normalized in _GENERIC_REJECTION_LABELS:
        return ""
    if normalized == title or normalized == row_text:
        return ""
    if title and title in normalized:
        return ""
    if len(normalized) < 4 and not any(
        marker in normalized for marker in ("广告", "侵权", "违规", "低俗", "重复")
    ):
        return ""
    return normalized[:500]


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = str(marker or "").lower()
    if not normalized_marker:
        return False
    # Latin status words must match as terms.  Plain substring matching turns
    # "unpublished" into "published" and "delivery" into "live".
    if normalized_marker.isascii():
        return bool(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(normalized_marker)}(?![a-z0-9_])",
                text,
                re.IGNORECASE,
            )
        )
    return normalized_marker in text


def _contains_positive_published(text: str) -> bool:
    normalized = str(text or "").lower()
    return bool(
        _contains_any(normalized, _PUBLISHED_MARKERS)
        and not _contains_any(normalized, _NEGATED_PUBLISHED_MARKERS)
    )


def _public_url(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized and any(pattern.fullmatch(normalized) for pattern in patterns))


def _https_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    scheme = "https" if parsed.scheme.lower() in {"http", "https"} else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


__all__ = [
    "PlatformReviewVerifier",
    "ReviewPagePolicy",
    "create_platform_review_verifier",
]
