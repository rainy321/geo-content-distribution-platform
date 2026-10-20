import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class ProjectsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_creates_reads_and_lists_project(self):
        response = self.client.post(
            "/api/projects",
            json={
                "name": " XX科技 ",
                "website": " https://example.test ",
                "product": "企业 AI Agent",
                "industry": "人工智能",
                "description": "面向企业提供智能服务。",
                "keywords": [" AI Agent ", "AI Agent", "企业智能体"],
                "competitors": ["品牌A", "品牌A", "品牌B"],
            },
        )

        self.assertEqual(response.status_code, 201)
        project = response.get_json()["data"]
        self.assertEqual(project["name"], "XX科技")
        self.assertEqual(project["website"], "https://example.test")
        self.assertEqual(project["keywords"], ["AI Agent", "企业智能体"])
        self.assertEqual(project["competitors"], ["品牌A", "品牌B"])
        self.assertEqual({key:project[key] for key in ('entity_type','company','audience','aliases','official_urls')},
                         {'entity_type':'product','company':'','audience':'','aliases':[],'official_urls':[]})

        detail = self.client.get(f"/api/projects/{project['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["article_count"], 0)

        listing = self.client.get("/api/projects")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.get_json()["data"]], [project["id"]])

    def test_updates_only_supplied_fields(self):
        project = self._create_project()

        response = self.client.put(
            f"/api/projects/{project['id']}",
            json={
                "product": "企业智能体平台",
                "keywords": ["企业智能体", "大模型应用"],
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["data"]
        self.assertEqual(updated["name"], "XX科技")
        self.assertEqual(updated["website"], "https://example.test")
        self.assertEqual(updated["product"], "企业智能体平台")
        self.assertEqual(updated["keywords"], ["企业智能体", "大模型应用"])

    def test_list_includes_article_count(self):
        project = self._create_project()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project["id"], "测试文章", "正文"),
                )

        listing = self.client.get("/api/projects").get_json()["data"]
        self.assertEqual(listing[0]["article_count"], 1)

        updated = self.client.put(
            f"/api/projects/{project['id']}",
            json={"description": "更新后的品牌介绍"},
        ).get_json()["data"]
        self.assertEqual(updated["article_count"], 1)

    def test_deletes_empty_project(self):
        project = self._create_project()

        response = self.client.delete(f"/api/projects/{project['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], {"id": project["id"]})
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}").status_code, 404)

    def test_refuses_to_delete_project_with_articles(self):
        project = self._create_project()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project["id"], "测试文章", "正文"),
                ).lastrowid

        response = self.client.delete(f"/api/projects/{project['id']}")

        self.assertEqual(response.status_code, 409)
        with closing(sqlite3.connect(self.db_path)) as conn:
            project_count = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()[0]
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()[0]
        self.assertEqual((project_count, article_count), (1, 1))

    def test_validates_fields_and_unknown_projects(self):
        invalid_requests = [
            self.client.post("/api/projects", json={}),
            self.client.post(
                "/api/projects",
                json={"name": "XX科技", "keywords": "AI Agent"},
            ),
            self.client.put("/api/projects/999999", json={"name": "新名称"}),
            self.client.put("/api/projects/1", json={}),
            self.client.delete("/api/projects/999999"),
        ]

        self.assertEqual(
            [response.status_code for response in invalid_requests],
            [400, 400, 404, 400, 404],
        )

    def test_company_product_identity_roundtrip_and_partial_update_preserve_new_fields(self):
        payload={'name':'合成模型网关','website':'https://product.example.org:8443/start/',
            'entity_type':'product','company':'合成科技有限公司','audience':'开发团队',
            'aliases':[' Gateway ', 'Gateway', '网关产品'],
            'official_urls':['https://product.example.org:8443/start/', 'http://docs.example.org:8080/guide/']}
        response=self.client.post('/api/projects',json=payload)
        self.assertEqual(response.status_code,201,response.json)
        project=response.json['data'];identifier=project['id']
        self.assertEqual(project['aliases'],['Gateway','网关产品'])
        self.assertEqual(project['official_urls'],payload['official_urls'])
        updated=self.client.put(f'/api/projects/{identifier}',json={'description':'只更新说明'}).json['data']
        for key in ('entity_type','company','audience','aliases','official_urls','website'):
            self.assertEqual(updated[key],project[key])
        self.assertEqual(self.client.get(f'/api/projects/{identifier}').json['data'],updated)
        self.assertEqual(self.client.get('/api/projects').json['data'][0],updated)
        company=self.client.post('/api/projects',json={'name':'合成科技有限公司','entity_type':'company',
            'company':'合成科技有限公司','aliases':['合成科技有限公司'],'official_urls':['https://company.example.org/']})
        self.assertEqual(company.status_code,201,company.json)
        self.assertEqual(company.json['data']['entity_type'],'company')

    def test_invalid_identity_arrays_types_and_limits_return_field_errors(self):
        for change,field in (({'entity_type':'brand'},'entity_type'),({'company':None},'company'),
            ({'audience':[]},'audience'),({'aliases':'alias'},'aliases'),({'aliases':[1]},'aliases'),
            ({'aliases':['']},'aliases'),({'aliases':['a']*21},'aliases'),({'aliases':[{'text':'x'}]},'aliases'),
            ({'official_urls':None},'official_urls'),({'official_urls':[False]},'official_urls'),
            ({'official_urls':['https://example.org/']*11},'official_urls')):
            with self.subTest(change=change):
                response=self.client.post('/api/projects',json={'name':'合成项目',**change})
                self.assertEqual(response.status_code,400,response.json)
                self.assertIn(field,response.json['msg'])

    def test_official_urls_and_website_reject_credentials_query_fragment_and_bad_port(self):
        invalid=('https://user:secret@example.org/','https://@example.org/','https://example.org/?k=v',
            'https://example.org/#part','https://example.org/?','https://example.org/#',
            'file:///tmp/a','example.org','https://example.org:0/','https://example.org:65536/',
            'https://example.org:bad/','https://example.org:/','https://exa mple.org/','https://example.org\\other')
        for field in ('website','official_urls'):
            for url in invalid:
                with self.subTest(field=field,url=url):
                    response=self.client.post('/api/projects',json={'name':'合成项目',field:[url] if field=='official_urls' else url})
                    self.assertEqual(response.status_code,400,response.json)
                    self.assertIn(field,response.json['msg'])

    def test_product_company_alias_rejection_uses_merged_partial_update(self):
        response=self.client.post('/api/projects',json={'name':'合成产品','company':'合成科技有限公司','aliases':['合成科技有限公司']})
        self.assertEqual(response.status_code,400)
        self.assertIn('aliases',response.json['msg'])
        project=self.client.post('/api/projects',json={'name':'合成公司','entity_type':'company',
            'company':'合成科技有限公司','aliases':['合成科技有限公司']}).json['data']
        rejected=self.client.put(f'/api/projects/{project["id"]}',json={'entity_type':'product'})
        self.assertEqual(rejected.status_code,400)
        self.assertEqual(self.client.get(f'/api/projects/{project["id"]}').json['data']['entity_type'],'company')

    def test_project_urls_reuse_canonical_scope_encoding_and_path_validation(self):
        invalid_paths=('/invalid/%','/invalid/%GG','/products%2Fdongai/', '/products%5Cdongai/',
            '/a/../b/','/a/./b/','/a/%2e%2e/b/','/a/%252e%252e/b/','/a/%C0%AF/','/a/%00/')
        for field in ('website','official_urls'):
            for path in invalid_paths:
                with self.subTest(field=field,path=path):
                    url='https://scope.example.org'+path
                    response=self.client.post('/api/projects',json={'name':'合成范围项目',field:[url] if field=='official_urls' else url})
                    self.assertEqual(response.status_code,400,response.json)
                    self.assertIn(field,response.json['msg'])

    def test_canonical_scope_preserves_exact_scheme_host_and_nondefault_ports(self):
        from services.geo_test_service import canonical_entity_url
        urls=['https://scope.example.org/products/item/',
            'https://scope.example.org:443/products/item/',
            'https://scope.example.org:8443/products/item/',
            'http://scope.example.org:8080/products/item/',
            'https://www.scope.example.org/products/item/',
            'https://scope.example.org:65535/%E4%B8%AD%E6%96%87/']
        response=self.client.post('/api/projects',json={'name':'合成端口项目','website':urls[2],'official_urls':urls})
        self.assertEqual(response.status_code,201,response.json)
        self.assertEqual(response.json['data']['official_urls'],urls)
        self.assertEqual(response.json['data']['website'],urls[2])
        identities=list(map(canonical_entity_url,urls))
        self.assertEqual(identities[0],identities[1])
        self.assertNotEqual(identities[0],identities[2])
        self.assertNotEqual(identities[0],identities[3])
        self.assertNotEqual(identities[0],identities[4])
        self.assertEqual(identities[5][2:],(65535,'/中文'))

    def test_identity_field_lengths_match_planning_core_boundaries(self):
        base='https://length.example.org/'
        url=lambda maximum:base+'a'*(maximum-len(base))
        payload={'name':'合成边界产品','description':'合成业务说明','entity_type':'product',
            'company':'司'*300,'audience':'客'*300,'aliases':['别'*120],
            'website':url(300),'official_urls':[url(2000)]}
        response=self.client.post('/api/projects',json=payload)
        self.assertEqual(response.status_code,201,response.json)
        project=response.json['data']
        from services.geo_test_service import normalize_profile
        normalized=normalize_profile(project)
        for field in ('company','audience','aliases','website','official_urls'):
            self.assertEqual(normalized[field],project[field])
        for field,value in (('company','司'*301),('audience','客'*301),
                            ('aliases',['别'*121]),('website',url(301)),('official_urls',[url(2001)])):
            with self.subTest(field=field):
                rejected=self.client.put(f'/api/projects/{project["id"]}',json={field:value})
                self.assertEqual(rejected.status_code,400,rejected.json)
                self.assertIn(field,rejected.json['msg'])
        unchanged=self.client.get(f'/api/projects/{project["id"]}').json['data']
        for field in ('company','audience','aliases','website','official_urls'):
            self.assertEqual(unchanged[field],project[field])

    def test_service_old_signature_and_explicit_invalid_metadata(self):
        from services.project_service import create_project,update_project
        old={'name':'旧调用','website':'','product':'','industry':'','description':'','keywords':[],'competitors':[]}
        project=create_project(self.db_path,**old)
        self.assertEqual(project['entity_type'],'product')
        with self.assertRaisesRegex(ValueError,'aliases'):
            create_project(self.db_path,**old,aliases=None)
        with self.assertRaisesRegex(ValueError,'company'):
            update_project(self.db_path,project['id'],{'company':False})

    def test_linked_project_cannot_change_entity_but_can_update_same_target_details(self):
        for kind in ('article','cycle','target'):
            with self.subTest(history=kind):
                project=self.client.post('/api/projects',json={'name':'合成历史对象','entity_type':'product',
                    'website':'https://entity.example.org/products/a/','company':'示例运营公司','aliases':['原别名']}).json['data']
                identifier=project['id']
                with closing(sqlite3.connect(self.db_path)) as connection,connection:
                    if kind=='article':
                        connection.execute("INSERT INTO articles(project_id,title,content) VALUES(?,'合成旧稿','原正文')",(identifier,))
                    elif kind=='cycle':
                        connection.execute('''INSERT INTO geo_content_cycles(id,project_id,report_ref,report_digest,baseline,observations,analysis,created_at,updated_at)
                            VALUES(?,?,'synthetic','synthetic','{}','{}','{}','2020-01-01','2020-01-01')''',(f'cycle-{identifier}',identifier))
                    else:
                        connection.execute("INSERT INTO geo_distribution_targets(id,project_id,config,created_at,updated_at) VALUES(?,?,'{}','2020-01-01','2020-01-01')",(f'target-{identifier}',identifier))
                changes=({'name':'另一个对象'},{'entity_type':'company'},
                    {'website':'https://entity.example.org/products/b/'},{'website':'https://entity.example.org/'},
                    {'website':'https://www.entity.example.org/products/a/'},{'website':'http://entity.example.org/products/a/'},
                    {'website':'https://entity.example.org:8443/products/a/'},{'website':''})
                for change in changes:
                    with self.subTest(change=change):
                        response=self.client.put(f'/api/projects/{identifier}',json=change)
                        self.assertEqual(response.status_code,400,response.json)
                        self.assertIn('另建项目',response.json['msg'])
                        unchanged=self.client.get(f'/api/projects/{identifier}').json['data']
                        self.assertEqual({k:unchanged[k] for k in ('name','entity_type','website')},
                                         {k:project[k] for k in ('name','entity_type','website')})
                canonical=self.client.put(f'/api/projects/{identifier}',json={'website':'https://ENTITY.example.org:443/products/a',
                    'company':'另一运营主体资料','aliases':['当前对象的别名'],'audience':'开发团队','description':'更新公开资料'})
                self.assertEqual(canonical.status_code,200,canonical.json)
                self.assertEqual(canonical.json['data']['aliases'],['当前对象的别名'])
                self.assertEqual(canonical.json['data']['company'],'另一运营主体资料')
                table={'article':'articles','cycle':'geo_content_cycles','target':'geo_distribution_targets'}[kind]
                with closing(sqlite3.connect(self.db_path)) as connection:
                    self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table} WHERE project_id=?',(identifier,)).fetchone()[0],1)

    def test_unlinked_new_project_still_allows_identity_changes(self):
        old=self._create_project()
        response=self.client.put(f'/api/projects/{old["id"]}',json={'name':'新的公司对象','entity_type':'company','website':'https://company.example.org/'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['data']['entity_type'],'company')
        self.assertEqual(response.json['data']['name'],'新的公司对象')

    def test_linked_legacy_project_detail_update_preserves_unmodified_old_website(self):
        project=self._create_project()
        with closing(sqlite3.connect(self.db_path)) as connection,connection:
            connection.execute("UPDATE projects SET website='legacy.example.org' WHERE id=?",(project['id'],))
            connection.execute("INSERT INTO articles(project_id,title,content) VALUES(?,'旧稿','旧正文')",(project['id'],))
        response=self.client.put(f'/api/projects/{project["id"]}',json={'description':'仅补充资料','audience':'新受众'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['data']['website'],'legacy.example.org')

    def _create_project(self):
        response = self.client.post(
            "/api/projects",
            json={
                "name": "XX科技",
                "website": "https://example.test",
                "product": "企业 AI Agent",
                "keywords": ["AI Agent"],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["data"]


if __name__ == "__main__":
    unittest.main()
