"""Offline synthetic forms/transports only; never launch a real browser or use a DB."""
import asyncio
from contextlib import nullcontext
import copy
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from services import geo_directory_browser as browser


def fields(model='GLM'):
    return {'name':'公开示例产品','url':'https://product.example/api','rate':'PUBLIC_PRICE_TEXT',
            'source':'https://product.example/pricing','models':[model],'contact':'PUBLIC_CONTACT_TEXT','notes':'公开备注'}


class Request:
    def __init__(self,body=None,*,method='POST',url=browser.LEAD_URL):
        self.method,self.url,self.body=method,url,body
    @property
    def post_data_json(self):
        if isinstance(self.body,Exception):raise self.body
        return self.body


class Route:
    def __init__(self,request,events=None):self.request=request;self.events=events if events is not None else [];self.continued=self.aborted=0
    async def continue_(self):self.continued+=1;self.events.append('continue')
    async def abort(self):self.aborted+=1;self.events.append('abort')


class Response:
    def __init__(self,request,body=None,*,status=200,url=browser.LEAD_URL):
        self.request,self.body,self.status,self.url=request,body,status,url
    async def json(self):
        if isinstance(self.body,Exception):raise self.body
        return self.body


class SubmissionGuardTests(unittest.IsolatedAsyncioTestCase):
    def guard(self,**changes):
        self.intent=AsyncMock(return_value=None);self.receipt=AsyncMock(return_value=None);self.progress=[]
        options={'before_submit':self.intent,'save_receipt':self.receipt,'progress':self.progress.append,**changes}
        state=browser.SubmissionGuard(browser.expected_transport(fields()),**options)
        state.form_ready=state.route_guard_installed=True
        state.form_submit_seen=True
        return state

    async def permitted(self,state):
        request=Request(copy.deepcopy(state.expected));route=Route(request)
        await state.route(route);self.assertEqual(route.continued,1);return request

    async def test_intent_persists_before_one_and_only_one_post(self):
        events=[]
        state=self.guard(before_submit=lambda value:events.append('persist'))
        one=Route(Request(state.expected),events);two=Route(Request(state.expected),events)
        await state.route(one);await state.route(two)
        self.assertEqual(events,['persist','continue','abort'])
        self.assertEqual((one.continued,two.continued,two.aborted),(1,0,1))
        self.assertEqual(state.allowed_posts,1)

    async def test_concurrent_posts_cannot_pass_the_durable_fence_twice(self):
        async def persist(value):await asyncio.sleep(0.01)
        state=self.guard(before_submit=AsyncMock(side_effect=persist))
        routes=[Route(Request(state.expected)) for _ in range(2)]
        await asyncio.gather(*(state.route(r) for r in routes))
        self.assertEqual(sum(r.continued for r in routes),1)
        self.assertEqual(state.before_submit.await_count,1)

    async def test_every_reviewed_transport_field_is_immutable(self):
        for key in browser.expected_transport(fields()):
            state=self.guard();body=copy.deepcopy(state.expected);body[key]='CHANGED'
            route=Route(Request(body));await state.route(route)
            with self.subTest(key=key):
                self.assertEqual(route.aborted,1);self.assertEqual(state.allowed_posts,0)
                self.intent.assert_not_awaited();self.assertEqual(state.result(normal_close_confirmed=True)['stage'],'submission_unknown')

    async def test_noncanonical_official_url_method_or_non_json_is_blocked(self):
        cases=[{'url':browser.LEAD_URL+'?x=1'},
               {'url':browser.LEAD_URL+'/'},{'url':'http://grokcode.cn/api/lead'},{'method':'PUT'}]
        for item in cases:
            state=self.guard();route=Route(Request(state.expected,**item));await state.route(route)
            with self.subTest(item=item):self.assertEqual(route.aborted,1);self.intent.assert_not_awaited()
        state=self.guard();route=Route(Request(ValueError('private body')));await state.route(route)
        self.assertEqual(route.aborted,1);self.intent.assert_not_awaited()

    async def test_callback_exception_aborts_and_never_yields_positive_no_send(self):
        state=self.guard(before_submit=AsyncMock(side_effect=RuntimeError('PRIVATE_CALLBACK_ERROR')))
        route=Route(Request(state.expected));await state.route(route)
        self.assertEqual((route.continued,route.aborted),(0,1));self.assertTrue(state.intent_attempted)
        self.assertFalse(state.result(normal_close_confirmed=True)['normal_close_confirmed'])

    async def test_negative_or_untyped_callback_acknowledgement_is_not_success(self):
        for value in (False,True,'untyped success',1):
            state=self.guard(before_submit=AsyncMock(return_value=value))
            route=Route(Request(state.expected));await state.route(route)
            with self.subTest(value=value):self.assertEqual(route.continued,0);self.assertEqual(route.aborted,1)
        state=self.guard(save_receipt=AsyncMock(return_value=False));request=await self.permitted(state)
        await state.response(Response(request,{'ok':True,'persisted':True,'intake_uid':'lead-test-1'}))
        self.assertFalse(state.receipt_saved)
        self.assertEqual(state.save_receipt.await_count,1)

    async def test_unbound_response_is_ignored_then_bound_receipt_saved_once(self):
        state=self.guard();request=await self.permitted(state)
        body={'ok':True,'persisted':True,'intake_uid':'lead-synthetic-1','message':'PRIVATE_SERVER_TEXT'}
        await state.response(Response(Request(state.expected),body));self.receipt.assert_not_awaited()
        await state.response(Response(request,body));await state.response(Response(request,body))
        self.assertEqual(self.receipt.await_count,1);self.assertTrue(state.receipt_saved)
        result=state.result(normal_close_confirmed=True);self.assertEqual(result['stage'],'submitted_pending_review')
        self.assertFalse(result['normal_close_confirmed'])
        for value in ('PUBLIC_CONTACT_TEXT','PUBLIC_PRICE_TEXT','PRIVATE_SERVER_TEXT'):
            self.assertNotIn(value,json.dumps([self.progress,result]))

    async def test_failed_or_ambiguous_receipt_never_becomes_pending(self):
        bodies=[{'ok':True,'persisted':False,'intake_uid':'lead-test-1'}, {'ok':1,'persisted':True,'intake_uid':'lead-test-1'},
                {'ok':True,'persisted':True,'intake_uid':True},{'ok':True,'persisted':True,'intake_uid':''},
                {'ok':True,'persisted':True,'intake_uid':'line\nbreak'},ValueError('private response')]
        for body in bodies:
            state=self.guard();request=await self.permitted(state);await state.response(Response(request,body))
            with self.subTest(body=type(body).__name__):
                self.receipt.assert_not_awaited();self.assertEqual(state.result()['stage'],'submission_unknown')
        for status,url in [(302,browser.LEAD_URL),(500,browser.LEAD_URL),(200,browser.LEAD_URL+'?other=1')]:
            state=self.guard();request=await self.permitted(state)
            await state.response(Response(request,{'ok':True,'persisted':True,'intake_uid':'lead-1'},status=status,url=url))
            self.receipt.assert_not_awaited()

    async def test_receipt_persistence_failure_remains_unknown_and_is_not_retried(self):
        state=self.guard(save_receipt=AsyncMock(side_effect=ValueError('uncertain save')));request=await self.permitted(state)
        response=Response(request,{'ok':True,'persisted':True,'intake_uid':'lead-test-1'})
        await state.response(response);await state.response(response)
        self.assertFalse(state.receipt_saved);self.assertEqual(state.save_receipt.await_count,1)
        self.assertEqual(state.result()['stage'],'submission_unknown')

    async def test_inspection_only_blocks_directory_writes_without_submit_callback(self):
        state=self.guard(inspection_only=True,before_submit=None,save_receipt=None)
        route=Route(Request(state.expected));await state.route(route)
        self.assertEqual(route.aborted,1);self.assertFalse(state.result(normal_close_confirmed=True)['normal_close_confirmed'])
        self.assertEqual(state.result()['stage'],'inspection_closed')

    async def test_normal_auth_captcha_telemetry_and_unrelated_posts_never_read_body(self):
        urls=['https://grokcode.cn/api/auth/login','https://grokcode.cn/api/captcha/verify',
              'https://verify.example/challenge','https://grokcode.cn/api/telemetry',
              'https://analytics.example/collect','https://other.example/ordinary-action',
              'https://third-party.example/api/lead','https://grokcode.cn.evil.example/api/lead']
        urls += ['https://grokcode.cn/api/'+part for part in ('session','oauth','password','token','security','cookie','sign-in','sign-up')]
        for inspection in (False,True):
            for url in urls:
                state=self.guard(inspection_only=inspection)
                route=Route(Request(AssertionError('AUTH_BODY_MUST_NOT_BE_READ'),url=url))
                await state.route(route)
                with self.subTest(url=url,inspection=inspection):
                    self.assertEqual((route.continued,route.aborted),(1,0));self.assertEqual(state.allowed_posts,0)
                    self.assertEqual(state.blocked_writes,0);self.intent.assert_not_awaited()

    async def test_unknown_business_endpoint_and_lead_auth_suffix_cannot_bypass_fence(self):
        for url in ('https://grokcode.cn/api/submit-transit','https://grokcode.cn/api/intake',
                    'https://grokcode.cn/api/lead/captcha','https://grokcode.cn/api/leads?auth=1'):
            state=self.guard();route=Route(Request(AssertionError('UNKNOWN_BODY_NOT_READ'),url=url))
            await state.route(route)
            with self.subTest(url=url):self.assertEqual(route.aborted,1);self.intent.assert_not_awaited()

    async def test_generic_same_origin_write_is_fenced_only_after_known_form_submit(self):
        state=self.guard();state.form_submit_seen=False
        before=Route(Request(AssertionError('UNKNOWN_BODY_NOT_READ'),url='https://grokcode.cn/api/forms'))
        await state.route(before);self.assertEqual((before.continued,before.aborted),(1,0))
        state.form_submit_seen=True
        after=Route(Request(AssertionError('UNKNOWN_BODY_NOT_READ'),url='https://grokcode.cn/api/forms'))
        await state.route(after);self.assertEqual((after.continued,after.aborted),(0,1))
        self.intent.assert_not_awaited();self.assertEqual(state.allowed_posts,0)

    async def test_boolean_dom_signal_closes_submit_binding_route_race_without_body_reads(self):
        state=self.guard();state.form_submit_seen=False;state.submit_signal_read=AsyncMock(return_value=True)
        route=Route(Request(AssertionError('UNKNOWN_BODY_NOT_READ'),url='https://grokcode.cn/api/forms'))
        await state.route(route);self.assertEqual(route.aborted,1);state.submit_signal_read.assert_awaited_once()
        self.intent.assert_not_awaited()

    async def test_canonical_lead_without_observed_form_submit_is_not_credited(self):
        state=self.guard();state.form_submit_seen=False
        route=Route(Request(state.expected));await state.route(route)
        self.assertEqual(route.aborted,1);self.intent.assert_not_awaited()

    def test_submit_observer_reads_only_metadata_and_never_intercepts_or_submits(self):
        script=browser.submit_observer_script('synthetic_binding','synthetic_marker')
        self.assertIn("addEventListener('submit'",script)
        for forbidden in ('preventDefault','stopPropagation','requestSubmit','.submit(','.click(','.value','innerText','password'):
            self.assertNotIn(forbidden,script)

    async def test_normal_no_send_requires_route_guard_and_zero_risk_flags(self):
        state=self.guard();self.assertEqual(state.result(normal_close_confirmed=True)['stage'],'closed_not_submitted')
        for key,value in [('route_guard_installed',False),('intent_attempted',True),('allowed_posts',1),('blocked_writes',1),('unsafe',True)]:
            state=self.guard();setattr(state,key,value)
            with self.subTest(key=key):self.assertFalse(state.result(normal_close_confirmed=True)['normal_close_confirmed'])

    def test_missing_persistence_callbacks_fail_closed(self):
        with self.assertRaises(ValueError):browser.SubmissionGuard({},before_submit=None,save_receipt=None)


