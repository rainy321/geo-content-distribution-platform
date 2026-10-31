"""Conservative recognition of explicit pending research notices, not final answers."""
from __future__ import annotations

import re


def is_deferred_answer(response: str) -> bool:
    """Reject a first-person delegation notice that promises results later.

    This is deliberately narrow: discussing background jobs or explaining a
    completed investigation is not evidence that the current answer is pending.
    """
    if not isinstance(response, str):
        return False
    text = re.sub(r"\s+", "", response).strip()
    if not text or len(text) > 2000:
        return False
    # A final response may recount the earlier delegation before its results.
    if re.search(r"(?:调研|研究|核验)(?:任务)?(?:已经|已)(?:完成|结束)|(?:以下|下面|现将|现提供).{0,15}(?:最终结果|调研结果|核验结果|研究结果)", text):
        return False
    delegated = re.search(
        r"(?:^|[。！？；])(?:我(?:已经|已|先)?|已经|已).{0,60}(?:委托|委派|交给|交由).{0,70}(?:代理|组织者|后台任务)",
        text,
    )
    # Observed work-client notice: the task was started and its Feishu
    # document will be delivered later. Mentioning Feishu alone is insufficient.
    started_research = re.search(
        r'(?:^|[。！？；])(?:我(?:已经|已)?|已经|已)(?:为(?:你|您))?启动(?:了)?(?:这个|本次)?(?:调研|研究)任务',
        text,
    )
    future_document = re.search(
        r'(?:调研|研究)(?:任务)?(?:完成|结束)后.{0,35}(?:飞书文档).{0,20}(?:交付|给您|给你|发送|提供)',
        text,
    )
    pending = re.search(
        r"(?:调研|研究|任务).{0,12}(?:正在|在)后台(?:进行|执行|运行)|(?:完成|结束)后.{0,35}(?:我会|给你|整理|汇总)|稍后.{0,25}(?:给你|整理|汇总)",
        text,
    )
    # Work mode may list its planned research scope before promising a later
    # result. That scope is not the result, even when a copy button is visible.
    # Keep the first-person delegation requirement: a generic description of
    # research delivery must not make an otherwise final answer pending.
    future_results = re.search(
        r'(?:调研|研究|核验)(?:结果|结论|报告)(?:出来|产出|返回|生成)(?:之|以)?后'
        r'.{0,20}(?:我会|我将).{0,35}(?:整理|汇总|交付|提供|发送|发给)',
        text,
    )
    return bool(delegated and (pending or future_results) or started_research and future_document)
