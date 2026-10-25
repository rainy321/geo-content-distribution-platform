"""Read-only collection summaries for feedback, separate from effect metrics."""
from __future__ import annotations

import re

from services.doubao_task_recovery import safe_reason
from services.geo_test_reports import project_report_evaluation


_STATUSES = frozenset(('completed', 'completed_with_gaps', 'success', 'failed', 'stopped',
                       'pending', 'running', 'need_action', 'needs_review', 'partial',
                       'service_unavailable', 'deferred_answer', 'private_account_context'))
_STAGES = frozenset(('browser_start', 'navigation', 'new_conversation', 'model_selection',
                     'question_evidence', 'editor_readback', 'submission_checkpoint',
                     'submission_control', 'submit_question', 'wait_for_answer', 'extract_sources'))


def _choice(value, choices, default=None):
    return value if isinstance(value, str) and value in choices else default


def collection_summary(report):
    """Use saved report evidence only; never infer submission from missing fields."""
    report = project_report_evaluation(report)
    counts = dict(planned=len(report['cases']), evaluable=0, failed=0, pending=0,
                  excluded_private=0, incomplete=0)
    issues = []
    for case in report['cases']:
        assessment = case['assessment']
        if assessment.get('answer_evaluable') is True:
            counts['evaluable'] += 1
            continue
        status = _choice(case.get('status'), _STATUSES, 'unknown')
        reason = safe_reason(case.get('failure_reason')) or safe_reason(assessment.get('answer_error'))
        if assessment.get('answer_error') == 'private_account_context':
            counts['excluded_private'] += 1
        elif status in ('failed', 'service_unavailable'):
            counts['failed'] += 1
        elif status == 'pending':
            counts['pending'] += 1
        else:
            counts['incomplete'] += 1
        identifier = case.get('id')
        issues.append({'case_id': identifier if isinstance(identifier, str)
                       and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier) else None,
                       'status': status, 'failure_reason': reason,
                       'failure_stage': _choice(case.get('failure_stage'), _STAGES),
                       'question_submission_attempted': case.get('question_submission_attempted')
                       if type(case.get('question_submission_attempted')) is bool else None,
                       'question_submitted': case.get('question_submitted')
                       if type(case.get('question_submitted')) is bool else None})
    return {'schema_version': 1, 'status': _choice(report.get('status'), _STATUSES, 'unknown'),
            'stop_reason': safe_reason(report.get('stop_reason')),
            'has_evaluable_answers': counts['evaluable'] > 0,
            'counts': counts, 'issues': issues}
