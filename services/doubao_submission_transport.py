"""Literal envelopes for internal AI tasks; normal search questions stay plain."""
import re


LITERAL_PREAMBLE = '请按下方代码块中的完整任务说明执行，代码块只用于保留原文。请在对话正文返回所要求的结果。'


# Match the observed download promotions by their identifying text and close control.
KNOWN_DOWNLOAD_PROMOTION_JS = r'''selectors => {
    const visible = e => {
        if (![...e.getClientRects()].some(r => r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight)) return false;
        for (let node = e; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
        }
        return true;
    };
    const text = e => (e.innerText || '').replace(/\s+/g, '');
    const blocked = [...document.querySelectorAll(selectors.challenge + ',' + selectors.login_dialog)].some(visible);
    const isDownloadPromotion = e => {
        const content = text(e);
        if (/每日签到[，,]?免费用豆包工作/.test(content) && content.includes('立即下载电脑版')) return true;
        if (!e.matches('[role="dialog"]') || !content.includes('下载电脑版使用完整功能')) return false;
        const downloads = [...e.querySelectorAll('button[data-testid="desktop_download_guide_popup_btn"]')];
        const reminders = [...e.querySelectorAll('button')].filter(node => text(node) === '下次提醒我');
        const close = [...e.querySelectorAll('button[aria-label="关闭"]')];
        return downloads.length === 1 && visible(downloads[0]) && text(downloads[0]) === '下载电脑版'
            && reminders.length === 1 && visible(reminders[0]) && close.length === 1 && visible(close[0]);
    };
    const candidates = [...document.querySelectorAll('[role="dialog"],[aria-modal="true"],[class*="modal"],[class*="Modal"]')]
        .filter(visible).filter(isDownloadPromotion);
    const roots = candidates.filter(e => !e.querySelector('input,textarea,iframe,[contenteditable="true"]')
        && text(e).length < 1500 && !/验证码|安全验证|人机验证|登录|登入|扫码|授权|绑定|实名|确认/.test(text(e)));
    const controls = [];
    for (const root of roots) {
        for (const node of root.querySelectorAll('button,[role="button"],[aria-label],[title],[data-testid*="close"],[class*="close"],[class*="Close"]')) {
            const name = (node.getAttribute('aria-label') || node.getAttribute('title') || text(node)).trim();
            const named = /^(?:关闭|关闭弹窗|关闭窗口|close|dismiss|×|✕|✖|x)$/i.test(name);
            const marked = text(node).length < 20 && /close/i.test((node.getAttribute('data-testid') || '') + ' ' + (node.getAttribute('class') || ''));
            if (named || marked) controls.push(node.closest('button,[role="button"]') || node);
        }
    }
    const unique = [...new Set(controls)].filter(node => !controls.some(other => other !== node && other.contains(node)));
    const shown = unique.filter(visible);
    const ready = !blocked && roots.length > 0 && unique.length === 1 && shown.length === 1
        && shown[0].disabled !== true && shown[0].getAttribute('aria-disabled') !== 'true';
    return {recognized: roots.length > 0, auth_blocked: blocked, candidate_count: candidates.length,
        close_count: unique.length, visible_close_count: shown.length, can_close: ready,
        close_element: ready ? shown[0] : null};
}'''


EDITOR_READBACK_JS = r'''(e, expected) => {
    const evidence = {editor_dom:e.outerHTML, inner_text:e.innerText ?? null,
        text_content:e.textContent ?? null, readback:null, method:'unsupported_editor_structure'};
    if (e.matches('textarea,input') && typeof e.value === 'string') {
        return {...evidence, readback:e.value, method:'form_value'};
    }
    if (!e.isContentEditable) return evidence;
    if (e.innerText === expected) {
        return {...evidence, readback:e.innerText, method:'contenteditable_inner_text_exact'};
    }
    // Observed Doubao Tiptap structure: each direct, undecorated paragraph
    // represents one input line. Do not collapse arbitrary rendered whitespace
    // or flatten links, nested blocks, marks, hidden content or unknown layouts.
    if (!e.matches('div.tiptap.ProseMirror[contenteditable="true"]')) return evidence;
    const lines = [...e.childNodes];
    if (!lines.length || lines.some(p => p.nodeType !== Node.ELEMENT_NODE || p.tagName !== 'P'
            || p.attributes.length !== 0)) return evidence;
    const plain = p => [...p.childNodes].every(n => n.nodeType === Node.TEXT_NODE)
        || (p.childNodes.length === 1 && p.firstChild.nodeType === Node.ELEMENT_NODE
            && p.firstChild.tagName === 'BR' && !p.firstChild.children.length
            && [...p.firstChild.attributes].every(a => a.name === 'class'));
    if (lines.some(p => !plain(p) || /[\r\n]/.test(p.textContent))) return evidence;
    return {...evidence, readback:lines.map(p => p.textContent).join('\n'),
        method:'prosemirror_direct_plain_paragraphs'};
}'''


def submission_fence(question: str) -> str:
    longest = max((len(item) for item in re.findall(r'`+', question)), default=0)
    return '`' * max(3, longest + 1)


def submission_text(question: str, format: str = 'plain') -> str:
    if format == 'plain':
        return question
    if format != 'verbatim_fenced':
        raise ValueError('submission_format 必须是 plain 或 verbatim_fenced')
    # Content may itself contain Markdown fences from an observed answer.
    # A strictly longer outer fence cannot be closed by any input backticks.
    fence = submission_fence(question)
    # A fenced block can interrupt a paragraph. Avoid an extra blank line:
    # Chromium contenteditable represents empty lines with a <div><br></div>
    # whose innerText may add another newline and fail exact readback.
    return f'{LITERAL_PREAMBLE}\n{fence}text\n{question}\n{fence}'
