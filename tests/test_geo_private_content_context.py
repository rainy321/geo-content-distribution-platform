import copy
import json
import unittest
import tempfile
import sqlite3
from contextlib import closing
from pathlib import Path

from services.geo_content_research import research_observations, research_prompt, parse_research, draft_input
from services import geo_content_store as content_store, geo_destination_service as destinations
from tests.test_geo_content_loop import report, ResearchFixture
from services.desktop_geo_jobs import DesktopGeoJobs
from services.desktop_task_store import DesktopTaskStore


class PrivateContentContextTests(unittest.TestCase):
    def baseline(self):
        value = report()
        value['evaluation_version'] = '1.4'
        value['cases'][1].update(client_mode='work', response='基于企业内部官方资料检索，结果为SYNTHETIC_PRIVATE_MARKER。',
            citations=[{'title':'SYNTHETIC_PRIVATE_SOURCE', 'url':'https://private.example.org/account-doc'}])
        return value

    def test_new_editorial_observations_exclude_private_text_and_source(self):
        baseline = self.baseline()
        original = copy.deepcopy(baseline)
        value = research_observations(baseline)
        encoded = json.dumps(value)
        self.assertNotIn('SYNTHETIC_PRIVATE', encoded)
        self.assertNotIn('private.example.org', encoded)
        self.assertFalse(next(c for c in value['cases'] if c['id']=='q02')['usable'])
        self.assertEqual(baseline, original)

    def test_native_history_and_workspace_project_private_answers_without_rewriting_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = DesktopTaskStore(root)
            store.save({'id':'synthetic-private-history','kind':'run','status':'completed',
                        'created_at':'2026-10-20T00:00:00Z','result':{'report':self.baseline()}})
            store.save_workspace({'report':self.baseline()})
            job_file = root/'desktop-jobs/synthetic-private-history.json'
            workspace_file = root/'workspace-state.json'
            original = (job_file.read_bytes(),workspace_file.read_bytes())
            jobs = DesktopGeoJobs(root)
            try:
                for value in (jobs.get_task()['result']['report'],jobs.get_workspace_state()['report']):
                    self.assertEqual(value['cases'][1]['assessment']['answer_error'],'private_account_context')
                    cohort = value['cohorts'][value['cases'][1]['category']]
                    self.assertEqual(cohort['answers_evaluable'],cohort['planned']-1)
                self.assertEqual((job_file.read_bytes(),workspace_file.read_bytes()),original)
            finally:
                jobs.shutdown()

    def test_legacy_editorial_prompt_cannot_include_private_context_or_authorize_its_brief(self):
        baseline = self.baseline()
        observed = research_observations(report())
        old_case = next(c for c in observed['cases'] if c['id']=='q02')
        old_case.update(usable=True, response_excerpt='SYNTHETIC_PRIVATE_MARKER')
        observed['sources'].append({'id':'s999','case_id':'q02','title':'SYNTHETIC_PRIVATE_SOURCE','url':'https://private.example.org/'})
        cycle = {'baseline':baseline,'observations':observed,'profile':baseline['profile'],
                 'opportunities':[{'description':'SYNTHETIC_PRIVATE_OPPORTUNITY'}]}
        original = copy.deepcopy(cycle)
        self.assertNotIn('SYNTHETIC_PRIVATE', research_prompt(cycle))
        payload = json.dumps({'diagnosis':'合成计划', 'briefs':[{'case_ids':['q02'],'topic':'合成选题',
            'why':'合成原因','outline':['说明'],'missing_evidence':['公开依据'],'source_ids':['s999'],'priority':'high'}]})
        with self.assertRaises(ValueError):
            parse_research(payload,cycle,{'method':'synthetic'})
        self.assertEqual(cycle,original)


class PrivateLegacyCycleTests(ResearchFixture, unittest.TestCase):
    def test_legacy_cycle_keeps_identity_facts_target_and_bytes_but_projects_private_case(self):
        self.prepare_facts()
        target = destinations.save_target(self.database, self.cycle['project_id'], {
            'name':'合成社区', 'home_url':'https://public.example.org/',
            'login_url':'https://public.example.org/login', 'editor_url':'https://public.example.org/editor',
            'source_scope':'https://public.example.org/articles/', 'style':'核对公开技术依据。'})
        cycle = content_store.get_cycle(self.database, self.cycle['id'])
        baseline = copy.deepcopy(cycle['baseline'])
        baseline.pop('evaluation_projection', None)
        baseline['evaluation_version'] = '1.4'
        baseline['cases'][1].update(client_mode='work', response='内部资料中检索到了SYNTHETIC_PRIVATE_MARKER。',
            citations=[{'title':'SYNTHETIC_PRIVATE_SOURCE', 'url':'https://private.example.org/internal'}])
        observed = copy.deepcopy(cycle['observations'])
        private_case = next(case for case in observed['cases'] if case['id']=='q02')
        private_case.update(usable=True, response_excerpt='SYNTHETIC_PRIVATE_MARKER', source_ids=['s999'])
        observed['sources'].append({'id':'s999','case_id':'q02','kind':'citations',
            'title':'SYNTHETIC_PRIVATE_SOURCE','url':'https://private.example.org/internal',
            'domain':'private.example.org','status':'observed_url'})
        # This is a synthetic pre-1.5 cycle, not a mutation of a user's records.
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE geo_content_cycles SET baseline=?,observations=? WHERE id=?',
                (json.dumps(baseline),json.dumps(observed),cycle['id']))
        database = Path(self.database)
        before = (database.read_bytes(), database.stat().st_mtime_ns)
        projected = content_store.get_cycle(self.database, cycle['id'])
        self.assertEqual((database.read_bytes(),database.stat().st_mtime_ns),before)
        for key in ('id','project_id','report_digest','report_ref','revision','created_at','updated_at','facts','test_plan'):
            self.assertEqual(projected[key],cycle[key],key)
        self.assertEqual(projected['destinations'][0]['id'],target['id'])
        self.assertEqual(projected['baseline']['evaluation_version'],'1.5')
        self.assertEqual(projected['baseline']['cases'][1]['assessment']['answer_error'],'private_account_context')
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(projected['observations']))
        self.assertNotIn('private.example.org',json.dumps(projected['opportunities']))
        self.assertNotIn('SYNTHETIC_PRIVATE',research_prompt(projected))
        private_brief = next(brief for brief in projected['analysis']['briefs'] if brief['case_ids']==['q02'])
        with self.assertRaisesRegex(ValueError,'不再有效'):
            draft_input(projected,private_brief['id'],'zhihu')
        self.assertEqual((database.read_bytes(),database.stat().st_mtime_ns),before)
