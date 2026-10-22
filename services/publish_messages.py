"""Read-only presentation of legacy technical publish errors.

Original job messages remain in the local ledger. This projection does not
change status, authorize a retry, release quotas, or infer platform success.
"""
from __future__ import annotations


def public_publish_message(message: str, platform: str) -> str:
    text = str(message or '').strip()
    if platform != 'zhihu':
        return text
    technical = any(marker in text.lower() for marker in (
        'browsertype.', 'browsercontext.', 'playwright.', 'traceback (most recent call last)',
        'page.goto:', 'page.fill:', 'page.click:', 'locator.', 'timeouterror',
        'target page, context or browser has been closed', 'ms exceeded',
        "executable doesn't exist", 'chromium_headless_shell', 'playwright install',
        'call log:', '\\users\\', '/users/', 'authorization:', 'cookie:',
    ))
    if not technical:
        return text
    if text.startswith(('知乎公开文章幂等检查失败', '知乎公开文章幂等检查超时')):
        if any(marker in text.lower() for marker in ("executable doesn't exist", 'chromium_headless_shell', 'playwright install')):
            return '知乎发文前检查未完成：检查浏览器组件不可用，请更新软件后重试。本次检查未通过，尚未进入填稿或发布。'
        return '知乎发文前检查未完成，本次尚未进入填稿或发布。请检查网络和登录状态后重试。'
    if text.startswith('知乎登录状态检查失败'):
        return '知乎登录检查未完成，本次尚未进入填稿或发布。请检查浏览器和登录状态后重试。'
    return '知乎操作未完成，平台最终状态尚未确认。请先核对原文章与平台提示，避免重复发布；详细诊断保留在本机日志。'
