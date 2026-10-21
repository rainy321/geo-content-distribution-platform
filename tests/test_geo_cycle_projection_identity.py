"""Assessment views must not duplicate an existing editorial cycle's raw run."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services import geo_content_store as store
from services.geo_test_reports import project_report_evaluation
from services.project_service import create_project
from tests.test_geo_content_loop import report, facts, analysis, draft


class CycleProjectionIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = initialize_database(Path(directory.name)/'database.db')
        self.original = report()
        self.original['evaluation_version'] = '1.4'
        self.original['cases'][1].update(client_mode='work', response='我先在企业内部资料中检索合成样例。')
        self.reference = 'synthetic-saved-report'
        self.cycle = store.create_cycle(self.database, self.original, self.reference)
        self.project_id = self.cycle['project_id']

    def stored_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return tuple(connection.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                         for table in ('geo_content_cycles', 'geo_content_drafts', 'articles'))

    def test_upgrade_and_repeated_projection_reuse_existing_cycle_and_lineage_without_writes(self):
        cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        cycle = store.save_analysis(self.database, cycle['id'], analysis(), {'method':'synthetic'}, cycle['revision'])
        store.save_draft(self.database, cycle['id'], 'b01', 'zhihu', draft(), {'method':'synthetic'}, cycle['revision'])
        cycle = store.get_cycle(self.database, cycle['id'])
        before = self.stored_rows()
        view = project_report_evaluation(self.original)
        self.assertNotEqual(store.fingerprint(view), cycle['report_digest'])
        self.assertEqual(view['evaluation_version'], '1.5')
        self.assertEqual(view['cases'][1]['assessment']['answer_error'], 'private_account_context')
        for projected in (view, project_report_evaluation(view), project_report_evaluation(project_report_evaluation(view))):
            with self.subTest(stored_version=projected['evaluation_projection']['stored_evaluation_version']):
                reused = store.create_cycle(self.database, projected, self.reference, self.project_id)
                for key in ('id','project_id','report_digest','report_ref','revision','created_at','updated_at',
                            'facts','analysis','drafts','test_plan'):
                    self.assertEqual(reused[key], cycle[key], key)
                self.assertEqual(self.stored_rows(), before)
        self.assertEqual(len(store.list_cycles(self.database)), 1)

    def test_same_run_and_plan_with_changed_raw_answer_must_not_merge(self):
        changed = project_report_evaluation(self.original)
        changed['cases'][1]['response'] += '这是不同的合成原始回答。'
        result = store.create_cycle(self.database, changed, self.reference, self.project_id)
        self.assertNotEqual(result['id'], self.cycle['id'])
        self.assertEqual(len(store.list_cycles(self.database)), 2)

    def test_same_run_and_plan_with_changed_source_must_not_merge(self):
        changed = project_report_evaluation(self.original)
        # Sources in an excluded private case are still raw evidence. Their
        # difference must not disappear merely because writing filters them.
        changed['cases'][1]['citations'] = [{'title':'different synthetic source', 'url':'https://public.example.org/changed'}]
        result = store.create_cycle(self.database, changed, self.reference, self.project_id)
        self.assertNotEqual(result['id'], self.cycle['id'])
        self.assertEqual(len(store.list_cycles(self.database)), 2)

    def test_same_run_and_evidence_in_another_project_stay_isolated(self):
        profile = self.original['profile']
        project = create_project(self.database, name=profile['name'], website=profile['website'],
                                 product=profile['product'], industry=profile['industry'],
                                 description=profile['description'], keywords=profile['keywords'],
                                 competitors=profile['competitors'])
        view = project_report_evaluation(self.original)
        other = store.create_cycle(self.database, view, self.reference, project['id'])
        self.assertNotEqual(other['id'], self.cycle['id'])
        self.assertEqual(other['project_id'], project['id'])
        self.assertNotEqual(other['project_id'], self.project_id)
        again = store.create_cycle(self.database, project_report_evaluation(view), self.reference, project['id'])
        self.assertEqual(again['id'], other['id'])
        self.assertEqual(len(store.list_cycles(self.database)), 2)

    def test_same_evidence_with_a_different_saved_report_reference_is_not_a_projection_match(self):
        result = store.create_cycle(self.database, project_report_evaluation(self.original),
                                    'different-synthetic-report', self.project_id)
        self.assertNotEqual(result['id'], self.cycle['id'])

    def test_same_evidence_with_a_different_run_id_is_not_a_projection_match(self):
        changed = project_report_evaluation(self.original)
        changed['run_id'] = 'different-synthetic-run'
        result = store.create_cycle(self.database, changed, self.reference, self.project_id)
        self.assertNotEqual(result['id'], self.cycle['id'])


if __name__ == '__main__':
    unittest.main()
