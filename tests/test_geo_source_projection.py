"""Later authenticated source evidence reaches content research, never a new answer."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.geo_test_reports import list_saved_reports, read_saved_report
from services.geo_test_service import build_report
from tests.test_geo_tests import answer, plan


def digest(value):
    return hashlib.sha256(value).hexdigest()


class SourceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base/'geo-tests'
        self.run = self.root/'desktop-runs/synthetic-run'
        self.original_path = self.run/'evidence/q01/result.json'
        self.original_path.parent.mkdir(parents=True)
        self.test_plan = plan()
        first = self.test_plan['cases'][0]
        self.original = answer(first['question'], response='可比较接口兼容性与公开价格。',
            timestamp='2026-10-19T00:00:00+00:00', conversation_url='https://www.doubao.com/chat/123456',
            environment={'client_mode':'work','model_label':'synthetic-model'},
            citations=[{'title':'官网说明','url':None}], displayed_sources=[])
        self.original_path.write_text(json.dumps(self.original,ensure_ascii=False),encoding='utf-8')
        self.original_sha = digest(self.original_path.read_bytes())
        state = {'run_id':'synthetic-run','status':'stopped','cases':{case['id']:{'status':'pending'} for case in self.test_plan['cases']}}
        state['cases']['q01'] = {'status':'success','result':self.original,'result_path':str(self.original_path)}
        self.report = build_report(self.test_plan,state)
        self.report_path = self.run/'report.json'
        self.report_path.write_text(json.dumps(self.report,ensure_ascii=False),encoding='utf-8')
        self.sidecar = {**copy.deepcopy(self.original),'run_id':'inspect-synthetic','mode':'inspect_existing_answer',
            'timestamp':'2026-10-20T00:00:00+00:00','original_answer_timestamp':self.original['timestamp'],
            'original_result_path':str(self.original_path),'original_result_sha256':self.original_sha,
            'original_response_sha256':digest(self.original['response'].encode()),
            'question_submission_attempted':False,'citations':[{'url':'https://tongyuye.com/about','title':'官网说明'}],
            'visible_citation_count':1,'visible_source_count':0,
            'inspection_provenance':{'kind':'later_read_only_sources_not_new_answer','question_sent':False,
                'original_result_unchanged':True,'question_sha256':digest(self.original['question'].encode()),
                'answer_identity_method':'exact_complete_response','omitted_progress_body_count':0,
                'current_answer_sha256':digest(self.original['response'].encode())}}
        self.sidecar_path = self.root/'source-inspections/inspect-synthetic/result.json'
        self.sidecar_path.parent.mkdir(parents=True)
        self.write_sidecar()

    def write_sidecar(self, value=None):
        self.sidecar_path.write_text(json.dumps(value or self.sidecar,ensure_ascii=False),encoding='utf-8')

    def read(self):
        report_id = list_saved_reports(self.root)[0]['id']
        return read_saved_report(self.root,report_id)

    def test_urls_and_citation_metrics_are_later_observations_with_immutable_answer_and_raw(self):
        paths = [self.report_path,self.original_path,self.sidecar_path]
        before = [(digest(p.read_bytes()),p.stat().st_mtime_ns) for p in paths]
        projected = self.read()
        row = projected['cases'][0]
        for key in ('response','question','collected_at','status','model_label','client_mode'):
            self.assertEqual(row[key],self.report['cases'][0][key])
        for key in ('run_id','updated_at','plan_sha256','profile','status'):
            self.assertEqual(projected[key],self.report[key])
        self.assertFalse(row['assessment']['brand_mentioned'])
        self.assertTrue(row['assessment']['official_citation_present'])
        self.assertEqual(projected['cohorts']['discovery']['official_citation_hits'],1)
        self.assertEqual(row['source_inspected_at'],self.sidecar['timestamp'])
        self.assertTrue(row['source_reinspection']['after_answer'])
        self.assertFalse(row['source_reinspection']['new_answer_collected'])
        self.assertEqual(projected['source_reinspection']['original_report_sha256'],digest(self.report_path.read_bytes()))
        self.assertEqual(projected['source_reinspection']['applied_case_ids'],['q01'])
        self.assertEqual(before,[(digest(p.read_bytes()),p.stat().st_mtime_ns) for p in paths])

    def test_wrong_hash_question_answer_conversation_time_or_failed_sidecars_never_merge(self):
        for change in ({'original_result_sha256':'0'*64},{'question':'其他客户的问题'},
                       {'response':'另一份回答'},{'original_answer_timestamp':'2026-10-18T00:00:00+00:00'},
                       {'conversation_url':'https://www.doubao.com/chat/777777'},
                       {'timestamp':'2026-10-18T00:00:00+00:00'},{'collection_status':'need_action'},
                       {'original_result_path':str(self.base/'outside/result.json')},
                       {'inspection_provenance':{**self.sidecar['inspection_provenance'],'question_sent':True}}):
            with self.subTest(change=change):
                self.write_sidecar({**self.sidecar,**change})
                row = self.read()['cases'][0]
                self.assertNotIn('source_reinspection',row)
                self.assertEqual(row['citations'],self.report['cases'][0]['citations'])
                self.assertIsNone(row['assessment']['official_citation_present'])

    def test_later_failed_attempt_does_not_replace_valid_sidecar_and_unknown_is_not_zero(self):
        failed = {**self.sidecar,'collection_status':'need_action','timestamp':'2026-10-20T01:00:00+00:00'}
        path = self.root/'source-inspections/inspect-failed/result.json'
        path.parent.mkdir()
        path.write_text(json.dumps(failed),encoding='utf-8')
        self.assertEqual(self.read()['cases'][0]['source_reinspection']['inspection_id'],'inspect-synthetic')
        partial = {**self.sidecar,'collection_status':'partial','source_extraction_status':'incomplete',
                   'citations':[{'title':'unknown address','url':None}]}
        self.write_sidecar(partial)
        self.assertIsNone(self.read()['cases'][0]['assessment']['official_citation_present'])

    def test_historical_other_run_inspection_never_enters_current_baseline(self):
        other = self.root/'desktop-runs/old-fixed/evidence/q01/result.json'
        other.parent.mkdir(parents=True)
        other.write_bytes(self.original_path.read_bytes())
        self.write_sidecar({**self.sidecar,'original_result_path':str(other)})
        self.assertNotIn('source_reinspection',self.read()['cases'][0])

    def test_report_api_to_content_cycle_consumes_urls_and_retains_later_source_lineage(self):
        from sau_backend import app
        from db.createTable import initialize_database
        database = self.base/'database.db'
        initialize_database(database)
        with patch.dict(app.config,GEO_TEST_REPORTS_DIRECTORY=self.root,DATABASE_PATH=database,
                        ACCESS_CONTROL_ENABLED=False,TESTING=True):
            client=app.test_client()
            report_id=client.get('/api/geo-tests/reports').json['data'][0]['id']
            response=client.post('/api/geo-content/cycles',json={'report_id':report_id})
        self.assertEqual(response.status_code,201,response.json.get('msg'))
        cycle=response.json['data']
        self.assertEqual(cycle['observations']['sources'][0]['url'],'https://tongyuye.com/about')
        self.assertEqual(cycle['baseline']['cases'][0]['collected_at'],self.original['timestamp'])
        self.assertFalse(cycle['baseline']['source_reinspection']['new_answer_collected'])
        self.assertEqual(cycle['baseline']['cases'][0]['source_reinspection']['original_result_sha256'],self.original_sha)


if __name__=='__main__':
    unittest.main()
