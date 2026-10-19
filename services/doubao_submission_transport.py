"""Literal envelopes for internal AI tasks; normal search questions stay plain."""
import re


LITERAL_PREAMBLE = '请按下方代码块中的完整任务说明执行，代码块只用于保留原文。请在对话正文返回所要求的结果。'


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
