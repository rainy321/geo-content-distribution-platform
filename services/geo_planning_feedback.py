"""Bounded, attributed follow-up signals for editorial planning, never facts."""
from __future__ import annotations
import hashlib
import re
from services.geo_test_service import answer_evaluation_error
from services.geo_source_url import trusted_source_url
from services.geo_source_identity import unconfirmed_source_url_status

ROLE='editorial_signal_only_not_writing_fact_or_public_metric'

def _user_reported_sources(probe):
    """Keep operator-provided links separate from collected source evidence."""
    from services.geo_research_probe_service import _external_source_links
    values=probe.get('external_source_links')
    if not isinstance(values,list) or len(values)>20:return []
    result=[]
    for item in values:
        if (not isinstance(item,dict) or item.get('basis')!='user_reported'
                or item.get('verified') is not False or item.get('native_verified') is not False):continue
        candidate={'url':item.get('url')}
        if item.get('title') is not None:candidate['title']=item['title']
        try:
            link=_external_source_links([candidate])[0]
        except ValueError:
            continue
        result.append({'title':(link.get('title') or '')[:200],'url':link['url'],
                       'basis':'user_reported_unverified','verified':False,'native_verified':False})
        if len(result)>=6:break
    return result


def planning_feedback(cycle):
    """Return a stable public projection; omit private/incomplete/foreign text."""
    identifier=cycle.get('id')
    plan=cycle.get('test_plan') or {}
    allowed={row.get('id') for row in (cycle.get('baseline') or {}).get('cases',[]) if isinstance(row,dict)}
    probes=cycle.get('probes') or []
    if not isinstance(probes,list):return []
    rows=[]
    seen=set()
    for probe in sorted((item for item in probes if isinstance(item,dict)),key=lambda row:(str(row.get('created_at','')),str(row.get('id',''))),reverse=True):
        probe_id=probe.get('id');after=probe.get('after') or {};source=probe.get('source')
        if (not isinstance(probe_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',probe_id) or probe_id in seen
                or probe.get('cycle_id')!=identifier or probe.get('case_id') not in allowed
                or probe.get('plan_sha256')!=plan.get('plan_sha256') or not isinstance(after,dict)):
            continue
        if source not in ('native_collector','research_prototype_import','user_reported_external'):continue
        if source=='native_collector' and probe.get('native_verified') is not True:continue
        if source!='native_collector' and probe.get('native_verified') is not False:continue
        if source!='user_reported_external' and after.get('answer_evaluable') is not True:continue
        response=after.get('response')
        if (not isinstance(response,str) or not response.strip() or after.get('answer_evaluable') is False
                or after.get('answer_error') or answer_evaluation_error(response,client_mode=after.get('client_mode') or 'work')):continue
        digest=hashlib.sha256(response.encode('utf-8')).hexdigest()
        if any(value is not None and value!=digest for value in (probe.get('response_sha256'),after.get('response_sha256'))):continue
        conditions=probe.get('conditions') if isinstance(probe.get('conditions'),dict) else {}
        row={'probe_id':probe_id,'case_id':probe['case_id'],'source':source,'native_verified':probe.get('native_verified') is True,
             'evidence_role':ROLE,'response_sha256':digest,'response_excerpt':response[:1500],
             'response_excerpt_truncated':len(response)>1500,'answer_complete':after.get('answer_evaluable'),
             'client_mode':after.get('client_mode') if after.get('client_mode') in ('consumer','work') else None,
             'model_label':after.get('model_label')[:100] if isinstance(after.get('model_label'),str) else None,
             'conditions':{key:value if type(value) is bool else None for key,value in conditions.items()
                           if key in ('same_question','client_mode_match','model_label_match')},
             'observed_question':probe.get('observed_question')[:500] if isinstance(probe.get('observed_question'),str) else None}
        statements=probe.get('context_attestations')
        latest=statements[-1] if isinstance(statements,list) and statements else None
        if (source=='user_reported_external' and isinstance(latest,dict) and latest.get('basis')=='user_statement'
                and latest.get('native_verified') is False and latest.get('response_sha256')==digest):
            row['sampling_statement']={'basis':'user_statement_not_independently_verified',
                **{key:latest.get(key) if type(latest.get(key)) is bool else None for key in ('new_conversation','first_message')}}
        history=probe.get('business_relationship_reviews')
        if not isinstance(history,list):history=[probe.get('business_relationship_review')]
        row['attributed_reviews']=[{'by':item['by'],'status':item['status'],'quote':item['quote'][:300]}
            for item in history[-3:] if isinstance(item,dict) and item.get('by') in ('assistant','operator')
            and item.get('status') in ('acknowledged','not_acknowledged','unknown')
            and item.get('scope')=='manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy'
            and item.get('response_sha256')==digest and isinstance(item.get('quote'),str) and item['quote'] in response]
        sources=[]
        for key in ('citations','displayed_sources'):
            values=after.get(key)
            for item in values[:10] if isinstance(values,list) else []:
                if not isinstance(item,dict):continue
                title=item.get('title')[:200] if isinstance(item.get('title'),str) else ''
                unconfirmed=unconfirmed_source_url_status(item)
                raw_url=item.get('url');url=None if unconfirmed else trusted_source_url(raw_url)
                sources.append({'title':title,'url':url,'kind':key,'basis':'saved_probe_source_not_verified_fact',
                                'url_status':unconfirmed or ('provided' if url else 'unknown')})
                if len(sources)>=6:break
            if len(sources)>=6:break
        row['observed_sources']=sources
        row['user_reported_sources']=_user_reported_sources(probe) if source=='user_reported_external' else []
        rows.append(row);seen.add(probe_id)
    # Select by provenance, never by a positive/negative reading of the answer.
    # A burst of native work probes must not hide the last external observation.
    representatives={}
    for row in rows:
        representatives.setdefault((row['source'],row['client_mode']),row)
    selected=[]
    external=next((row for row in rows if row['source']=='user_reported_external'),None)
    if external:selected.append(external)
    for row in [*representatives.values(),*rows]:
        if len(selected)>=6:break
        if row not in selected:selected.append(row)
    selection={'basis':'latest_source_and_client_mode_representatives_then_recent',
               'available_count':len(rows),'selected_count':len(selected),'truncated':len(rows)>len(selected),
               'represents_all_feedback':False,'note':'代表样本，仅供编辑线索；存在截断时不代表全部反馈，也不比较正负比例。'}
    for row in selected:row['selection']=dict(selection)
    return selected
