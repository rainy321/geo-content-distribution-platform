"""Manual progress for official search portals; no login, file upload or network."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from services import webmaster_service as webmaster

PROVIDERS=(
    {'id':'baidu','name':'百度搜索资源平台','entry_url':'https://ziyuan.baidu.com/site/index',
     'submission_url':'https://ziyuan.baidu.com/linksubmit/index',
     'help_url':'https://ziyuan.baidu.com/college/articleinfo?id=1003',
     'availability_note':'官方站点管理和普通收录入口可访问；登录权限、配额与实际结果以平台当前显示为准。'},
    {'id':'toutiao','name':'头条搜索站长平台','entry_url':'https://zhanzhang.toutiao.com/',
     'submission_url':'https://zhanzhang.toutiao.com/',
     'help_url':'https://zhanzhang.toutiao.com/page/outer/docs',
     'availability_note':'官方入口及公开客户端仍可读取；本软件未验证登录后的账号权限或提交服务。'},
)
IDS={item['id'] for item in PROVIDERS}
STATUSES={'unknown','not_submitted','pending','processed','failed'}
LIMITATIONS=['进度来自用户对官方平台的声明，软件未独立核验平台结果。',
    '网站验证、提交、抓取和索引分别记录；不能据此判断豆包采用或GEO效果。',
    '使用自己的日常浏览器完成登录与提交；验证材料由网站维护者处理。']

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def _progress(value):
    if (not isinstance(value,dict) or set(value)!={'site_added','ownership_confirmed','sitemap_status','note'}
            or any(value[key] is not None and type(value[key]) is not bool for key in ('site_added','ownership_confirmed'))
            or not isinstance(value['sitemap_status'],str) or value['sitemap_status'] not in STATUSES
            or not isinstance(value['note'],str) or len(value['note'])>500):
        raise ValueError('只接受明确选择或未知的站点进度及500字内备注，不接受文件、token或登录信息')
    try:value['note'].encode('utf-8')
    except UnicodeError:raise ValueError('备注包含无效字符') from None
    if value['site_added'] is False and value['ownership_confirmed'] is True:
        raise ValueError('尚未添加官网与网站验证已通过互相矛盾，请核对平台当前显示')
    return copy.deepcopy(value)

def _scope(cycle,origin):
    return {'cycle_id':cycle['id'],'project_id':cycle['project_id'],'plan_sha256':cycle['test_plan']['plan_sha256'],
        'site_origin':origin,'profile':{'name':cycle['profile'].get('name'),'website':cycle['profile'].get('website'),
            'entity_type':cycle['profile'].get('entity_type') or 'product','official_urls':cycle['profile'].get('official_urls') or []}}

def _entries(stored,cycle_id):
    if not isinstance(stored,dict):raise ValueError('官网记录格式异常，已保留原记录')
    entries=stored.get('other_search_entries',{})
    if not isinstance(entries,dict) or len(entries)>16:
        raise ValueError('其他搜索入口记录格式异常，已保留原记录')
    for key,value in entries.items():
        fields={'provider','site_origin','scope','identity_sha256','revision','progress','request_sha256',
                'evidence_source','software_verified','software_submitted','updated_at'}
        if (not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key) or not isinstance(value,dict) or set(value)!=fields
                or not isinstance(value.get('provider'),str) or value['provider'] not in IDS or type(value.get('revision')) is not int
                or not 1<=value['revision']<=1000000 or value.get('evidence_source')!='user_reported_platform_progress'
                or value.get('software_verified') is not False or value.get('software_submitted') is not False):
            raise ValueError('其他搜索入口记录身份异常，已保留原记录')
        _progress(value.get('progress'))
        if (any(not isinstance(value.get(field),str) or not re.fullmatch('[0-9a-f]{64}',value[field])
                for field in ('identity_sha256','request_sha256'))
                or key!=_key(value['provider'],value['identity_sha256'])):
            raise ValueError('其他搜索入口记录范围异常，已保留原记录')
        scope=value['scope']
        if (not isinstance(scope,dict) or set(scope)!={'cycle_id','project_id','plan_sha256','site_origin','profile'}
                or scope['cycle_id']!=cycle_id or not (scope['project_id'] is None or type(scope['project_id']) is int and scope['project_id']>0)
                or not isinstance(scope['plan_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',scope['plan_sha256'])
                or not isinstance(scope['profile'],dict) or set(scope['profile'])!={'name','entity_type','website','official_urls'}):
            raise ValueError('其他搜索入口记录资料范围异常，已保留原记录')
        profile=scope['profile']
        if (not isinstance(profile['name'],str) or not profile['name'] or len(profile['name'])>500
                or profile['entity_type'] not in ('company','product') or not isinstance(profile['official_urls'],list)
                or len(profile['official_urls'])>20 or any(not isinstance(url,str) or len(url)>2000 for url in profile['official_urls'])
                or webmaster._origin(profile['website'])!=value['site_origin'] or scope['site_origin']!=value['site_origin']
                or _digest(scope)!=value['identity_sha256']):
            raise ValueError('其他搜索入口记录官网绑定异常，已保留原记录')
        try:
            timestamp=datetime.fromisoformat(value['updated_at']) if isinstance(value['updated_at'],str) and len(value['updated_at'])<=40 else None
            if timestamp is None or timestamp.tzinfo is None:raise ValueError()
        except (ValueError,TypeError):raise ValueError('其他搜索入口记录时间异常，已保留原记录') from None
    return entries

def _key(provider,identity):return _digest({'provider':provider,'identity_sha256':identity})

def overview(connection,cycle):
    result={'supported':False,'reason':'','cycle_id':cycle['id'],'project_id':cycle['project_id'],
            'revision':cycle['revision'],'plan_sha256':cycle['test_plan']['plan_sha256'],
            'site_origin':None,'sitemap_url':None,'identity_sha256':None,'context_sha256':None,
            'providers':[],'limitations':list(LIMITATIONS)}
    try:origin=webmaster._origin(cycle['profile'].get('website'))
    except ValueError as error:result['reason']=str(error);return result
    identity=_digest(_scope(cycle,origin))
    entries=_entries(webmaster._stored(connection,cycle['id']),cycle['id'])
    result.update(supported=True,site_origin=origin,sitemap_url=origin+'/sitemap.xml',identity_sha256=identity,
                  context_sha256=_digest({'identity_sha256':identity,'revision':cycle['revision']}))
    result['providers']=[{**item,'record':copy.deepcopy(entries.get(_key(item['id'],identity)))} for item in PROVIDERS]
    return result

def get_overview(database,cycle_id):
    from services.geo_content_store import _connect,_cycle
    with closing(_connect(database)) as connection:return overview(connection,_cycle(connection,cycle_id))

def save_progress(database,cycle_id,payload):
    from services.geo_content_store import _connect,_cycle
    required={'project_id','expected_plan_sha256','expected_revision','expected_context_sha256',
              'provider','expected_provider_revision','progress'}
    if (not isinstance(payload,dict) or set(payload)!=required or not isinstance(payload.get('provider'),str)
            or payload['provider'] not in IDS):
        raise ValueError('只接受百度或头条的当前官网进度，不接受任意平台、网址、文件或判效字段')
    progress=_progress(payload['progress'])
    for key in ('expected_revision','expected_provider_revision'):
        if type(payload[key]) is not int or not 0<=payload[key]<=1000000:raise ValueError('请提供当前资料和平台进度版本')
    if any(not isinstance(payload[key],str) or not re.fullmatch('[0-9a-f]{64}',payload[key])
           for key in ('expected_plan_sha256','expected_context_sha256')):raise ValueError('请刷新原计划和官网范围后保存')
    request_sha=_digest(payload)
    with closing(_connect(database)) as connection,connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle=_cycle(connection,cycle_id,for_update=True);view=overview(connection,cycle)
        if (not view['supported'] or not ((type(payload['project_id']) is int and payload['project_id']>0)
                or (payload['project_id'] is None and cycle['project_id'] is None))
                or payload['project_id']!=cycle['project_id'] or payload['expected_plan_sha256']!=view['plan_sha256']):
            raise ValueError('项目、原计划或官网范围已变化，请刷新后核对')
        stored=webmaster._stored(connection,cycle_id);entries=_entries(stored,cycle_id)
        key=_key(payload['provider'],view['identity_sha256']);record=entries.get(key)
        revision=record['revision'] if record else 0
        duplicate=bool(record and record.get('request_sha256')==request_sha and payload['expected_provider_revision']==revision-1)
        if not duplicate:
            if (payload['expected_revision']!=cycle['revision'] or payload['expected_context_sha256']!=view['context_sha256']
                    or payload['expected_provider_revision']!=revision):
                raise ValueError('官网资料或该平台进度已变化，请刷新核对；原记录未覆盖')
            if key not in entries and len(entries)>=16:raise ValueError('本轮已保留多个官网范围，请新建对应内容任务；旧记录保持')
            if revision>=1000000:raise ValueError('平台进度记录已达到当前版本上限')
            record={'provider':payload['provider'],'site_origin':view['site_origin'],'identity_sha256':view['identity_sha256'],
                    'scope':_scope(cycle,view['site_origin']),
                    'revision':revision+1,'progress':progress,'request_sha256':request_sha,
                    'evidence_source':'user_reported_platform_progress','software_verified':False,'software_submitted':False,
                    'updated_at':datetime.now(timezone.utc).isoformat()}
            entries=copy.deepcopy(entries);entries[key]=record;stored['other_search_entries']=entries
            connection.execute(webmaster.CREATE_TABLE)
            connection.execute('INSERT INTO geo_webmaster_records(cycle_id,record,updated_at) VALUES(?,?,?) '
                'ON CONFLICT(cycle_id) DO UPDATE SET record=excluded.record,updated_at=excluded.updated_at',
                (cycle_id,json.dumps(stored,ensure_ascii=False),record['updated_at']))
        return {'overview':overview(connection,cycle),'receipt':{'provider':payload['provider'],'revision':record['revision'],
                'identity_sha256':view['identity_sha256'],'request_sha256':record['request_sha256'],'idempotent':duplicate}}
