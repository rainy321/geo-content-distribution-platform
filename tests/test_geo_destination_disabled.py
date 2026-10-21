"""Disabled destinations keep history without authorizing new platform work."""
import asyncio
import copy
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import AsyncMock, Mock, patch

from services import geo_content_research as research, geo_content_store as store
from services import geo_destination_service as destinations
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_destination_browser import open_session
from tests.test_geo_content_loop import ResearchFixture, draft, facts
from tests.test_geo_destinations import config


class DisabledDestinationTests(ResearchFixture, unittest.TestCase):
    def target(self, **changes):
        target = destinations.save_target(self.database, self.cycle['project_id'], config(**changes))
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        return target

    def set_state(self, target, state):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE geo_distribution_targets SET state=? WHERE id=?', (state, target['id']))
        return destinations.get_target(self.database, self.cycle['project_id'], target['id'])

    def test_draft_generation_rejects_disabled_config_and_all_inactive_states(self):
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        target = self.target()
        for state, enabled in [('configured', False), ('disabled', True), ('archived', True), ('deleted', True)]:
            cycle = copy.deepcopy(self.cycle)
            cycle['destinations'][0]['state'] = state
            cycle['destinations'][0]['config']['enabled'] = enabled
            for writer in (research.draft_input, research.draft_prompt):
                with self.subTest(state=state, enabled=enabled, writer=writer.__name__), self.assertRaisesRegex(ValueError, '已停用'):
                    writer(cycle, 'b01', target['key'])
        self.assertEqual(research.draft_input(self.cycle, 'b01', target['key'])['destination']['id'], target['id'])
        self.assertEqual(research.draft_input(self.cycle, 'b01', 'owned_web')['platform'], 'owned_web')

    def test_disabled_target_cannot_save_a_new_draft_or_start_native_login(self):
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        target = self.target(enabled=False)
        with self.assertRaisesRegex(ValueError, '已停用'):
            store.save_draft(self.database, self.cycle['id'], 'b01', target['key'], draft(), {'method': 'synthetic'}, self.cycle['revision'])
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        with patch.object(jobs, '_start', Mock()) as start:
            result = jobs.open_destination(self.cycle['id'], target['id'])
        self.assertIn('已停用', result['error'])
        start.assert_not_called()
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['drafts'], [])
        self.assertEqual(destinations.get_target(self.database, self.cycle['project_id'], target['id'])['events'], [])

    def test_disabled_browser_entry_rejects_before_creating_profile_or_launching(self):
        for state, enabled in [('configured', False), ('disabled', True), ('archived', True), ('deleted', True)]:
            target = {'state': state, 'config': {'enabled': enabled}}
            profile = self.root / f'not-created-{state}'
            with self.subTest(state=state), patch('services.browser_runtime.launch_context', AsyncMock()) as launch:
                with self.assertRaisesRegex(ValueError, '已停用'):
                    asyncio.run(open_session(target, profile, None))
                launch.assert_not_awaited()
                self.assertFalse(profile.exists())

    def test_existing_draft_and_moderation_remain_available_while_new_fill_is_blocked(self):
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        target = self.target()
        result = store.save_draft(self.database, self.cycle['id'], 'b01', target['key'], draft(), {'method': 'synthetic'}, self.cycle['revision'])
        article_id = result['article_id']
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE articles SET status='ready' WHERE id=?", (article_id,))
        destinations.save_target(self.database, self.cycle['project_id'], config(enabled=False), target['id'], target['revision'])
        current = store.get_cycle(self.database, self.cycle['id'])
        article = next(item for item in current['drafts'] if item['article_id'] == article_id)
        saved = store.save_moderation_observation(self.database, current['id'], {
            'observation_id': uuid.uuid4().hex, 'article_id': article_id, 'target_id': target['id'],
            'state': 'rejected', 'confirmed': True, 'note': '原投稿审核结果', 'expected_article_sha256': article['article_sha256']})
        self.assertEqual(saved['drafts'][0]['content'], article['content'])
        self.assertEqual(saved['moderation_observations'][0]['state'], 'rejected')
        self.assertFalse(saved['destinations'][0]['config']['enabled'])
        with self.assertRaisesRegex(ValueError, '已停用'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], target['id'], article_id)

    def test_disabled_target_cannot_be_restored_by_review_or_late_browser_results(self):
        for state, enabled in [('configured', False), ('disabled', True), ('archived', True), ('deleted', True)]:
            target = self.target(enabled=enabled)
            target = self.set_state(target, state)
            before = copy.deepcopy(target)
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, '已停用'):
                destinations.review_block(self.database, self.cycle['project_id'], target['id'], True)
            self.assertEqual(destinations.get_target(self.database, self.cycle['project_id'], target['id'])['events'], before['events'])
            destinations.record_event(self.database, target['id'], 'login', 'editor_ready', '晚到的只读观察')
            after = destinations.get_target(self.database, self.cycle['project_id'], target['id'])
            self.assertEqual(after['state'], before['state'])
            self.assertEqual(after['config']['enabled'], enabled)
            self.assertEqual(after['events'][0]['message'], '晚到的只读观察')

    def test_editing_does_not_implicitly_reenable_and_explicit_enable_keeps_blocks_and_history(self):
        for state in ('configured', 'disabled', 'archived', 'deleted'):
            target = self.target(enabled=False)
            destinations.record_event(self.database, target['id'], 'fill', 'need_action', '原填写结果待核对')
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute('UPDATE geo_distribution_targets SET state=?,blocked_reason=? WHERE id=?', (state, '原填写结果待核对', target['id']))
            edited = destinations.save_target(self.database, self.cycle['project_id'], config(name='只改平台名称'), target['id'], target['revision'])
            self.assertFalse(edited['config']['enabled'])
            self.assertEqual(edited['state'], state)
            enabled = destinations.save_target(self.database, self.cycle['project_id'], config(enabled=True), target['id'], edited['revision'])
            destinations.assert_target_enabled(enabled)
            self.assertEqual(enabled['blocked_reason'], '原填写结果待核对')
            self.assertEqual(enabled['events'][0]['state'], 'need_action')
            self.assertEqual(len(enabled['events']), 1)

    def test_current_session_recheck_cannot_resume_a_disabled_platform(self):
        target = self.target(enabled=False)
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        jobs.job = {'id': 'synthetic-current', 'status': 'running', 'kind': 'destination_login',
                    'cycle_id': self.cycle['id'], 'target_id': target['id']}
        jobs._destination_check_task_id = jobs.job['id']
        before = copy.deepcopy(jobs.job)
        result = jobs.check_destination_session(jobs.job['id'])
        self.assertIn('已停用', result['error'])
        self.assertEqual(jobs.job, before)


if __name__ == '__main__':
    unittest.main()
