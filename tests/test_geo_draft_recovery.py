"""A completed editorial reply can be reviewed and saved without another send."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from services import geo_content_research as research, geo_content_store as store, geo_draft_recovery as recovery
from services.article_service import get_article
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_content_loop import ResearchFixture, draft


def supplied_facts():
    return [{'text': '产品公开介绍页为 https://example.org/about，实际服务入口为 https://service.example.org/，二者用途不同。',
             'source_url': 'https://example.org/about', 'confirmed': True}]


class DraftURLTests(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = store.save_facts(self.database, self.cycle['id'], supplied_facts(), self.cycle['revision'])
        self.inputs = research.draft_input(self.cycle, 'b01', 'zhihu')

    def raw(self, url):
        value = json.loads(draft())
        value['content'] += '\n实际服务入口：' + url
        return json.dumps(value, ensure_ascii=False)

    def test_exact_literal_service_url_in_confirmed_fact_text_is_allowed(self):
        value = research.parse_draft(self.raw('https://service.example.org/'), self.inputs)
        self.assertIn('https://service.example.org/', value['content'])

    def test_original_source_url_with_prose_punctuation_stays_allowed(self):
        research.parse_draft(self.raw('https://example.org/about。'), self.inputs)

    def test_other_hosts_paths_queries_fragments_and_url_suffixes_are_not_authorized(self):
        for url in ('https://service.example.org.evil.test/', 'https://evil.test/',
                    'https://service.example.org/other', 'https://service.example.org/?key=other',
                    'https://service.example.org/#other', 'https://service.example.org/，unconfirmed',
                    'http://service.example.org/', 'https://user@service.example.org/',
                    'https://service.example.org\\@evil.test/'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, '网址'):
                research.parse_draft(self.raw(url), self.inputs)

    def test_profile_brief_and_unconfirmed_fact_text_do_not_authorize_links(self):
        value = copy.deepcopy(self.inputs)
        value['context']['website'] = 'https://not-confirmed.example/'
        value['brief']['why'] += ' https://not-confirmed.example/'
        value['facts'].append({'id': 'f02', 'confirmed': False, 'text': 'https://not-confirmed.example/', 'source_url': ''})
        with self.assertRaisesRegex(ValueError, '网址'):
            research.parse_draft(self.raw('https://not-confirmed.example/'), value)

    def test_real_newlines_and_literal_backslashes_are_not_silently_decoded(self):
        value = json.loads(self.raw('https://service.example.org/'))
        value['content'] += '\n代码示例保留：C:\\new\\test 与字符 \\n。'
        result = research.parse_draft(json.dumps(value, ensure_ascii=False), self.inputs)
        self.assertEqual(result['content'], value['content'])


class DraftRecoveryTests(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = store.save_facts(self.database, self.cycle['id'], supplied_facts(), self.cycle['revision'])
        self.task_id = 'a' * 32
        self.path = self.root / 'geo-content/evidence' / ('b' * 32) / '20261101-120000-1234abcd/result.json'
        self.path.parent.mkdir(parents=True)
        self.task_path = self.root / 'desktop-jobs' / (self.task_id + '.json')
        self.task_path.parent.mkdir()
        self.raw = json.loads(draft())
        self.raw['content'] += '\n服务入口：https://service.example.org/'
        self.evidence = {'platform': 'doubao', 'client': 'web', 'collection_status': 'success',
            'question': research.draft_prompt(self.cycle, 'b01', 'zhihu'),
            'response': json.dumps(self.raw, ensure_ascii=False), 'question_submission_attempted': True,
            'question_submitted': True, 'answer_completed': True,
            'environment': {'client_mode': 'work', 'model_label': 'synthetic-model'}}
        self.task = {'id': self.task_id, 'kind': 'draft', 'status': 'failed',
                     'cycle_id': self.cycle['id'], 'evidence_path': str(self.path)}
        self.write_evidence()
        self.write_task()

    def write_evidence(self):
        self.path.write_text(json.dumps(self.evidence, ensure_ascii=False), encoding='utf-8')

    def write_task(self):
        self.task_path.write_text(json.dumps(self.task), encoding='utf-8')

    def preview(self):
        return recovery.preview(self.root, self.task_id, self.cycle['id'])

    def article_count(self):
        with sqlite3.connect(self.database) as conn:
            return conn.execute('SELECT count(*) FROM articles').fetchone()[0]

    def test_preview_is_read_only_and_save_retains_original_ai_lineage_without_resending(self):
        original = (self.path.read_bytes(), self.task_path.read_bytes())
        with patch('services.desktop_geo_jobs.collect_doubao_sources', side_effect=AssertionError('must not send')):
            view = self.preview()
            self.assertTrue(view['available'])
            self.assertFalse(view['already_saved'])
            self.assertEqual((view['brief_id'], view['platform']), ('b01', 'zhihu'))
            self.assertEqual(view['draft']['content'], self.raw['content'])
            self.assertEqual(self.article_count(), 0)
            saved = recovery.recover(self.root, self.task_id, self.cycle['id'], view['preview_sha256'])
        self.assertTrue(saved['reused_existing_response'])
        self.assertFalse(saved['already_saved'])
        article = get_article(self.database, saved['article_id'])
        self.assertEqual(article['content'], self.raw['content'])
        self.assertEqual(article['status'], 'draft')
        lineage = store.article_research(self.database, article['id'])['provenance']
        self.assertEqual(lineage['method'], 'doubao_web_ai')
        self.assertEqual(lineage['role'], 'content_editing_not_exposure_test')
        self.assertEqual(lineage['recovered_from_task_id'], self.task_id)
        self.assertEqual(lineage['response_sha256'], hashlib.sha256(self.evidence['response'].encode()).hexdigest())
        self.assertEqual(lineage['prompt_sha256'], research.fingerprint(self.evidence['question']))
        self.assertEqual((self.path.read_bytes(), self.task_path.read_bytes()), original)

    def test_double_save_is_idempotent_and_preview_reports_existing_article(self):
        first_view = self.preview()
        first = recovery.recover(self.root, self.task_id, self.cycle['id'], first_view['preview_sha256'])
        second = recovery.recover(self.root, self.task_id, self.cycle['id'], first_view['preview_sha256'])
        self.assertEqual(first['article_id'], second['article_id'])
        self.assertTrue(second['already_saved'])
        self.assertEqual(self.article_count(), 1)
        view = self.preview()
        self.assertTrue(view['already_saved'])
        self.assertEqual(view['article_id'], first['article_id'])

    def test_wrong_task_or_cycle_running_or_analysis_tasks_cannot_be_recovered(self):
        for patching in ({'cycle_id': 'foreign'}, {'kind': 'research'}, {'status': 'running'}, {'id': 'other'}):
            with self.subTest(patching=patching):
                original = self.task.copy()
                self.task.update(patching);self.write_task()
                self.assertFalse(self.preview()['available'])
                self.task = original;self.write_task()
        self.assertFalse(recovery.preview(self.root, '../task', self.cycle['id'])['available'])
        self.assertFalse(recovery.preview(self.root, self.task_id, '../cycle')['available'])

    def test_no_complete_submitted_reply_is_not_a_draft(self):
        for patching in ({'answer_completed': False}, {'question_submitted': False},
                         {'question_submission_attempted': False}, {'collection_status': 'failed'}, {'response': 'bad json'}):
            with self.subTest(patching=patching):
                original = self.evidence.copy()
                self.evidence.update(patching);self.write_evidence()
                self.assertFalse(self.preview()['available'])
                self.evidence = original;self.write_evidence()

    def test_controlled_path_and_regular_file_guards(self):
        for path in (self.root/'outside.json', self.root/'geo-tests/result.json',
                     self.root/'geo-content/evidence'/('b'*32)/'wrong/result.json',
                     self.root/'geo-content/evidence'/('b'*32)/'20261101-120000-1234abcd/other.json'):
            with self.subTest(path=str(path)):
                self.task['evidence_path'] = str(path);self.write_task()
                self.assertFalse(self.preview()['available'])
        self.task['evidence_path'] = str(self.path);self.write_task()
        with patch.object(recovery.stat, 'S_ISLNK', return_value=True):
            self.assertFalse(self.preview()['available'])

    def test_changed_current_facts_or_brief_cannot_be_matched_to_an_old_reply(self):
        changed = supplied_facts();changed[0]['text'] += ' 当前资料已更新。'
        store.save_facts(self.database, self.cycle['id'], changed, self.cycle['revision'])
        self.assertFalse(self.preview()['available'])
        self.assertEqual(self.article_count(), 0)

    def test_modified_evidence_or_task_after_preview_requires_new_review(self):
        view = self.preview()
        self.evidence['response'] += ' '
        self.write_evidence()
        with self.assertRaisesRegex(ValueError, '预览后'):
            recovery.recover(self.root, self.task_id, self.cycle['id'], view['preview_sha256'])
        self.assertEqual(self.article_count(), 0)

    def test_invalid_preview_digest_and_later_cycle_revision_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '预览'):
            recovery.recover(self.root, self.task_id, self.cycle['id'], '')
        view = self.preview()
        with sqlite3.connect(self.database) as conn, conn:
            conn.execute('UPDATE geo_content_cycles SET revision=revision+1 WHERE id=?', (self.cycle['id'],))
        with self.assertRaisesRegex(ValueError, '预览后'):
            recovery.recover(self.root, self.task_id, self.cycle['id'], view['preview_sha256'])
        self.assertEqual(self.article_count(), 0)

    def test_nonunique_original_prompt_is_blocked(self):
        with patch.object(research, 'draft_prompt', return_value=self.evidence['question']):
            self.assertFalse(self.preview()['available'])

    def test_bridge_preserves_original_task_and_rejects_other_active_work(self):
        bridge = DesktopGeoJobs(self.root)
        original_task = self.task_path.read_bytes()
        view = bridge.preview_research_draft(self.task_id, self.cycle['id'])
        self.assertTrue(view['available'])
        bridge.job = {'status': 'running'}
        self.assertFalse(bridge.preview_research_draft(self.task_id, self.cycle['id'])['available'])
        self.assertIn('error', bridge.recover_research_draft(self.task_id, self.cycle['id'], view['preview_sha256']))
        bridge.job = {'status': 'idle'}
        result = bridge.recover_research_draft(self.task_id, self.cycle['id'], view['preview_sha256'])
        self.assertTrue(result['reused_existing_response'])
        self.assertEqual(self.task_path.read_bytes(), original_task)


if __name__ == '__main__':
    unittest.main()
