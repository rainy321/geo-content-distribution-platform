"""Native bridge wiring with fake browser callbacks, no app or real browser."""
import asyncio
import json
import threading
import unittest
from unittest.mock import patch

from services import geo_directory_service as directory
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_directory_service import DirectoryFixture


class DirectoryBridgeTests(DirectoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def wait(self):
        self.jobs.worker.join(timeout=5)
        self.assertFalse(self.jobs.worker.is_alive())
        return self.jobs.get_task()

    def test_native_ack_and_durable_intent_precede_the_fake_request_then_matched_receipt_is_pending(self):
        record = self.review(self.save())
        events = []

        async def browser(value, profile, executable, **options):
            self.assertEqual(profile.parent.name, directory.ADAPTER)
            self.assertFalse(options['inspection_only'])
            self.assertFalse(profile.exists())
            options['progress']({'stage': 'opening', 'window_open': True})
            options['progress']({'stage': 'awaiting_human_confirmation', 'window_open': True})
            options['before_submit'](directory.expected_transport(value['fields']))
            persisted = directory.get_application(self.database, self.cycle_id, record['id'])
            self.assertTrue(persisted['submit_attempted'])
            self.assertEqual(persisted['stage'], 'submission_unknown')
            events.append('fake_request_can_now_continue')
            options['save_receipt']({'ok': True, 'persisted': True, 'intake_uid': 'lead-native-fixture'}, 200)
            return {'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 1, 'blocked_writes': 0}

        with patch('services.geo_directory_browser.open_session', side_effect=browser):
            ack = self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
            self.assertEqual(ack['kind'], 'directory_application')
            self.assertEqual(ack['cycle_id'], self.cycle_id)
            self.assertEqual(ack['directory_application_id'], record['id'])
            task = self.wait()
        self.assertEqual(events, ['fake_request_can_now_continue'])
        self.assertEqual(task['directory_application_id'], record['id'])
        self.assertEqual(task['result']['directory_application']['stage'], 'submitted_pending_review')
        self.assertEqual(task['id'], task['result']['directory_application']['native_task_id'])
        self.assertFalse(task['result']['directory_application']['can_open'])
        self.assertNotIn('session_nonce', json.dumps(task))

    def test_exception_before_post_is_unknown_and_inspection_does_not_reset_lock(self):
        record = self.review(self.save())

        async def disconnected(*args, **options):
            options['progress']({'stage': 'opening', 'window_open': True})
            raise RuntimeError('synthetic browser detached')

        with patch('services.geo_directory_browser.open_session', side_effect=disconnected), self.assertLogs(level='ERROR'):
            self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
            self.wait()
        before = directory.get_application(self.database, self.cycle_id, record['id'])
        self.assertEqual(before['stage'], 'submission_unknown')
        self.assertFalse(before['can_review'])

        async def inspect(*args, **options):
            self.assertTrue(options['inspection_only'])
            self.assertIsNone(options['before_submit'])
            self.assertIsNone(options['save_receipt'])
            options['progress']({'stage': 'inspection_open', 'window_open': True})
            return {'stage': 'inspection_closed'}

        with patch('services.geo_directory_browser.open_session', side_effect=inspect):
            ack = self.jobs.inspect_directory_application(self.cycle_id, record['id'])
            self.assertEqual(ack['kind'], 'directory_application_inspect')
            self.wait()
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id']), before)
        self.assertIn('error', self.jobs.check_directory_application_session('stale-task'))

    def test_native_normal_close_positive_result_allows_new_review_but_not_replayed_old_nonce(self):
        record = self.review(self.save())

        async def normal_close(*args, **options):
            options['progress']({'stage': 'awaiting_human_confirmation', 'window_open': True})
            return {'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0}

        with patch('services.geo_directory_browser.open_session', side_effect=normal_close):
            self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
            self.wait()
        closed = directory.get_application(self.database, self.cycle_id, record['id'])
        self.assertEqual(closed['stage'], 'closed_not_submitted')
        self.assertIn('error', self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce']))
        self.assertTrue(self.review(closed)['can_open'])

    def test_controlled_close_uses_only_owned_window_signal_and_ack_does_not_reset_state(self):
        record = self.review(self.save())
        entered = threading.Event()

        async def controlled(*args, **options):
            options['progress']({'stage': 'awaiting_human_confirmation', 'window_open': True})
            entered.set()
            await options['close_requested'].wait()
            return {'normal_close_confirmed': True, 'route_guard_installed': True, 'allowed_posts': 0, 'blocked_writes': 0}

        with patch('services.geo_directory_browser.open_session', side_effect=controlled):
            ack = self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
            self.assertTrue(entered.wait(timeout=3))
            self.assertIn('error', self.jobs.close_directory_application_session('another-task'))
            requested = self.jobs.close_directory_application_session(ack['id'])
            self.assertEqual(requested, {'id': ack['id'], 'status': 'running', 'close_requested': True})
            self.assertNotIn('stage', requested)
            self.wait()
        closed = directory.get_application(self.database, self.cycle_id, record['id'])
        self.assertEqual(closed['stage'], 'closed_not_submitted')
        self.assertIn('error', self.jobs.close_directory_application_session(ack['id']))

    def test_generic_stop_is_unknown_even_with_zero_submission_intent(self):
        record = self.review(self.save())
        entered = threading.Event()

        async def waiting(*args, **options):
            options['progress']({'stage': 'awaiting_human_confirmation', 'window_open': True})
            entered.set()
            await asyncio.Event().wait()

        with patch('services.geo_directory_browser.open_session', side_effect=waiting):
            self.jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], record['open_nonce'])
            self.assertTrue(entered.wait(timeout=3))
            self.jobs.stop_task()
            self.assertEqual(self.wait()['status'], 'stopped')
        stopped = directory.get_application(self.database, self.cycle_id, record['id'])
        self.assertEqual(stopped['stage'], 'submission_unknown')
        self.assertFalse(stopped['native_submit_attempted'])
        self.assertFalse(stopped['can_review'])


if __name__ == '__main__':
    unittest.main()