class Control:
    def __init__(self,form,key,*,checkbox=False):self.form,self.key,self.checkbox=form,key,checkbox
    async def count(self):return 1
    async def is_visible(self):return True
    async def is_editable(self):return True
    async def evaluate(self,_):return True
    async def input_value(self):return self.form.values[self.key]
    async def fill(self,value):self.form.fills.append(self.key);self.form.values[self.key]=value
    async def is_checked(self):return self.key in self.form.checked
    async def check(self):self.form.checks.append(self.key);self.form.checked.add(self.key)
    def locator(self,selector):
        if selector=='xpath=ancestor::form':return self.form
        raise AssertionError(selector)


class Checked:
    def __init__(self,form):self.form=form
    async def count(self):return len(self.form.checked)


class Form:
    def __init__(self):self.values={k:'' for k in ('name','url','rate','source','contact','notes')};self.checked=set();self.fills=[];self.checks=[];self.available=True
    async def count(self):return 1
    async def is_visible(self):return True
    async def wait_for(self,**_):
        if not self.available:raise asyncio.TimeoutError()
    def locator(self,selector):
        if selector=='input[type="checkbox"]:checked':return Checked(self)
        match=re.fullmatch(r'input\[name="([a-z]+)"\]',selector)
        if match:return Control(self,match.group(1))
        raise AssertionError(selector)
    def get_by_label(self,label,exact=False):
        assert exact;return Control(self,label,checkbox=True)


