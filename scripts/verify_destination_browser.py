"""Exercise the real Edge editor driver against intercepted, synthetic pages.

No external site is contacted; no final submission is made. Run from the desktop
build environment. Output is a local verification artifact, not a platform test.
"""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from services.desktop_geo_jobs import edge_executable, chrome_executable
from services.browser_runtime import launch_context
from services.geo_destination_browser import fill_editor


async def verify(output):
    output.mkdir(parents=True, exist_ok=True)
    profiles = output / 'profiles' / uuid.uuid4().hex
    requests = []
    results = []
    article = {'title': '合成平台填写验证', 'content': '这是合成稿件，仅用于软件验证。\n不是已发布文章，也不是真实客户案例。'}
    config = {'editor_url': 'https://editor.synthetic.example.org/write', 'title_selector': '#title', 'body_selector': '#body'}
    template = '''<!doctype html><meta charset="utf-8"><title>本机合成编辑器</title>
    <h1>本机合成平台编辑器，非真实外部网站</h1><script>window.submits=0</script>
    <form onsubmit="window.submits++;event.preventDefault()"><input id="title" name="title">
    BODY<button type="submit">发布（测试中不会点击）</button></form>'''
    async with async_playwright() as playwright:
        context = await launch_context(playwright, profiles / 'driver', {'executable_path':edge_executable(), 'headless':True}, chrome_executable())
        fixture = {'html': template.replace('BODY', '<textarea id="body"></textarea>')}
        async def route_request(route):
            requests.append({'url': route.request.url, 'method': route.request.method})
            assert route.request.url.startswith(('https://editor.synthetic.example.org/', 'https://login.synthetic.example.org/'))
            await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture['html'])
        await context.route('**/*', route_request)
        page = await context.new_page()
        browser_version = await page.evaluate('navigator.userAgent')
        async def load(html, url=config['editor_url']):
            fixture['html'] = html
            await page.goto(url)
        for kind, body in (('textarea', '<textarea id="body"></textarea>'), ('contenteditable', '<div id="body" contenteditable="true"></div>')):
            await load(template.replace('BODY', body))
            value = await fill_editor(page, config, article)
            assert value['state'] == 'filled'
            assert await page.evaluate('window.submits') == 0
            results.append(kind + '_filled_without_submit')
        await page.screenshot(path=str(output / 'synthetic-editor-filled.png'), full_page=True)
        for name, html, url, selectors in (
            ('existing_draft', template.replace('BODY', '<textarea id="body">已有草稿，必须保留</textarea>'), config['editor_url'], config),
            ('ambiguous_selector', template.replace('BODY', '<input id="title"><textarea id="body"></textarea>'), config['editor_url'], config),
            ('verification_warning', template.replace('BODY', '<p>请完成安全验证</p><textarea id="body"></textarea>'), config['editor_url'], config),
            ('auth_redirect', template.replace('BODY', '<textarea id="body"></textarea>'), 'https://login.synthetic.example.org/login', config),
            ('same_control', template.replace('BODY', '<textarea id="body"></textarea>'), config['editor_url'], {**config, 'body_selector': '#title'}),
            ('readback_mismatch', template.replace('BODY', '<textarea id="body" oninput="this.value=this.value.slice(0,4)"></textarea>'), config['editor_url'], config),
        ):
            await load(html, url)
            try:
                await fill_editor(page, selectors, article)
            except ValueError:
                results.append(name + '_stopped')
            else:
                raise AssertionError(name + ' unexpectedly filled')
            assert await page.evaluate('window.submits') == 0
            if name not in ('readback_mismatch',):
                assert await page.locator('#title').first.input_value() == ''
        await context.close()
        # Dedicated persistent profiles retain their own session, never each other's.
        for name, should_exist in (('account-a', False), ('account-a', True), ('account-b', False)):
            persistent = await launch_context(playwright, profiles / name, {'executable_path':edge_executable(), 'headless':True}, chrome_executable())
            cookies = await persistent.cookies('https://editor.synthetic.example.org/')
            assert any(item['name'] == 'synthetic-session' for item in cookies) is should_exist
            if name == 'account-a' and not should_exist:
                await persistent.add_cookies([{'name': 'synthetic-session', 'value': 'synthetic-no-credentials', 'url': 'https://editor.synthetic.example.org/', 'expires': 2000000000}])
            await persistent.close()
        results.append('persistent_profiles_reopen_and_remain_isolated')
    assert all(item['method'] == 'GET' for item in requests)
    verification = {'kind': 'synthetic_real_chromium_destination_driver', 'browser_version':browser_version, 'passed': True, 'checks': results,
                    'external_site_requests': 0, 'platform_publications': 0, 'synthetic_non_get_requests': 0}
    (output / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/desktop-verification/destination-browser')
    asyncio.run(verify(parser.parse_args().output))
