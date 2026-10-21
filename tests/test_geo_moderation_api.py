"""HTTP moderation reporting uses temporary customer data, never platform accounts."""
import unittest
import uuid
from unittest.mock import patch

from services import geo_content_store as store
from tests import test_geo_content_feedback as feedback
from tests.test_geo_content_loop import ResearchFixture


class TestModerationAPI(ResearchFixture, unittest.TestCase):
    prepare_article = feedback.TestPublicationFeedback.prepare_article
    payload = feedback.TestPublicationFeedback.payload

    def setUp(self):
        super().setUp()
        from sau_backend import app
        self.app=app
        self.configuration=patch.dict(app.config,DATABASE_PATH=self.database,ACCESS_CONTROL_ENABLED=False,TESTING=True)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.client=app.test_client()
        self.prepare_article()
        self.endpoint='/api/geo-content/cycles/'+self.cycle['id']+'/moderation-observations'

    def observation(self, **changes):
        article=next(item for item in store.get_cycle(self.database,self.cycle['id'])['drafts'] if item['article_id']==self.article_id)
        return {'observation_id':uuid.uuid4().hex,'article_id':self.article_id,'target_id':self.target['id'],
            'state':'rejected','confirmed':True,'note':'合成平台观察：审核未通过',
            'expected_article_sha256':article['article_sha256'],**changes}

    def test_rejection_is_reported_without_publishing_and_blocks_false_public_link(self):
        result=self.client.post(self.endpoint,json=self.observation())
        self.assertEqual(result.status_code,200)
        self.assertIn('运营方观察',result.json['msg'])
        self.assertEqual(result.json['data']['publications'],[])
        self.assertEqual(result.json['data']['moderation_observations'][0]['source'],'operator_reported')
        registration=self.client.post(self.endpoint.replace('/moderation-observations','/publications'),json=self.payload())
        self.assertEqual(registration.status_code,400)
        self.assertIn('待审核或审核未通过',registration.json['msg'])

    def test_prefix_and_idempotent_retry_keep_one_observation(self):
        value=self.observation()
        for _ in range(2):
            result=self.client.post('/backend'+self.endpoint,json=value)
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(result.json['data']['moderation_observations']),1)
        conflict=self.client.post(self.endpoint,json={**value,'note':'changed replay'})
        self.assertEqual(conflict.status_code,400)

    def test_public_content_requires_current_content_and_a_concrete_platform_link(self):
        for changes in [dict(state='publicly_visible'),dict(state='publicly_visible',url=self.payload()['url']),
            dict(state='publicly_visible',url='https://elsewhere.example.org/post/1',content_confirmed=True),
            dict(expected_article_sha256='0'*64)]:
            self.assertEqual(self.client.post(self.endpoint,json=self.observation(**changes)).status_code,400)
        current=self.client.get('/api/geo-content/cycles/'+self.cycle['id']).json['data']
        self.assertEqual(current['moderation_observations'],[])

    def test_malformed_missing_cycle_and_access_control_remain_separate(self):
        self.assertEqual(self.client.post(self.endpoint,json=[]).status_code,400)
        self.assertEqual(self.client.post(self.endpoint.replace(self.cycle['id'],'missing'),json=self.observation()).status_code,404)
        with patch.dict(self.app.config,ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.post(self.endpoint,json=self.observation()).status_code,401)


if __name__=='__main__':unittest.main()