class Page:
    def __init__(self,context=None):self.url=browser.ENTRY_URL;self.form=Form();self.handlers={};self.context=context;self.closed=False;self.main_frame=object()
    def locator(self,selector):
        if selector=='input[name="name"]':return Control(self.form,'name')
        raise AssertionError(selector)
    def on(self,name,callback):self.handlers[name]=callback
    async def goto(self,url,**_):
        assert self.context.route_installed,'No navigation before route installation'
        self.url=url
        if self.context.inspection:asyncio.get_running_loop().call_soon(self.close_user)
    def close_user(self):self.closed=True;self.handlers['close']()
    def is_closed(self):return self.closed
    async def evaluate(self,expression,*_):
        if expression=='() => true':return True
        assert expression=='(key) => window[key] === true';return self.context.form_submit_seen


class FormPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_inputs_and_exact_single_model_readback_without_submit_click(self):
        for label in ('GLM','GPT','千问 / Qwen','图片 / 视频模型'):
            page=Page();await browser.prepare_form(page,fields(label))
            self.assertEqual(set(page.form.fills),set(page.form.values));self.assertEqual(page.form.checks,[label])
            await browser.readback_form(page.form,fields(label))

    async def test_existing_different_input_or_other_model_is_not_overwritten(self):
        page=Page();page.form.values['notes']='other existing text'
        with self.assertRaises(ValueError):await browser.prepare_form(page,fields())
        self.assertEqual(page.form.fills+page.form.checks,[])
        page=Page();page.form.checked={'GPT'}
        with self.assertRaises(ValueError):await browser.prepare_form(page,fields())
        self.assertEqual(page.form.fills+page.form.checks,[])

    async def test_changed_readback_or_wrong_page_fails(self):
        page=Page();page.url='https://other.example/'
        with self.assertRaises(ValueError):await browser.prepare_form(page,fields())
        page=Page();await browser.prepare_form(page,fields());page.form.values['rate']='CHANGED'
        with self.assertRaises(ValueError):await browser.readback_form(page.form,fields())


