"""API搜 candidate contracts use temporary databases and synthetic forms only."""
import copy
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from services import geo_directory_service as directory, geo_directory_browser as browser
from services import geo_destination_service as destinations
from tests.test_geo_directory_service import DirectoryFixture
from tests.test_geo_destinations import config
from tests.test_geo_directory_browser import Request, Route, Context, Runtime
from services.desktop_geo_jobs import DesktopGeoJobs


class ApisouServiceTests(DirectoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.target = destinations.save_target(self.database, self.cycle['project_id'], config(
            name='API搜合成目录', home_url='https://www.apisou.com/', source_scope='https://www.apisou.com/',
            editor_url=directory.APISOU_ENTRY_URL, login_url=directory.APISOU_ENTRY_URL,
            title_selector='', body_selector='', publication_use='commercial'))
        self.fields = {'name': self.cycle['profile']['name'], 'url': self.cycle['profile']['website'],
                       'tagline': '', 'type': '商业', 'contact': '', 'note': '', 'model_lines': [], 'modalities': []}

    def external(self, record, **changes):
        return directory.register_external_receipt(self.database, self.cycle_id, record['id'], {
            'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'],
            'confirmed': True, 'status': 'submitted_pending_review', 'evidence_sha256': 'a' * 64,
            'observed_at': '2026-01-01T09:12+08:00', 'observed_at_precision': 'minute',
            'note': '本人已在正常平台页面看到此申请的提交成功提示，收录尚未核验', **changes})

    def test_independent_schema_requires_no_price_or_model_and_keeps_grok_separate(self):
        overview = directory.get_overview(self.database, self.cycle_id, self.target['id'])
        self.assertEqual(overview['adapter'], directory.APISOU_ADAPTER)
        self.assertEqual(overview['field_required'], ['name', 'url'])
        self.assertTrue(overview['prefill_only']); self.assertFalse(overview['native_submission_supported'])
        record = self.save()
        self.assertEqual(record['fields'], self.fields)
        self.assertEqual(record['adapter'], directory.APISOU_ADAPTER)
        self.assertFalse(record['can_record_external_receipt'])
        with self.assertRaises(ValueError): self.save(application_id=record['id'], revision=record['revision'], fields={**self.fields, 'rate': 'wrong schema'})
        for values in ({'contact': 'operator@example.org'}, {'model_lines': ['unverified']},
                       {'modalities': ['文本', '文本']}, {'note': '成本价：123'},
                       {'url': 'https://foreign.example.org/'}, {'note': 'https://foreign.example.org/'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                directory._validate_application_fields(directory.APISOU_ADAPTER, {**self.fields, **values}, self.cycle['profile'])

    def test_shared_directory_recognition_is_exact_and_does_not_match_ordinary_articles(self):
        for url in ('https://grokcode.cn/api-transit', 'https://www.apiznz.com', 'https://www.apiznz.com/', 'https://www.apiznz.com/site/lists/', 'https://www.apisou.com', 'https://www.apisou.com/'):
            self.assertTrue(destinations.is_directory_target({'config': {'source_scope': url}}), url)
        for url in ('https://www.apisou.com/submit', 'https://www.apisou.com.evil.example/',
                    'https://name:password@www.apisou.com/', 'https://127.0.0.1/',
                    'https://example.internal/', 'https://www.apisou.com/?',
                    'https://www.apisou.com/#x', 'https://grokcode.cn', 'https://grokcode.cn/', 'https://grokcode.cn/api-transit/other',
                    'https://blog.csdn.net/example/', 'https://www.apisou.com/../',
                    'https://www.apisou.com\\@evil.example/'):
            self.assertFalse(destinations.is_directory_target({'config': {'source_scope': url}}), url)
        for value in (None, {}, {'config': None}, {'config': {'source_scope': False}}):
            self.assertFalse(destinations.is_directory_target(value))

    def test_exact_target_scope_and_project_adapter_deduplication(self):
        record = self.save()
        second = destinations.save_target(self.database, self.cycle['project_id'], self.target['config'])
        view = directory.get_overview(self.database, self.cycle_id, second['id'])
        with self.assertRaises(ValueError): directory.save_application(self.database, self.cycle_id,
            {'target_id': second['id'], 'expected_context_sha256': view['context_sha256'], 'fields': self.fields})
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id'])['fields'], self.fields)
        for key in ('home_url', 'source_scope', 'login_url', 'editor_url'):
            changed = {**self.target['config'], key: 'https://www.apisou.com.attacker.org/submit'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                directory._known_target({'config': changed, 'state': 'configured'}, directory.APISOU_ADAPTER)
        self.assertNotEqual(directory._target_adapter({'source_scope': 'https://unknown.example.org/'}), directory.APISOU_ADAPTER)
        with self.assertRaises(ValueError): directory._known_target({'config': self.target['config'], 'state': 'configured'}, 'unknown-adapter')

    def test_same_project_known_directories_keep_independent_records_and_schemas(self):
        apisou = self.save()
        grok = next(item for item in destinations.list_targets(self.database, self.cycle['project_id'])
                    if item['config']['source_scope'] == directory.LIST_URL)
        grok_view = directory.get_overview(self.database, self.cycle_id, grok['id'])
        grok_record = directory.save_application(self.database, self.cycle_id, {'target_id': grok['id'],
            'expected_context_sha256': grok_view['context_sha256'], 'fields': {'name': self.fields['name'], 'url': self.fields['url'],
                'rate': 'Public price', 'source': self.fields['url'], 'models': ['GLM'], 'contact': '', 'notes': ''}})
        self.assertNotEqual(apisou['id'], grok_record['id'])
        for target, expected, schema_key in ((self.target, apisou, 'model_lines'), (grok, grok_record, 'rate')):
            rows = directory.get_overview(self.database, self.cycle_id, target['id'])['applications']
            self.assertEqual([row['id'] for row in rows], [expected['id']])
            self.assertIn(schema_key, rows[0]['fields'])

    def test_review_nonce_cas_and_prefill_never_authorize_native_submit_or_receipt(self):
        session = self.session()
        with self.assertRaises(ValueError): directory.begin_submit(self.database, self.cycle_id, session['id'], session['session_nonce'], {})
        with self.assertRaises(ValueError): directory.record_receipt(self.database, self.cycle_id, session['id'], session['session_nonce'],
            {'ok': True, 'persisted': True, 'intake_uid': 'lead-fake-01'}, 200)
        actual = directory.get_application(self.database, self.cycle_id, session['id'])
        self.assertFalse(actual['submit_attempted']); self.assertIsNone(actual['receipt'])
        with self.assertRaises(ValueError): directory.reserve_session(self.database, self.cycle_id, session['id'], session['review_sha256'], 'replay')
        closed = directory.close_session(self.database, self.cycle_id, session['id'], session['session_nonce'],
            no_submission_evidence={'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0})
        self.assertEqual(closed['stage'], 'closed_not_submitted'); self.assertTrue(closed['can_record_external_receipt'])
        reviewed = self.review(closed)
        self.assertEqual(reviewed['fields'], self.fields); self.assertTrue(reviewed['can_open'])
        destinations.save_target(self.database, self.cycle['project_id'], {**self.target['config'], 'style': 'Updated scope'}, self.target['id'], self.target['revision'])
        with self.assertRaises(ValueError): directory.reserve_session(self.database, self.cycle_id, reviewed['id'], reviewed['review_sha256'], reviewed['open_nonce'])

    def test_only_real_owner_reported_pending_receipt_with_hash_and_no_invented_uid(self):
        record = self.review(self.save())
        for changes in ({'evidence_sha256': None}, {'intake_uid': 'invented'}, {'status': 'accepted'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.external(record, **changes)
        received = self.external(record)
        self.assertEqual(received['evidence_level'], 'owner_reported')
        self.assertIsNone(received['receipt']['intake_uid']); self.assertTrue(received['receipt']['owner_reported'])
        self.assertFalse(received['receipt']['public_listing_verified']); self.assertFalse(received['geo_effect_evidence'])
        self.assertFalse(received['native_submit_attempted']); self.assertFalse(received['can_open'])
        with self.assertRaises(ValueError): self.external(received)


class ApisouGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_mutations_are_blocked_without_reading_body_or_persisting_intent(self):
        for url in (directory.APISOU_ENTRY_URL, 'https://www.apisou.com/another-action', 'https://other.example.org/submit'):
            state = browser.ApisouPrefillGuard()
            state.route_guard_installed = True
            route = Route(Request(RuntimeError('Body must never be read'), url=url))
            await state.route(route)
            self.assertEqual((route.aborted, route.continued), (1, 0)); self.assertEqual(state.allowed_posts, 0)
            self.assertFalse(state.intent_attempted); self.assertFalse(state.receipt_saved)
            self.assertEqual(state.result(normal_close_confirmed=True)['stage'], 'submission_unknown')

    async def test_get_only_controlled_close_preserves_preparation_not_receipt(self):
        state = browser.ApisouPrefillGuard(); state.route_guard_installed = True
        route = Route(Request(method='GET', url=directory.APISOU_ENTRY_URL)); await state.route(route)
        self.assertEqual(route.continued, 1)
        self.assertEqual(state.result(normal_close_confirmed=True)['stage'], 'closed_not_submitted')
        self.assertEqual(state.result()['stage'], 'submission_unknown')


class PublicControl:
    def __init__(self, form, key): self.form, self.key = form, key
    async def count(self): return 1
    async def is_visible(self): return True
    async def is_editable(self): return True
    async def evaluate(self, expression):
        if expression == 'e=>e.defaultChecked': return self.key in self.form.default_checked
        return self.key not in self.form.invalid
    async def input_value(self): return self.form.values[self.key]
    async def fill(self, value): self.form.writes.append(self.key); self.form.values[self.key] = value
    async def is_checked(self): return self.key in self.form.checked
    async def check(self): self.form.writes.append(self.key); self.form.checked.add(self.key)
    async def uncheck(self): self.form.writes.append('uncheck:' + self.key); self.form.checked.discard(self.key)
    async def select_option(self, *, label): self.form.writes.append(self.key); self.form.values[self.key] = label
    async def inner_text(self): return self.form.values['type']
    async def all_text_contents(self): return directory.APISOU_TYPES
    def locator(self, selector):
        if selector == 'xpath=ancestor::form': return self.form
        if selector in ('option', 'option:checked'): return self
        raise AssertionError(selector)


class ApisouForm:
    def __init__(self):
        self.values = {key: '' for key in ('name', 'url', 'tagline', 'contact', 'note', 'website')}
        self.values['type'] = '商业'; self.checked = {'mod_文本'}; self.default_checked = {'mod_文本'}
        self.writes = []; self.invalid = set()
        self.schema_names = ['name', 'url', 'tagline', 'contact', 'note', 'website', 'type',
            *('line_' + item for item in directory.APISOU_MODEL_LINES), *('mod_' + item for item in directory.APISOU_MODALITIES)]
    async def count(self): return 1
    async def is_visible(self): return True
    async def wait_for(self, **_): pass
    def locator(self, selector):
        if selector == '[required]': return self
        if selector == 'input:not([type="hidden"]),textarea,select':
            return type('Names', (), {'evaluate_all': AsyncMock(side_effect=lambda _: sorted(self.schema_names))})()
        if selector == 'input[type="checkbox"]': return type('Count', (), {'count': AsyncMock(return_value=17)})()
        import re
        match = re.fullmatch(r'(?:input|select|textarea)\[name="([^"]+)"\](?: option:checked)?', selector)
        if match: return PublicControl(self, match.group(1))
        raise AssertionError(selector)
    async def evaluate_all(self, _): return ['name', 'url']


INITIAL_MARKER = '__geo_apisou_initial_' + 'a' * 32


async def initial_state_evaluate(page, expression, value):
    """Lifecycle fake only; the private offline DOM suite executes the actual JS."""
    if expression == '(key)=>window[key]?.isPristine()===true':
        return page.initial_marker == value and not page.touched and not page.claimed
    if expression == '(value)=>window[value.marker]?.claimInitialState(value.clearDefault)===true':
        if page.initial_marker != value['marker'] or page.touched or page.claimed or page.claim_fails:
            return False
        page.claimed = True
        if value['clearDefault']:
            page.form.writes.append('normalize-default:mod_文本'); page.form.checked.discard('mod_文本')
        return True
    raise AssertionError(expression)


class ApisouFormTests(unittest.IsolatedAsyncioTestCase):
    def page(self):
        form = ApisouForm()
        return type('Page', (), {'url': directory.APISOU_ENTRY_URL, 'form': form,
            'initial_marker': INITIAL_MARKER, 'touched': False, 'claimed': False, 'claim_fails': False,
            'evaluate': initial_state_evaluate, 'locator': lambda _, selector: PublicControl(form, 'name')})()
    async def prepare(self, page, fields):
        return await browser.prepare_apisou_form(page, fields, initial_state_marker=INITIAL_MARKER)
    def fields(self):
        return {'name': 'Synthetic Product', 'url': 'https://example.org/', 'tagline': '', 'type': '商业', 'contact': '', 'note': 'Public note', 'model_lines': [], 'modalities': []}
    async def test_fill_and_readback_optional_defaults_without_touching_honeypot_or_submit(self):
        page = self.page(); fields = self.fields(); before = copy.deepcopy(fields)
        await self.prepare(page, fields)
        self.assertEqual(page.form.writes, ['normalize-default:mod_文本', 'name', 'url', 'note'])
        self.assertEqual(fields, before)
        self.assertEqual(page.form.checked, set()); self.assertEqual(page.form.values['website'], '')
        await browser.readback_apisou_form(page.form, fields)
        page.form.values['url'] = 'https://changed.example.org/'
        with self.assertRaises(ValueError): await browser.readback_apisou_form(page.form, fields)
    async def test_existing_content_or_choices_stop_before_first_write(self):
        for change in ('existing_note', 'existing_model', 'changed_control'):
            page = self.page()
            if change == 'existing_note': page.form.values['note'] = 'Keep existing'
            elif change == 'existing_model': page.form.checked.add('line_openai')
            else: page.form.invalid.add('url')
            with self.subTest(change=change), self.assertRaises(ValueError): await self.prepare(page, self.fields())
            self.assertEqual(page.form.writes, [])

    async def test_user_choice_or_other_platform_default_is_never_cleared(self):
        for change in ('user_checked_text', 'other_checked_model', 'other_default_model', 'missing_text_default'):
            page = self.page()
            if change == 'user_checked_text': page.form.default_checked.clear()
            elif change == 'other_checked_model': page.form.checked.add('line_openai')
            elif change == 'other_default_model': page.form.default_checked.add('line_openai')
            else:
                page.form.default_checked.clear(); page.form.checked.clear()
            prior = set(page.form.checked)
            with self.subTest(change=change), self.assertRaises(ValueError):
                await self.prepare(page, self.fields())
            self.assertEqual(page.form.checked, prior); self.assertEqual(page.form.writes, [])

    async def test_known_default_can_only_be_normalized_after_all_schema_and_initial_state_checks(self):
        for change in ('unknown_optional_field', 'duplicate_known_field', 'matching_existing_note', 'different_type', 'late_invalid_checkbox'):
            page = self.page()
            if change == 'unknown_optional_field': page.form.schema_names.append('new_optional')
            elif change == 'duplicate_known_field': page.form.schema_names.append('name')
            elif change == 'matching_existing_note': page.form.values['note'] = self.fields()['note']
            elif change == 'different_type': page.form.values['type'] = '开源'
            else: page.form.invalid.add('mod_向量')
            fields = self.fields()
            if change == 'different_type': fields['type'] = '开源'
            with self.subTest(change=change), self.assertRaises(ValueError): await self.prepare(page, fields)
            self.assertEqual(page.form.writes, []); self.assertIn('mod_文本', page.form.checked)

    async def test_approved_text_selection_is_retained_and_pristine_unchecked_state_stays_unchecked(self):
        page = self.page(); fields = self.fields(); fields['modalities'] = ['文本']
        await self.prepare(page, fields)
        self.assertEqual(page.form.checked, {'mod_文本'}); self.assertNotIn('normalize-default:mod_文本', page.form.writes)
        page = self.page(); page.form.checked.clear()
        await self.prepare(page, self.fields())
        self.assertEqual(page.form.checked, set()); self.assertNotIn('normalize-default:mod_文本', page.form.writes)

    async def test_failed_atomic_initial_state_claim_stops_before_any_write(self):
        page = self.page(); fields = self.fields()
        page.claim_fails = True
        with self.assertRaisesRegex(ValueError, '已有交互或初始状态变化'):
            await self.prepare(page, fields)
        self.assertEqual(page.form.writes, []); self.assertIn('mod_文本', page.form.checked)
        self.assertEqual(fields['modalities'], [])

    async def test_missing_touched_or_already_claimed_initial_state_never_fills(self):
        for change in ('missing', 'touched', 'claimed'):
            page = self.page()
            if change == 'missing': page.initial_marker = None
            elif change == 'touched': page.touched = True
            else: page.claimed = True
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '缺少未交互'):
                await self.prepare(page, self.fields())
            self.assertEqual(page.form.writes, []); self.assertEqual(page.form.checked, {'mod_文本'})
        with self.assertRaisesRegex(ValueError, '缺少未交互'):
            await browser.prepare_apisou_form(self.page(), self.fields())
        with self.assertRaises(ValueError): browser.apisou_initial_state_script('untrusted-marker')

    async def test_late_touch_during_preflight_stops_before_first_write(self):
        page = self.page(); original = page.evaluate
        async def touch_before_claim(expression, value):
            if 'claimInitialState' in expression: page.touched = True
            return await original(expression, value)
        page.evaluate = touch_before_claim
        with self.assertRaisesRegex(ValueError, '已有交互或初始状态变化'):
            await self.prepare(page, self.fields())
        self.assertEqual(page.form.writes, [])

    async def test_post_normalization_platform_reset_cannot_pass_exact_readback(self):
        page = self.page(); original = PublicControl.fill
        async def reset_on_fill(control, value):
            await original(control, value)
            control.form.checked.add('mod_文本')
        with patch.object(PublicControl, 'fill', new=reset_on_fill):
            with self.assertRaisesRegex(ValueError, '回读不一致'): await self.prepare(page, self.fields())
        self.assertIn('mod_文本', page.form.checked)

    async def test_actual_session_dispatch_uses_only_prefill_and_controlled_close(self):
        import asyncio
        context = Context(); context.pages[0].form = ApisouForm()
        page = context.pages[0]; page.initial_marker = None
        page.touched = page.claimed = page.claim_fails = False
        original_evaluate, original_init = page.evaluate, context.add_init_script
        async def evaluate(expression, *values):
            if 'isPristine' in expression or 'claimInitialState' in expression:
                return await initial_state_evaluate(page, expression, values[0])
            return await original_evaluate(expression, *values)
        async def add_init_script(*, script):
            await original_init(script=script)
            if 'claimInitialState' in script:
                import re
                page.initial_marker = re.search(r'__geo_apisou_initial_[a-f0-9]{32}', script).group()
        page.evaluate, context.add_init_script = evaluate, add_init_script
        close_signal = asyncio.Event(); stages = []
        def progress(value):
            stages.append(value['stage'])
            if value['stage'] == 'awaiting_human_confirmation': close_signal.set()
        with (tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp') as temporary,
             patch('services.browser_runtime.launch_context', new=AsyncMock(return_value=context)),
             patch('services.geo_test_runner.exclusive_lock', side_effect=lambda _: nullcontext())):
            result = await browser.open_session({'adapter': directory.APISOU_ADAPTER, 'stage': 'opening', 'fields': self.fields()},
                Path(temporary) / 'fresh', 'synthetic-browser', progress=progress,
                before_submit=AsyncMock(side_effect=AssertionError('No submission')),
                save_receipt=AsyncMock(side_effect=AssertionError('No native receipt')),
                playwright_factory=Runtime, close_requested=close_signal, timeout_seconds=1)
        self.assertTrue(context.closed); self.assertEqual(context.pages[0].url, directory.APISOU_ENTRY_URL)
        self.assertIn('awaiting_human_confirmation', stages)
        self.assertTrue(page.claimed); self.assertIsNotNone(page.initial_marker)
        self.assertEqual(result['stage'], 'closed_not_submitted'); self.assertEqual(result['allowed_posts'], 0)
        with self.assertRaises(ValueError): await browser.open_session({'adapter': 'unknown'}, Path('unused'), 'unused')


class ApisouBridgeTests(DirectoryFixture, unittest.TestCase):
    def test_native_bridge_preserves_review_scope_and_separates_adapter_profile(self):
        target = destinations.save_target(self.database, self.cycle['project_id'], config(name='API搜',
            home_url='https://www.apisou.com/', source_scope='https://www.apisou.com/',
            editor_url=directory.APISOU_ENTRY_URL, login_url=directory.APISOU_ENTRY_URL,
            title_selector='', body_selector='', publication_use='commercial'))
        view = directory.get_overview(self.database, self.cycle_id, target['id'])
        fields = {'name': self.cycle['profile']['name'], 'url': self.cycle['profile']['website'],
                  'tagline': '', 'type': '商业', 'contact': '', 'note': '', 'model_lines': [], 'modalities': []}
        draft = directory.save_application(self.database, self.cycle_id, {'target_id': target['id'],
            'expected_context_sha256': view['context_sha256'], 'fields': fields})
        reviewed = self.review(draft); frozen = copy.deepcopy(reviewed['fields'])
        jobs = DesktopGeoJobs(self.root); self.addCleanup(jobs.shutdown)
        async def fake_window(record, profile, _executable, **options):
            self.assertEqual(profile.parent.name, directory.APISOU_ADAPTER)
            self.assertNotIn(directory.ADAPTER, profile.parts)
            self.assertEqual(record['review_sha256'], reviewed['review_sha256'])
            self.assertEqual(record['fields'], frozen)
            options['progress']({'stage': 'opening', 'window_open': True})
            options['progress']({'stage': 'awaiting_human_confirmation', 'window_open': True})
            return {'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0}
        with patch('services.geo_directory_browser.open_session', side_effect=fake_window):
            ack = jobs.open_directory_application(self.cycle_id, reviewed['id'], reviewed['review_sha256'], reviewed['open_nonce'])
            jobs.worker.join(timeout=5); self.assertFalse(jobs.worker.is_alive())
            self.assertEqual(ack['kind'], 'directory_application')
        saved = directory.get_application(self.database, self.cycle_id, reviewed['id'])
        self.assertEqual(saved['stage'], 'closed_not_submitted'); self.assertEqual(saved['fields'], frozen)
        self.assertIsNone(saved['receipt']); self.assertFalse(saved['submit_attempted'])
        apiznz = destinations.save_target(self.database, self.cycle['project_id'], config(name='API指南针',
            home_url='https://www.apiznz.com/', source_scope='https://www.apiznz.com/',
            editor_url=directory.APIZNZ_ENTRY_URL, login_url=directory.APIZNZ_ENTRY_URL,
            title_selector='', body_selector='', publication_use='commercial'))
        from urllib.parse import urlsplit
        view = directory.get_overview(self.database, self.cycle_id, apiznz['id'])
        existing = directory.save_application(self.database, self.cycle_id, {'target_id': apiznz['id'],
            'expected_context_sha256': view['context_sha256'], 'fields': {'name': fields['name'],
                'domain': urlsplit(fields['url']).hostname, 'intro': 'Public synthetic description', 'api_base': ''}})
        external_only = self.review(existing)
        self.assertFalse(external_only['can_open']); self.assertFalse(external_only['can_inspect'])
        with patch('services.geo_directory_browser.open_session') as not_opened:
            self.assertIn('error', jobs.open_directory_application(self.cycle_id, external_only['id'], external_only['review_sha256'], None))
            not_opened.assert_not_called()
