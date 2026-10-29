"""Bounded observations of server HTML; no rendering, JSON-LD expansion or I/O.

Metadata semantics: https://html.spec.whatwg.org/multipage/semantics.html
Crawler directives: https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag
JSON-LD syntax: https://www.w3.org/TR/json-ld11/
Canonical signals: https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls
"""
from __future__ import annotations

import json
import re
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from services import webmaster_service as public_http


_TYPES = frozenset(('Organization', 'Corporation', 'LocalBusiness', 'WebSite', 'WebPage', 'Product',
                    'Service', 'SoftwareApplication', 'WebApplication', 'Article', 'TechArticle', 'FAQPage'))


def text(value, maximum):
    return re.sub(r'\s+', ' ', value).strip()[:maximum]


def name_present(value, configured_name):
    if not isinstance(configured_name, str) or not configured_name.strip():
        return None
    # No short aliases, homophone matching or substring matches inside a longer
    # name. False means this conservative literal rule did not observe a match.
    return re.search(r'(?<![\w])' + re.escape(configured_name) + r'(?![\w])', value, re.I) is not None


def display_url(base, value):
    if (not isinstance(value, str) or not value or len(value) > 2000
            or any(ord(char) <= 32 or ord(char) == 127 for char in value) or '\\' in value):
        return None
    try:
        absolute = urljoin(base, value)
        parsed = urlsplit(absolute)
        if parsed.scheme not in ('http', 'https') or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.port not in (None, 443 if parsed.scheme == 'https' else 80):
            return None
        # Reuse the existing public-origin syntax/IP validator, without DNS.
        public_http._origin('https://' + parsed.netloc.removesuffix(':80') if parsed.scheme == 'http' else absolute)
    except ValueError:
        return None
    return absolute if len(absolute) <= 2000 else None


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = 0
        self.head = 0
        self.body = 0
        self.suppressed = []
        self.in_title = False
        self.title = []
        self.body_text = []
        self.descriptions = []
        self.canonicals = []
        self.robots = []
        self.base = None
        self.links = []
        self.anchor = None
        self.json_scripts = []
        self.script = None

    def budget(self):
        self.events += 1
        if self.events > 20000:
            raise ValueError('html_structure_limit')

    def handle_starttag(self, tag, attributes):
        self.budget()
        attrs = dict(attributes)
        if tag == 'head':
            self.head += 1
        if tag == 'body':
            self.body += 1
        if tag == 'title' and self.head:
            self.in_title = True
        if tag == 'meta' and self.head:
            name = (attrs.get('name') or '').casefold()
            content = attrs.get('content') or ''
            if name == 'description' and len(self.descriptions) < 5:
                self.descriptions.append(content[:1000])
            if name in ('robots', 'googlebot', 'bingbot') and len(self.robots) < 20:
                self.robots.append({'agent': name, 'content': content[:1000]})
        if tag == 'link' and self.head and 'canonical' in (attrs.get('rel') or '').casefold().split() and len(self.canonicals) < 5:
            self.canonicals.append((attrs.get('href') or '')[:2000])
        if tag == 'base' and self.head and self.base is None:
            self.base = (attrs.get('href') or '')[:2000]
        if tag in ('script', 'style', 'template'):
            self.suppressed.append(tag)
        if tag == 'script' and (attrs.get('type') or '').casefold().split(';', 1)[0] == 'application/ld+json':
            self.script = [] if len(self.json_scripts) < 10 else None
        if tag == 'a' and self.body and not self.suppressed and len(self.links) < 60:
            self.anchor = {'href': (attrs.get('href') or '')[:2000], 'text': []}

    def handle_endtag(self, tag):
        self.budget()
        if tag == 'head':
            self.head = max(0, self.head - 1)
        if tag == 'body':
            self.body = max(0, self.body - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.script is not None:
            self.json_scripts.append(''.join(self.script))
            self.script = None
        if tag in self.suppressed:
            self.suppressed = self.suppressed[:self.suppressed.index(tag)]
        if tag == 'a' and self.anchor is not None:
            self.links.append(self.anchor)
            self.anchor = None

    def handle_data(self, data):
        self.budget()
        if self.script is not None:
            if sum(map(len, self.script)) + len(data) > 65536:
                self.script = ['INVALID:oversize-json-ld']
            else:
                self.script.append(data)
        if self.in_title and not self.suppressed:
            self.title.append(data)
        if self.body and not self.suppressed:
            self.body_text.append(data)
            if self.anchor is not None:
                self.anchor['text'].append(data)


def _json_ld(scripts, configured_name, base):
    entries, invalid = [], 0

    def reject_constant(value):
        raise ValueError('Nonfinite JSON')

    for script in scripts:
        try:
            parsed = json.loads(script, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            invalid += 1
            continue
        pending, visited = [(parsed, 0)], 0
        while pending and visited < 100 and len(entries) < 20:
            value, depth = pending.pop()
            visited += 1
            if depth > 8:
                continue
            if isinstance(value, list):
                pending.extend((row, depth + 1) for row in value[:30])
            elif isinstance(value, dict):
                kinds = value.get('@type', [])
                kinds = [kinds] if isinstance(kinds, str) else kinds if isinstance(kinds, list) else []
                kinds = [kind.rsplit('/', 1)[-1] for kind in kinds[:10] if isinstance(kind, str)]
                relevant = [kind for kind in kinds if kind in _TYPES]
                if relevant:
                    name = value.get('name') if isinstance(value.get('name'), str) else None
                    entries.append({'types': relevant, 'name': text(name, 300) if name else None,
                        'url': display_url(base, value.get('url')),
                        'configured_name_equal': name.strip() == configured_name if name and configured_name else None})
                pending.extend((row, depth + 1) for row in list(value.values())[:50] if isinstance(row, (dict, list)))
    return {'script_count': len(scripts), 'invalid_script_count': invalid, 'entries': entries,
            'remote_contexts_fetched': False, 'standards_validation_performed': False,
            'basis': 'server_markup_not_independent_fact_check'}


def inspect_html(data, metadata, final_url, configured_name, official_identity):
    message = Message()
    message['content-type'] = metadata.get('content_type') or ''
    charset = (message.get_content_charset() or 'utf-8').casefold()
    allowed = {'utf-8', 'utf8', 'utf-16', 'ascii', 'gb18030', 'gbk', 'gb2312', 'big5', 'iso-8859-1', 'windows-1252'}
    warning = charset not in allowed or metadata.get('content_type_truncated') is True
    raw = data.decode(charset if charset in allowed else 'utf-8', errors='replace')
    parser = _Page()
    parser.feed(raw)
    parser.close()
    base = display_url(final_url, parser.base) or final_url
    canonical_urls = list(dict.fromkeys(url for value in parser.canonicals if (url := display_url(base, value))))
    robots = [{'agent': item['agent'], 'directives': [part for part in re.split(r'[,\s]+', item['content'].lower()) if part][:30]}
              for item in parser.robots]
    header = metadata.get('x_robots_tag') or ''
    noindex = any(set(item['directives']) & {'noindex', 'none'} for item in robots)
    noindex = noindex or bool(re.search(r'(?:^|[,\s:])(?:noindex|none)(?=$|[,\s])', header, re.I))
    if not noindex and metadata.get('x_robots_tag_truncated') is True:
        noindex = None
    title = text(''.join(parser.title), 300)
    description = text(parser.descriptions[0], 500) if parser.descriptions else ''
    body = ' '.join(parser.body_text)
    body_name = name_present(body, configured_name) if body else None
    body_literal = configured_name in body if body and isinstance(configured_name, str) and configured_name else None
    title_literal = configured_name in title if title and isinstance(configured_name, str) and configured_name else None
    links, seen = [], set()
    for item in parser.links:
        url = display_url(base, item['href'])
        if not url or url in seen or len(links) >= 30:
            continue
        seen.add(url)
        parsed, final = urlsplit(url), urlsplit(final_url)
        scope = ('configured_official' if official_identity(url) else
                 'same_origin_unconfigured' if (parsed.scheme, parsed.netloc) == (final.scheme, final.netloc) else 'external')
        links.append({'url': url, 'text': text(''.join(item['text']), 200), 'scope': scope, 'automatically_fetched': False})
    findings = []
    for condition, code, label in (
        (not title, 'title_not_observed', '本次服务端HTML未观察到title'),
        (not description, 'description_not_observed', '本次服务端HTML未观察到meta description'),
        (not canonical_urls, 'canonical_not_observed', '本次HTML未观察到有效canonical；它并非索引保证或必备成功项'),
        (bool(canonical_urls) and any(not official_identity(url) for url in canonical_urls),
         'canonical_outside_configured_scope', 'canonical指向未登记的官方页面范围；仅记录，未访问或判断搜索引擎选择'),
        (noindex, 'noindex_observed', '观察到noindex/none规则；适用爬虫及实际索引需另核对'),
        (body_literal is False, 'configured_name_not_observed', '本次正文未观察到登记完整名称的精确文字，不作主体或业务事实结论'),
        (body_literal is True and body_name is False, 'configured_name_context_ambiguous',
         '完整名称文字出现，但未满足独立名称边界，需核对上下文；不作主体或业务结论'),
        (warning or '\ufffd' in raw, 'decoding_uncertain', '字符编码存在不确定性，名称观察需结合原页核对')):
        if condition:
            findings.append({'code': code, 'message': label})
    return {'title': title or None, 'meta_description': description or None, 'canonical_urls': canonical_urls,
        'robots_meta': robots, 'x_robots_tag': header[:1000], 'robots_noindex_observed': noindex,
        'name_text_match': {'configured_name': configured_name,
            'body_text_present': body_name,
            'title_text_present': name_present(title, configured_name) if title else None,
            'body_literal_substring_present': body_literal,
            'title_literal_substring_present': title_literal,
            'identity_verified': False,
            'basis': 'bounded_full_name_literal_not_entity_or_business_verification'},
        'body_text_characters': len(body), 'json_ld': _json_ld(parser.json_scripts, configured_name, base),
        'page_links': links, 'findings': findings, 'browser_rendered': False,
        'text_encoding': charset if charset in allowed else 'utf-8', 'decoding_warning': warning or '\ufffd' in raw}