class Browser:
    def __init__(self):self.handlers={}
    def on(self,name,callback):self.handlers[name]=callback
    def is_connected(self):return True


class Context:
    def __init__(self,inspection=False):
        self.handlers={};self.browser=Browser();self.inspection=inspection;self.route_installed=False;self.closed=False
        self.pages=[Page(self)];self.form_submit_seen=False
    def on(self,name,callback):self.handlers[name]=callback
    async def route(self,pattern,callback):assert pattern=='**/*';self.guard=callback;self.route_installed=True
    async def expose_binding(self,name,callback):assert self.route_installed;self.submit_binding=callback
    async def add_init_script(self,*,script):assert self.route_installed;self.observer_script=script
    async def close(self):self.closed=True;self.handlers.get('close',lambda:None)()


class Runtime:
    async def __aenter__(self):return object()
    async def __aexit__(self,*_):pass


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        (Path(__file__).resolve().parents[1]/'.tmp').mkdir(exist_ok=True)

    async def run_session(self,mode,*,inspection=False):
        context=Context(inspection);events=[];receipts=[];intent=[]
        self.last_context=context
        close_signal=asyncio.Event();check_signal=asyncio.Event()
        if mode=='form_error_controlled':context.pages[0].form.values['notes']='different existing public text'
        if mode=='verification':context.pages[0].form.available=False
        if mode=='close_while_navigation':
            async def blocked_navigation(*_,**__):
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:
                    context.navigation_cancelled=True
                    raise
            context.pages[0].goto=blocked_navigation
        async def act():
            context.form_submit_seen=True
            context.submit_binding({'page':context.pages[0],'frame':context.pages[0].main_frame})
            request=Request(browser.expected_transport(fields()));await context.guard(Route(request))
            if mode=='success':context.handlers['response'](Response(request,{'ok':True,'persisted':True,'intake_uid':'lead-test-1'}))
            else:context.pages[0].close_user()
        def progress(value):
            events.append(value)
            if mode=='callback_error':raise RuntimeError('PRIVATE_CALLBACK_ERROR')
            if mode=='close_while_navigation' and value['stage']=='opening':close_signal.set()
            if value['stage']=='awaiting_human_confirmation':
                if mode=='normal_close':asyncio.get_running_loop().call_soon(context.pages[0].close_user)
                elif mode in ('controlled_close','verification'):close_signal.set()
                elif mode=='crash':asyncio.get_running_loop().call_soon(context.pages[0].handlers['crash'])
                elif mode=='disconnect':asyncio.get_running_loop().call_soon(context.browser.handlers['disconnected'])
                elif mode in ('intent_close','success'):asyncio.create_task(act())
                elif mode=='left_adapter':
                    context.pages[0].url='https://other.example/'
                    context.pages[0].handlers['framenavigated'](type('Frame',(),{'parent_frame':None,'url':'https://other.example/'})())
                    asyncio.create_task(act())
                elif mode=='cancel':asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            elif value['stage']=='submitted_pending_review':asyncio.get_running_loop().call_soon(context.pages[0].close_user)
            elif value['stage']=='needs_manual_review' and mode=='form_error_controlled':close_signal.set()
            elif value['stage']=='awaiting_human_verification' and mode=='verification':
                context.pages[0].form.available=True;check_signal.set()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory,\
             patch('services.browser_runtime.launch_context',new=AsyncMock(return_value=context)) as launched,\
             patch('services.geo_test_runner.exclusive_lock',side_effect=lambda _:nullcontext()):
            result=await browser.open_session({'stage':'opening','fields':fields()},Path(directory)/'fresh-profile','synthetic-browser',
                progress=progress,before_submit=lambda body:intent.append(body),save_receipt=lambda body,status:receipts.append((body,status)),
                playwright_factory=Runtime,inspection_only=inspection,close_requested=close_signal,
                check_requested=check_signal,timeout_seconds=1)
        self.assertTrue(context.closed);self.assertTrue(launched.await_args.args[2]['service_workers']=='block')
        self.assertTrue(launched.await_args.args[2]['chromium_sandbox'])
        self.assertEqual(launched.await_args.args[2]['args'],['--start-maximized'])
        self.assertIsNone(launched.await_args.args[2]['viewport']);self.assertTrue(launched.await_args.args[2]['no_viewport'])
        self.assertFalse(any('normal_close_confirmed' in value for value in events))
        return result,context,intent,receipts

    async def test_normal_controlled_close_without_any_directory_write_returns_positive(self):
        result,_,intent,_=await self.run_session('controlled_close')
        self.assertEqual(result['stage'],'closed_not_submitted');self.assertTrue(result['normal_close_confirmed'])
        self.assertTrue(result['route_guard_installed']);self.assertEqual(result['allowed_posts'],0);self.assertEqual(intent,[])

    async def test_crash_disconnect_callback_error_and_timeout_never_return_positive(self):
        for mode in ('normal_close','crash','disconnect','callback_error','timeout'):
            result,*_=await self.run_session(mode)
            with self.subTest(mode=mode):self.assertEqual(result['stage'],'submission_unknown');self.assertFalse(result['normal_close_confirmed'])

    async def test_close_after_intent_preserves_unknown_and_does_not_resend(self):
        result,_,intent,receipts=await self.run_session('intent_close')
        self.assertEqual(result['stage'],'submission_unknown');self.assertEqual(result['allowed_posts'],1)
        self.assertEqual(len(intent),1);self.assertEqual(receipts,[])

    async def test_success_requires_receipt_and_remains_pending_review_after_close(self):
        result,_,intent,receipts=await self.run_session('success')
        self.assertEqual(result['stage'],'submitted_pending_review');self.assertFalse(result['normal_close_confirmed'])
        self.assertEqual(len(intent),1);self.assertEqual(len(receipts),1)

    async def test_form_error_without_business_send_still_allows_controlled_no_send_close(self):
        result,context,intent,_=await self.run_session('form_error_controlled')
        self.assertTrue(result['normal_close_confirmed']);self.assertEqual(result['stage'],'closed_not_submitted')
        self.assertEqual(context.pages[0].form.fills,[]);self.assertEqual(intent,[])

    async def test_waits_for_known_form_after_normal_verification_without_auth_reads(self):
        result,context,intent,_=await self.run_session('verification')
        self.assertEqual(result['stage'],'closed_not_submitted');self.assertEqual(len(context.pages[0].form.fills),6)
        self.assertEqual(intent,[])

    async def test_task_cancellation_is_not_a_controlled_close(self):
        task=asyncio.create_task(self.run_session('cancel'))
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.last_context.closed)

    async def test_leaving_adapter_stops_directory_authorization(self):
        result,_,intent,_=await self.run_session('left_adapter')
        self.assertEqual(result['stage'],'submission_unknown');self.assertEqual(result['allowed_posts'],0)
        self.assertEqual(result['blocked_writes'],1);self.assertEqual(intent,[])

    async def test_route_install_failure_closes_context_without_positive_evidence(self):
        context=Context();context.route=AsyncMock(side_effect=RuntimeError('synthetic route failure'))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory,\
             patch('services.browser_runtime.launch_context',new=AsyncMock(return_value=context)),\
             patch('services.geo_test_runner.exclusive_lock',side_effect=lambda _:nullcontext()):
            result=await browser.open_session({'stage':'opening','fields':fields()},Path(directory)/'fresh',None,
                before_submit=lambda _:None,save_receipt=lambda *_:None,playwright_factory=Runtime)
        self.assertTrue(context.closed);self.assertFalse(result['route_guard_installed'])
        self.assertEqual(result['stage'],'submission_unknown');self.assertFalse(result['normal_close_confirmed'])

    async def test_controlled_close_error_is_unknown(self):
        original=Context.close
        async def failed(context):
            await original(context)
            raise RuntimeError('synthetic close failed')
        with patch.object(Context,'close',failed):
            result,*_=await self.run_session('controlled_close')
        self.assertEqual(result['stage'],'submission_unknown');self.assertFalse(result['normal_close_confirmed'])

    async def test_controlled_close_interrupts_initial_navigation_without_native_cancellation(self):
        result,context,intent,_=await self.run_session('close_while_navigation')
        self.assertEqual(result['stage'],'closed_not_submitted');self.assertTrue(result['normal_close_confirmed'])
        self.assertTrue(context.navigation_cancelled);self.assertEqual(intent,[])

    async def test_inspection_opens_only_fixed_list_and_does_not_fill_or_unlock(self):
        result,context,intent,_=await self.run_session('inspection',inspection=True)
        self.assertEqual(context.pages[0].url,browser.LIST_URL);self.assertEqual(context.pages[0].form.fills,[])
        self.assertEqual(result['stage'],'inspection_closed');self.assertFalse(result['normal_close_confirmed']);self.assertEqual(intent,[])

    async def test_existing_profile_or_existing_submit_intent_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            profile=Path(directory)/'profile';profile.mkdir();(profile/'old-state').write_text('synthetic')
            with patch('services.browser_runtime.launch_context',new=AsyncMock()) as launch:
                with self.assertRaises(ValueError):await browser.open_session({'stage':'opening','fields':fields()},profile,None,before_submit=lambda _:None,save_receipt=lambda *_:None)
                launch.assert_not_awaited()
            with self.assertRaises(ValueError):await browser.open_session({'stage':'opening','fields':fields(),'submit_intent':{'already':True}},Path(directory)/'fresh',None,before_submit=lambda _:None,save_receipt=lambda *_:None)


if __name__=='__main__':unittest.main()
