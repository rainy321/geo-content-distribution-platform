import copy
import sqlite3
import unittest
from contextlib import closing

from services import geo_destination_service as destinations, geo_content_store as store
from tests.test_geo_content_loop import ResearchFixture, draft, facts


class CsdnDestinationTests(ResearchFixture, unittest.TestCase):
    def ready_article(self, editor='https://editor.csdn.net/md/'):
        config=copy.deepcopy(next(row[3] for row in destinations.CATALOG if row[0]=='csdn'))
        config['editor_url']=editor
        target=destinations.save_target(self.database,self.cycle['project_id'],config)
        cycle=store.get_cycle(self.database,self.cycle['id'])
        cycle=store.save_facts(self.database,cycle['id'],facts(),cycle['revision'])
        saved=store.save_draft(self.database,cycle['id'],'b01',target['key'],draft(),{'method':'synthetic_fixture'},cycle['revision'])
        with closing(sqlite3.connect(self.database)) as conn,conn:
            conn.execute("UPDATE articles SET status='ready' WHERE id=?",(saved['article_id'],))
        return target,saved['article_id']

    def test_known_recipe_reserves_reviewed_fill_without_user_css(self):
        target,article=self.ready_article()
        self.assertEqual(target['capability'],'platform_assisted_fill')
        self.assertFalse(target['config']['title_selector'])
        self.assertFalse(target['auto_publish_verified'])
        self.assertFalse(target['builtin_publisher']['available'])
        _,_,event=destinations.reserve_fill(self.database,self.cycle['project_id'],target['id'],article)
        self.assertTrue(event)
        self.assertEqual(store.get_cycle(self.database,self.cycle['id'])['publications'],[])

    def test_similar_host_never_receives_the_fixed_recipe(self):
        target,article=self.ready_article('https://editor.csdn.net.evil.example/md/')
        self.assertEqual(target['capability'],'login_and_export')
        with self.assertRaisesRegex(ValueError,'尚未配置'):
            destinations.reserve_fill(self.database,self.cycle['project_id'],target['id'],article)
        self.assertEqual(destinations.get_target(self.database,self.cycle['project_id'],target['id'])['events'],[])
