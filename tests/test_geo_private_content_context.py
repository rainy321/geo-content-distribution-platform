import copy
import json
import unittest
import tempfile
from pathlib import Path

from services.geo_content_research import research_observations, research_prompt, parse_research
from tests.test_geo_content_loop import report
from services.desktop_geo_jobs import DesktopGeoJobs
from services.desktop_task_store import DesktopTaskStore


class PrivateContentContextTests(unittest.TestCase):
    def baseline(self):
        value = report()
        value['evaluation_version'] = '1.3'
        value['cases'][1].update(client_mode='work', response='内部飞书工作记录显示：SYNTHETIC_PRIVATE_MARKER。',
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
