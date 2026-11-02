"""Offline real-Window integration for production timer functions; no native app."""
import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright


async def main(payload):
    utils = Path(payload['utils']).resolve()
    component = 'import {bingAwait} from "/geoBingInspection.js";let activeWait=null;\n' + payload['waitFor'] + '\nexport {waitFor};'
    files = {'/': ('text/html', '<!doctype html><title>Offline Window timers</title>'),
             '/geoBingInspection.js': ('text/javascript', (utils/'geoBingInspection.js').read_text(encoding='utf-8')),
             '/geoWebmaster.js': ('text/javascript', (utils/'geoWebmaster.js').read_text(encoding='utf-8')),
             '/component-wait.js': ('text/javascript', component)}
    requests, errors = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='msedge' if sys.platform == 'win32' else None, headless=True)
        context = await browser.new_context(service_workers='block')
        async def serve(route):
            request = route.request
            from urllib.parse import urlsplit
            parsed = urlsplit(request.url)
            if request.method == 'GET' and parsed.netloc == 'geo-timer.test' and parsed.path in files:
                content_type, body = files[parsed.path]
                await route.fulfill(status=200, content_type=content_type, body=body)
            else:
                requests.append(request.url)
                await route.abort()
        await context.route('**/*', serve)
        page = await context.new_page()
        page.on('pageerror', lambda cause: errors.append(str(cause)))
        await page.goto('http://geo-timer.test/', wait_until='load')
        result = await page.evaluate('''async () => {
          const {bingAwait}=await import('/geoBingInspection.js');
          const {waitFor}=await import('/component-wait.js');
          let oldReceiverFailure=false;
          try {const timers={setTimeout,clearTimeout};const id=timers.setTimeout(()=>{},1000);timers.clearTimeout(id);}
          catch(error){oldReceiverFailure=error.name==='TypeError' && error.message==='Illegal invocation';}
          const cases=[];
          const defaultValue=await bingAwait(Promise.resolve('default-ok'),undefined,1000).promise;
          if(defaultValue!=='default-ok')throw new Error('Default timer changed resolved value');cases.push('default-resolve');
          try {await bingAwait(new Promise(()=>{}),undefined,10).promise;throw new Error('Default timeout did not reject');}
          catch(error){if(!error.message.includes('读取回执超时'))throw error;}cases.push('default-timeout');
          const componentValue=await waitFor(Promise.resolve('component-ok'),1000);
          if(componentValue!=='component-ok')throw new Error('Component timer changed resolved value');cases.push('component-resolve');
          try {await waitFor(new Promise(()=>{}),10);throw new Error('Component timeout did not reject');}
          catch(error){if(!error.message.includes('读取回执超时'))throw error;}cases.push('component-timeout');
          return {cases,old_receiver_failure_reproduced:oldReceiverFailure};
        }''')
        await browser.close()
    result.update(page_errors=errors, external_requests=len(requests))
    assert not errors and not requests, result
    print(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main(json.load(sys.stdin)))
