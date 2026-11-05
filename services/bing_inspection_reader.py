"""Explicit read of an already-visible Bing inspection page; never navigate.

The native Windows bridge is the only writer. HTTP clients cannot supply an
observation. UIA text stays in the child process; only bounded known fields
cross the process boundary. Native observations do not replace owner reports.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone


TIMEOUT_SECONDS = 8
MAX_STDOUT_BYTES = 8192
CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS geo_bing_native_inspections (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, project_id INTEGER NOT NULL,
    request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)'''

# Fixed program, not interpolated source. The expected URL is stdin JSON data.
# Do not add InvokePattern, SetValue, navigation, login or indexing operations.
POWERSHELL_READER = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Finish([string]$status, [string]$reason, [string]$index = 'unknown', $crawl = $null, $fetch = $null) {
    [Console]::Write((@{status=$status;reason=$reason;index_status=$index;last_crawl_text=$crawl;page_fetch_text=$fetch;observed_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress))
    exit 0
}
function Visible($node) { return $null -ne $node -and -not $node.Current.IsOffscreen }
function Value($node) {
    $pattern = $null
    if (-not $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)) { return $null }
    return [string]$pattern.Current.Value
}
function TargetMatches([string]$raw, [string]$expected, [int]$layers) {
    $candidate = $raw
    for ($n=0; $n -le $layers; $n++) {
        # Stop at a complete URL: never decode escapes inside its target path.
        if ($candidate -cmatch '^https://') { return $candidate -ceq $expected }
        if ($n -eq $layers) { return $false }
        $candidate = [Uri]::UnescapeDataString($candidate)
    }
    return $false
}
function OfficialAddress([string]$raw, [Uri]$expected) {
    if ($raw -cmatch '^(?:www\.)?bing\.com/') { $raw = 'https://' + $raw }
    $address = $null
    if (-not [Uri]::TryCreate($raw,[UriKind]::Absolute,[ref]$address)) { return $false }
    if ($address.Scheme -cne 'https' -or $address.Host -cnotin @('bing.com','www.bing.com') -or $address.Port -ne 443 -or $address.UserInfo -or $address.Fragment -or $address.AbsolutePath -cne '/webmasters/urlinspection') { return $false }
    $parameters = @{}
    foreach ($part in $address.Query.TrimStart('?').Split('&')) {
        $pair = $part.Split('=',2)
        if ($pair.Length -ne 2) { return $false }
        $name = [Uri]::UnescapeDataString($pair[0])
        if ($parameters.ContainsKey($name)) { return $false }
        $parameters[$name] = $pair[1]
    }
    if (-not $parameters.ContainsKey('siteUrl') -or (-not $parameters.ContainsKey('urlInspect') -and -not $parameters.ContainsKey('urlToInspect'))) { return $false }
    $site = $null
    if (-not [Uri]::TryCreate([Uri]::UnescapeDataString($parameters['siteUrl']),[UriKind]::Absolute,[ref]$site)) { return $false }
    if ($site.Scheme -cne 'https' -or $site.Host -cne $expected.Host -or $site.Port -ne 443 -or $site.UserInfo -or $site.Query -or $site.Fragment) { return $false }
    # A manually edited input alone does not prove the shown results belong to
    # it. The already-inspected URL in the browser address must match as well.
    if ($parameters.ContainsKey('urlInspect') -and -not (TargetMatches $parameters['urlInspect'] $expected.AbsoluteUri 1)) { return $false }
    if ($parameters.ContainsKey('urlToInspect') -and -not (TargetMatches $parameters['urlToInspect'] $expected.AbsoluteUri 2)) { return $false }
    return $true
}
function Field([string[]]$lines, [string]$label) {
    $found = @()
    for ($n=0; $n -lt $lines.Length; $n++) {
        if ($lines[$n] -ieq $label) {
            if ($n+1 -lt $lines.Length) { $found += $lines[$n+1] }
        } elseif ($lines[$n] -imatch ('^' + [Regex]::Escape($label) + '\s*:?\s*(\S.*)$')) {
            $found += $Matches[1].Trim()
        }
    }
    if ($found.Count -eq 1) { return [string]$found[0] }
    return $null
}
try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (@($payload.PSObject.Properties.Name).Count -ne 1 -or $null -eq $payload.expected_url -or $payload.expected_url -isnot [string] -or $payload.expected_url.Length -gt 2000) { Finish 'need_action' 'invalid_expected_url' }
    $expected = [Uri]$payload.expected_url
    if ($expected.Scheme -cne 'https' -or $expected.Port -ne 443 -or $expected.UserInfo -or $expected.Query -or $expected.Fragment -or $expected.AbsoluteUri -cne $payload.expected_url) { Finish 'need_action' 'invalid_expected_url' }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $scope = [System.Windows.Automation.TreeScope]::Descendants
    $pids = @(Get-Process chrome,msedge -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $windowCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Window)
    $windows = @([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$windowCondition) | Where-Object {
        $pids -contains $_.Current.ProcessId -and $_.Current.Name -match '^URL Inspection.*Bing Webmaster Tools' -and (Visible $_)
    })
    if ($windows.Count -eq 0) { Finish 'need_action' 'bing_inspection_window_missing' }
    if ($windows.Count -ne 1) { Finish 'need_action' 'multiple_bing_inspection_windows' }
    $window = $windows[0]
    $editCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)
    $addresses = @($window.FindAll($scope,$editCondition) | Where-Object { $_.Current.Name -in @('地址和搜索栏','Address and search bar') -and (Visible $_) })
    if ($addresses.Count -ne 1) { Finish 'need_action' 'browser_address_unavailable' }
    $addressBefore = Value $addresses[0]
    if (-not (OfficialAddress $addressBefore $expected)) { Finish 'need_action' 'bing_address_or_inspected_url_mismatch' }
    $documentCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Document)
    $documents = @($window.FindAll($scope,$documentCondition) | Where-Object { $_.Current.Name -match '^URL Inspection.*Bing Webmaster Tools' -and (Visible $_) })
    if ($documents.Count -ne 1) { Finish 'need_action' 'bing_document_not_unique' }
    $document = $documents[0]
    $inputCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.ValuePattern]::ValueProperty,$payload.expected_url)
    $inputs = @($document.FindAll($scope,$inputCondition) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and (Visible $_) })
    if ($inputs.Count -ne 1 -or (Value $inputs[0]) -cne $payload.expected_url) { Finish 'need_action' 'inspection_input_mismatch' }
    $textObject = $null
    if (-not $document.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$textObject)) { Finish 'need_action' 'document_text_unavailable' }
    # At most 20480 UTF-16 code units (40 KiB); a full buffer is uncertain.
    $text = $textObject.DocumentRange.GetText(20480)
    if ($text.Length -ge 20480) { Finish 'need_action' 'document_text_too_large' }
    if ((Value $addresses[0]) -cne $addressBefore -or (Value $inputs[0]) -cne $payload.expected_url -or $document.Current.Name -notmatch '^URL Inspection.*Bing Webmaster Tools') { Finish 'need_action' 'browser_page_changed' }
    if ($text -match '(?im)^\s*(Please sign in|Sign in to Bing Webmaster Tools|请登录|请先登录)\s*$') { Finish 'need_action' 'bing_login_required' }
    if ($text -match '完成安全验证|验证码|请拖动滑块|Verify you are human|verify that you are human|CAPTCHA') { Finish 'need_action' 'bing_verification_required' }
    if ($text -match 'Error encountered while inspecting URL|Unable to inspect URL') { Finish 'need_action' 'bing_inspection_error' }
    $indexed = $text -match '(?im)^\s*(Indexed successfully|URL is on Bing)\s*$'
    $notIndexed = $text -match '(?im)^\s*(URL is not on Bing|URL is not indexed|Not indexed)\s*$'
    if ($indexed -eq $notIndexed) { Finish 'need_action' 'index_result_not_visible' }
    $lines = @($text.Split([char]10) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $crawl = Field $lines 'Last crawl attempted'
    if ($crawl -and $crawl -notmatch '^(?:(?:\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}|Today|Yesterday)(?: at \d{1,2}:\d{2}(?::\d{2})?(?: ?[AP]M)?)?|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|\d{1,2}/\d{1,2}/\d{4}(?: \d{1,2}:\d{2}(?::\d{2})?(?: ?[AP]M)?)?)$') { $crawl = $null }
    $fetch = Field $lines 'Page Fetch'
    if ($fetch -and $fetch -cnotin @('Successful','Failed','Blocked','Unknown','Not available','Timed out','Success','Failure')) { $fetch = $null }
    $index = if ($indexed) { 'indexed' } else { 'not_indexed' }
    Finish 'observed' 'current_visible_page_read' $index $crawl $fetch
} catch { Finish 'need_action' 'reader_unavailable' }
'''

REASONS = {
    'windows_only': '当前功能仅支持Windows，请在官方Bing页面核对；未读取任何浏览器。',
    'powershell_unavailable': '系统PowerShell不可用，未读取浏览器。',
    'reader_timeout': '读取现有Bing页面超时；没有点击、输入或请求索引，请核对窗口后再主动读取。',
    'reader_unavailable': '现有窗口无法通过系统辅助功能安全读取，请保持官方Bing检查页可见。',
    'invalid_reader_result': '浏览器读取结果未满足安全字段约定，未导入索引结论。',
    'invalid_expected_url': '待检查网址不是当前项目的规范HTTPS地址。',
    'bing_inspection_window_missing': '未找到当前可见的Bing URL Inspection窗口，请先在日常Chrome或Edge打开并完成检查。',
    'multiple_bing_inspection_windows': '有多个Bing URL Inspection窗口，请仅保留要读取的一个检查页。',
    'browser_address_unavailable': '无法唯一核对浏览器地址栏，未读取页面结论。',
    'bing_address_or_inspected_url_mismatch': 'Bing站点或地址栏中的已检查网址与所选页面不一致，请先在Bing完成该网址检查。',
    'bing_document_not_unique': '无法唯一识别官方Bing页面文档，未导入结论。',
    'inspection_input_mismatch': 'Bing页面检查框与所选网址不完全一致，未导入结论。',
    'document_text_unavailable': 'Bing页面未暴露可安全读取的文档文本，请在普通浏览器查看。',
    'document_text_too_large': 'Bing文档超过本次读取上限，未导入不完整结论。',
    'browser_page_changed': '读取过程中浏览器页面发生变化，未导入结论。',
    'bing_login_required': 'Bing检查页要求登录，请在当前日常浏览器正常登录后再主动读取。',
    'bing_verification_required': 'Bing检查页要求安全验证，请在当前浏览器正常完成验证后再主动读取。',
    'bing_inspection_error': 'Bing当前检查页显示错误，不能将它记为未收录。',
    'index_result_not_visible': '没有唯一可识别的索引结果，请在Bing完成检查并展开索引详情后再读取。',
    'current_visible_page_read': '已只读保存当前可见Bing检查结果；页面缓存、内容版本与GEO效果仍未知。',
    'scope_changed': '读取期间项目、官网范围或原Bing记录已变化，本次结果未导入。',
    'reading': '读取意图已保存，结果尚未确认；请先刷新原记录，不要重复调用。',
}
_DATE = re.compile(r'(?:(?:\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}|Today|Yesterday)(?: at \d{1,2}:\d{2}(?::\d{2})?(?: ?[AP]M)?)?|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|\d{1,2}/\d{1,2}/\d{4}(?: \d{1,2}:\d{2}(?::\d{2})?(?: ?[AP]M)?)?)', re.I)
_FETCH = frozenset(('Successful', 'Failed', 'Blocked', 'Unknown', 'Not available', 'Timed out', 'Success', 'Failure'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _failure(reason, *, status='need_action'):
    return {'status': status, 'reason': reason, 'index_status': 'unknown',
            'last_crawl_text': None, 'page_fetch_text': None, 'observed_at': _now()}


def _powershell_executable():
    buffer = ctypes.create_unicode_buffer(32768)
    count = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise OSError('system_directory_unavailable')
    executable = Path(buffer.value) / 'WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_absolute() or not executable.is_file():
        raise OSError('powershell_unavailable')
    return str(executable)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate')
        value[key] = item
    return value


def _validate_result(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_STDOUT_BYTES:
        raise ValueError('result_size')
    result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique)
    fields = {'status', 'reason', 'index_status', 'last_crawl_text', 'page_fetch_text', 'observed_at'}
    if not isinstance(result, dict) or set(result) != fields:
        raise ValueError('result_fields')
    if result['status'] not in ('observed', 'need_action') or result['reason'] not in REASONS:
        raise ValueError('result_status')
    value = result['observed_at']
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)', value):
        raise ValueError('result_time')
    # .NET's roundtrip format has seven fractional digits; Python 3.10 accepts
    # microseconds. Validate that UTC instant without rewriting the raw field.
    parsed_time = re.sub(r'(\.\d{6})\d(?=Z|\+00:00)', r'\1', value).replace('Z', '+00:00')
    stamp = datetime.fromisoformat(parsed_time)
    if stamp.tzinfo is None:
        raise ValueError('result_time')
    if result['status'] == 'observed':
        if result['reason'] != 'current_visible_page_read' or result['index_status'] not in ('indexed', 'not_indexed'):
            raise ValueError('result_index')
        crawl, fetch = result['last_crawl_text'], result['page_fetch_text']
        if crawl is not None and (not isinstance(crawl, str) or len(crawl) > 90 or not _DATE.fullmatch(crawl)):
            raise ValueError('result_crawl')
        if fetch is not None and fetch not in _FETCH:
            raise ValueError('result_fetch')
    elif (result['reason'] == 'current_visible_page_read' or result['index_status'] != 'unknown'
          or result['last_crawl_text'] is not None or result['page_fetch_text'] is not None):
        raise ValueError('result_failure')
    return result


def read_existing_page(expected_url):
    """Run a fixed read-only UIA program, only on explicit native invocation."""
    if sys.platform != 'win32':
        return _failure('windows_only', status='unsupported')
    try:
        executable = _powershell_executable()
    except OSError:
        return _failure('powershell_unavailable')
    encoded = base64.b64encode(POWERSHELL_READER.encode('utf-16-le')).decode('ascii')
    try:
        completed = subprocess.run([executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            input=_json({'expected_url': expected_url}).encode('utf-8'), capture_output=True,
            timeout=TIMEOUT_SECONDS, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000), check=False)
    except subprocess.TimeoutExpired:
        return _failure('reader_timeout')
    except OSError:
        return _failure('reader_unavailable')
    if completed.returncode != 0:
        return _failure('reader_unavailable')
    try:
        return _validate_result(completed.stdout)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _failure('invalid_reader_result')


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_bing_native_inspections'").fetchone() is not None


def _public(record, *, idempotent=False):
    return {**{key: value for key, value in record.items() if key not in ('request_sha256', 'identity_sha256')},
            'message': REASONS[record['reason']], 'idempotent': idempotent}


def native_inspections(connection, cycle_id):
    """GET projection only; no migration or UI process is started."""
    if not _exists(connection):
        return []
    return [_public(json.loads(row[0])) for row in connection.execute(
        'SELECT record FROM geo_bing_native_inspections WHERE cycle_id=? ORDER BY created_at DESC,id DESC LIMIT 15', (cycle_id,))]


def _context(connection, cycle_id, url, expected):
    from services.geo_content_store import _cycle
    from services import geo_indexnow_service, webmaster_service
    cycle = _cycle(connection, cycle_id)
    scope = geo_indexnow_service._scope(connection, cycle)
    geo_indexnow_service._inside(url, scope)
    view = webmaster_service.overview(connection, cycle)
    if not isinstance(expected, str) or view['context_sha256'] != expected:
        raise ValueError('官网或Bing资料已变化，请刷新当前任务后再读取')
    return cycle, scope, view


def read_for_cycle(database, cycle_id, url, context_sha256, request_id):
    from services.geo_content_store import _connect, _cycle
    from services.geo_external_baseline_contract import validate_request_id
    from services import webmaster_service
    validate_request_id(request_id)
    if not isinstance(cycle_id, str) or not re.fullmatch('[a-f0-9]{32}', cycle_id):
        raise ValueError('当前内容轮次编号无效')
    if not isinstance(url, str) or not isinstance(context_sha256, str) or not re.fullmatch('[a-f0-9]{64}', context_sha256):
        raise ValueError('请提供当前项目的官网页面与Bing资料摘要')
    request_sha = _digest({'cycle_id': cycle_id, 'url': url, 'context_sha256': context_sha256, 'request_id': request_id})
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        if _exists(connection):
            row = connection.execute('SELECT request_sha256,record FROM geo_bing_native_inspections WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if row[0] != request_sha:
                    raise ValueError('同一读取请求编号已绑定其他页面或项目，原记录未覆盖')
                return {'overview': webmaster_service.overview(connection, _cycle(connection, cycle_id)),
                        'inspection': _public(json.loads(row[1]), idempotent=True)}
        cycle, scope, view = _context(connection, cycle_id, url, context_sha256)
        record = {'id': uuid.uuid4().hex, 'cycle_id': cycle_id, 'project_id': cycle['project_id'],
            'url': url, 'context_sha256': context_sha256, 'identity_sha256': scope['context_sha256'],
            'request_id': request_id, 'request_sha256': request_sha, 'created_at': _now(),
            **_failure('reading', status='reading'), 'source': 'native_browser_ui', 'independent': False,
            'platform_timezone': 'unknown', 'timezone': 'unknown', 'page_freshness': 'unknown',
            'content_version_verified': False, 'geo_effect_verified': False,
            'browser_opened': False, 'navigation_performed': False, 'inspect_invoked': False, 'indexing_requested': False}
        connection.execute(CREATE_TABLE)
        connection.execute('INSERT INTO geo_bing_native_inspections VALUES(?,?,?,?,?,?,?,?)',
            (record['id'], cycle_id, cycle['project_id'], request_id, request_sha, record['created_at'], 'reading', _json(record)))
    # Intent first. A repeated UUID can only return its existing row, even while
    # UIA is blocked or after an interrupted process; it never triggers a reread.
    try:
        observation = read_existing_page(url)
    except Exception:
        observation = _failure('reader_unavailable')
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            cycle, scope, view = _context(connection, cycle_id, url, context_sha256)
            if scope['context_sha256'] != record['identity_sha256']:
                raise ValueError('identity_changed')
        except (ValueError, TypeError, KeyError):
            observation = _failure('scope_changed')
            cycle = _cycle(connection, cycle_id)
        record.update(observation)
        updated = connection.execute('UPDATE geo_bing_native_inspections SET status=?,record=? WHERE id=? AND status=?',
            (record['status'], _json(record), record['id'], 'reading'))
        if updated.rowcount != 1:
            raise ValueError('读取记录已变化，请查看原记录，不要自动重新读取')
        return {'overview': webmaster_service.overview(connection, cycle), 'inspection': _public(record)}
