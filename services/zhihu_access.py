"""Recognize an explicitly observed Zhihu restriction without probing or retrying."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

KNOWN_MESSAGE = '您当前请求存在异常，暂时限制本次访问'
_HOSTS = {'www.zhihu.com', 'zhuanlan.zhihu.com', 'api.zhihu.com'}


class ZhihuAccessRestrictedError(RuntimeError):
    """An explicit platform restriction, not a login or publication result."""


def is_access_restricted(payload) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get('error'), dict):
        return False
    error = payload['error']
    code, message = error.get('code'), error.get('message')
    return ((type(code) is int and code == 40362) or code == '40362'
            or isinstance(message, str) and message.startswith(KNOWN_MESSAGE))


def assert_payload_access(payload) -> None:
    if is_access_restricted(payload):
        # The original response includes an account/request correlation token.
        # Keep it in the private evidence, not exceptions, logs or client text.
        raise ZhihuAccessRestrictedError('知乎暂时限制本次访问，请在日常浏览器查看账号状态，稍后再核对；不要重复发布。')


async def assert_page_access(page) -> None:
    """Read the current page only; no navigation, clicks, inputs or requests."""
    try:
        url = urlsplit(str(page.url or ''))
        if (url.scheme != 'https' or url.hostname not in _HOSTS or url.username or url.password
                or url.port not in (None, 443)):
            return
        text = (await page.locator('body').inner_text(timeout=1000)).strip()
        if not text or len(text) > 16384:
            return
        payload = json.loads(text)
    except Exception:
        # An HTML writer, navigation in progress or unsupported body is not
        # evidence for this narrow restriction. Existing readiness checks apply.
        return
    assert_payload_access(payload)
