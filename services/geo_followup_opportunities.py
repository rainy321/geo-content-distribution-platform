"""Read-only follow-up source channels, separate from baseline source IDs.

Only the current public planning-feedback projection is eligible. These URLs
are research leads: they establish neither customer facts, permission to submit
to a platform, nor a measured GEO result. No I/O is performed here.
"""
from __future__ import annotations

import hashlib
import json
import re

from services.geo_destination_service import opportunities
from services.geo_planning_feedback import planning_feedback


ROLE = 'followup_channel_research_only'
NOTE = '仅为后续复测的渠道线索；入口、投稿权限、客户事实和 GEO 效果均未确认。'


def _rows(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _unique(rows, key):
    grouped = {}
    for row in rows:
        identifier = row.get(key)
        if isinstance(identifier, str):
            grouped.setdefault(identifier, []).append(row)
    return {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}


def _eligible(cycle):
    """Add exact visible-question binding without relaxing planning's gates."""
    plan = _dict(cycle.get('test_plan'))
    if not plan or (cycle.get('baseline_source') != 'user_reported_external'
                    and not isinstance(cycle.get('baseline'), dict)):
        return
    originals = _unique(_rows(plan.get('cases')), 'id')
    anchors = originals if cycle.get('baseline_source') == 'user_reported_external' else _unique(
        _rows((cycle.get('baseline') or {}).get('cases')), 'id')
    probes = _unique(_rows(cycle.get('probes')), 'id')
    for feedback in planning_feedback(cycle):
        case = originals.get(feedback['case_id'])
        anchor = anchors.get(feedback['case_id'])
        probe = probes.get(feedback['probe_id'])
        if not case or not anchor or not probe:
            continue
        question = case.get('question')
        if (not isinstance(question, str) or not question.strip() or len(question) > 800
                or case.get('category') not in ('discovery', 'brand_identity', 'website_guided')
                or anchor.get('question') != question or anchor.get('category') != case.get('category')
                or feedback.get('conditions', {}).get('same_question') is False):
            continue
        # Read the full saved question, since the planning excerpt is bounded.
        if any(probe.get(field) is not None and probe[field] != question
               for field in ('question', 'observed_question')):
            continue
        if probe.get('category') is not None and probe['category'] != case.get('category'):
            continue
        yield feedback


def followup_projection(cycle):
    """Re-read trusted feedback, never a cached cycle.followup_opportunities."""
    cycle = _dict(cycle)
    feedback = list(_eligible(cycle))
    sources, by_ref = [], {}
    for item in feedback:
        for source in item['observed_sources']:
            identity = {'probe_id': item['probe_id'], 'case_id': item['case_id'],
                        'response_sha256': item['response_sha256'], **source}
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':')).encode('utf-8')).hexdigest()
            reference = 'fs_' + digest
            if reference in by_ref:
                continue
            bound = {'probe_id': item['probe_id'], 'case_id': item['case_id'], 'source_ref': reference,
                     **source, 'source': item['source'], 'native_verified': item['native_verified']}
            by_ref[reference] = bound
            # The destination normalizer needs internal IDs. They are removed
            # before returning, never added to baseline observations/source_ids.
            sources.append({'id': reference, 'case_id': item['case_id'],
                            'title': source['title'], 'url': source['url']})
    observations = {'cases': [{'id': case, 'usable': True} for case in dict.fromkeys(
        item['case_id'] for item in feedback)], 'sources': sources}
    profile = _dict(cycle.get('profile'))
    routes = opportunities(observations, cycle.get('destinations') or [], profile.get('website', ''))
    for route in routes:
        bound = [by_ref[reference] for reference in route['source_ids']]
        route.update(id='feedback:' + route['id'], source_ids=[],
                     feedback_source_refs=[{key: source[key] for key in ('probe_id', 'case_id', 'source_ref')}
                                           for source in bound],
                     feedback_sources=bound, evidence_role=ROLE,
                     facts_verified=False, publication_authorized=False, geo_effect_verified=False)
        # Title-only leads retain their separate follow-up references too.
        for lead in route['title_leads']:
            lead['feedback_source_refs'] = [{key: by_ref[reference][key]
                                            for key in ('probe_id', 'case_id', 'source_ref')}
                                           for reference in lead['source_ids']]
            lead['source_ids'] = []
        route['note'] += ' ' + NOTE
    return {'opportunities': routes, 'probe_cases': {item['probe_id']: item['case_id'] for item in feedback}}


def followup_opportunities(cycle):
    return followup_projection(cycle)['opportunities']


def source_routes_for_brief(projection, brief):
    """Bind only explicitly referenced valid probes belonging to this brief."""
    identifiers, cases = brief.get('feedback_probe_ids'), brief.get('case_ids')
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 6
            or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value)
                   for value in identifiers) or len(set(identifiers)) != len(identifiers)
            or not isinstance(cases, list) or any(not isinstance(case, str) for case in cases)
            or any(projection['probe_cases'].get(value) not in cases for value in identifiers)):
        return []
    selected = set(identifiers)
    result = []
    for route in projection['opportunities']:
        references = [dict(item) for item in route['feedback_source_refs']
                      if item['probe_id'] in selected and item['case_id'] in cases]
        if not references:
            continue
        label = ((route.get('candidate') or {}).get('name') or route.get('domain') or '来源网址待核对')
        result.append({'id': route['id'], 'label': label, 'surface': route['surface'], 'source_ids': [],
                       'feedback_source_refs': references, 'evidence_role': ROLE,
                       'facts_verified': False, 'publication_authorized': False, 'geo_effect_verified': False})
    return result
