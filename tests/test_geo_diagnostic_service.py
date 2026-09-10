import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services.geo_diagnostic_service import (
    CHANNEL_RECOMMENDATION_VERSION,
    GEO_DIAGNOSTIC_VERSION,
    diagnose_geo_readiness,
    get_geo_diagnostic_run,
    list_geo_diagnostic_runs,
)
from services.geo_score_service import GEO_SCORE_VERSION


class GEODiagnosticServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.project_id = conn.execute(
                "INSERT INTO projects (name) VALUES ('Acme Cloud')"
            ).lastrowid
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_project_diagnostic_prioritizes_missing_commercial_foundation(self):
        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
        )

        self.assertEqual(result["diagnostic_version"], GEO_DIAGNOSTIC_VERSION)
        self.assertEqual(result["scope"], "project")
        self.assertEqual(result["grade"], "D")
        self.assertGreaterEqual(result["gap_counts"]["P0"], 3)
        keys = {gap["key"] for gap in result["gaps"]}
        self.assertIn("positioning.product", keys)
        self.assertIn("discoverability.keywords", keys)
        self.assertIn("content.inventory", keys)
        self.assertIn("visibility.baseline", keys)
        self.assertFalse(result["methodology"]["external_model_used"])
        self.assertFalse(result["methodology"]["customer_content_sent_externally"])
        self.assertEqual(
            result["baseline"]["score_basis"],
            "current_rules_recomputed",
        )
        self.assertEqual(result["baseline"]["score_version"], GEO_SCORE_VERSION)
        self.assertNotIn("Acme Cloud", result["input_digest"])
        channel_strategy = result["channel_strategy"]
        self.assertEqual(channel_strategy["version"], CHANNEL_RECOMMENDATION_VERSION)
        self.assertTrue(channel_strategy["baseline_required"])
        self.assertEqual(channel_strategy["evaluated_platform_count"], 10)
        self.assertEqual(
            {item["platform"] for item in channel_strategy["recommendations"]},
            {
                "zhihu", "toutiao", "baijiahao", "sohu", "xiaohongshu",
                "douyin", "kuaishou", "bilibili", "channels", "tiktok",
            },
        )
        self.assertTrue(
            all(item["stage"] == "baseline_then_pilot" for item in channel_strategy["recommendations"])
        )
        self.assertIn("不是平台权重", channel_strategy["claim_boundary"])
        self.assertTrue(
            all("weight" not in item for item in channel_strategy["recommendations"])
        )

        history = list_geo_diagnostic_runs(
            self.database_path, project_id=self.project_id
        )
        self.assertEqual(len(history), 1)
        replay = get_geo_diagnostic_run(self.database_path, result["run_id"])
        self.assertEqual(replay["score"], result["score"])

    def test_content_diagnostic_reuses_geo_score_without_persisting_body(self):
        private_body = "Acme Cloud 很好，但正文非常短。private-customer-text"
        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            title="Acme Cloud",
            content=private_body,
        )

        self.assertEqual(result["scope"], "content")
        self.assertEqual(result["source_type"], "ad_hoc")
        self.assertEqual(
            result["algorithm_versions"]["ai_citeability"],
            "ai-citeability-rules-2.2.0",
        )
        self.assertIn("citeability", result)
        self.assertEqual(len(result["citeability"]["dimensions"]), 7)
        self.assertTrue(any(gap["dimension"] == "structure" for gap in result["gaps"]))
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["readiness_status"], "ready")
        self.assertNotIn(
            "content_preflight_blocked",
            {item["stage"] for item in result["channel_strategy"]["recommendations"]},
        )
        self.assertTrue(result["channel_strategy"]["content_gate"]["publish_ready"])
        self.assertTrue(
            any(
                gap["dimension"] == "citeability"
                and gap["priority"] in {"P0", "P1"}
                for gap in result["gaps"]
            )
        )
        self.assertFalse(
            result["measurement_plan"]["current_status"][
                "success_definition_met"
            ]
        )
        stored = get_geo_diagnostic_run(self.database_path, result["run_id"])
        self.assertNotIn(private_body, json.dumps(stored, ensure_ascii=False))
        self.assertEqual(
            stored["algorithm_versions"]["content_score"],
            GEO_SCORE_VERSION,
        )

    @patch("services.geo_diagnostic_service.score_ai_citeability")
    @patch("services.geo_diagnostic_service.score_geo_content")
    def test_high_legacy_score_does_not_override_failed_citeability_gate(
        self,
        mock_content_score,
        mock_citeability,
    ):
        mock_content_score.return_value = {
            "score": 95,
            "dimensions": {
                "entity": 100,
                "keywords": 100,
                "structure": 100,
                "faq": 100,
                "citation": 100,
            },
            "suggestions": [],
        }
        mock_citeability.return_value = {
            "version": "ai-citeability-rules-test",
            "score": 74,
            "pass_score": 75,
            "passed": False,
            "publish_gate_passed": True,
            "dimensions": {},
            "blocking_issues": [],
            "warnings": [],
            "suggestions": ["补充可追溯来源。"],
        }

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            title="Acme Cloud 完整指南",
            content="Acme Cloud 的完整说明。" * 80,
            persist=False,
        )

        # ``score`` and ``grade`` retain the legacy contract, while the new
        # fields express the stricter publication decision.
        self.assertEqual(result["score"], 95)
        self.assertEqual(result["grade"], "A")
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["readiness_status"], "ready")
        gate_gap = next(
            gap
            for gap in result["gaps"]
            if gap["key"] == "citeability.score_below_threshold"
        )
        self.assertEqual(gate_gap["priority"], "P1")
        self.assertEqual(gate_gap["dimension"], "citeability")
        self.assertGreaterEqual(result["gap_counts"]["P1"], 1)
        plan = result["measurement_plan"]
        self.assertTrue(plan["current_status"]["score_target_met"])
        self.assertTrue(plan["current_status"]["p0_target_met"])
        self.assertFalse(plan["current_status"]["ai_citeability_passed"])
        self.assertFalse(plan["current_status"]["success_definition_met"])
        self.assertTrue(plan["success_definition"]["publish_ready"])
        self.assertEqual(plan["success_definition"]["ai_citeability_score_at_least"], 75)

    @patch("services.geo_diagnostic_service.score_ai_citeability")
    @patch("services.geo_diagnostic_service.score_geo_content")
    def test_passing_citeability_gate_sets_publish_readiness(
        self,
        mock_content_score,
        mock_citeability,
    ):
        mock_content_score.return_value = {
            "score": 90,
            "dimensions": {
                "entity": 100,
                "keywords": 100,
                "structure": 100,
                "faq": 100,
                "citation": 100,
            },
            "suggestions": [],
        }
        mock_citeability.return_value = {
            "version": "ai-citeability-rules-test",
            "score": 90,
            "pass_score": 75,
            "passed": True,
            "publish_gate_passed": True,
            "dimensions": {},
            "blocking_issues": [],
            "warnings": [],
            "suggestions": [],
        }

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            title="Acme Cloud 完整指南",
            content="Acme Cloud 的完整说明。" * 80,
            persist=False,
        )

        self.assertEqual(result["score"], 90)
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["readiness_status"], "ready")
        self.assertTrue(
            result["measurement_plan"]["current_status"][
                "success_definition_met"
            ]
        )

    def test_compact_saved_article_diagnostic_keeps_long_form_checks_not_applicable(self):
        content = (
            "Acme Cloud 用于说明企业服务的真实适用条件和人工复核边界，"
            "本文仅基于已经确认的产品资料，不把未经验证的效果写成事实。\n\n"
            "使用前应核对业务目标、数据权限和服务条款，并记录实际测试结果，"
            "再判断方案是否适合当前场景。"
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            article_id = conn.execute(
                """
                INSERT INTO articles (
                    project_id, title, summary, content, geo_score, geo_analysis
                ) VALUES (?, ?, '', ?, 0, ?)
                """,
                (
                    self.project_id,
                    "Acme Cloud 适合哪些场景？",
                    content,
                    json.dumps(
                        {"length_profile": {"key": "compact", "target_length": 200}},
                        ensure_ascii=False,
                    ),
                ),
            ).lastrowid
            conn.commit()

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            article_id=article_id,
            persist=False,
        )

        faq_dimension = next(item for item in result["dimensions"] if item["key"] == "faq")
        self.assertEqual(result["length_profile"]["key"], "compact")
        self.assertEqual(result["length_profile"]["target_length"], 200)
        self.assertIsNone(faq_dimension["score"])
        self.assertEqual(faq_dimension["status"], "not_applicable")
        self.assertFalse(faq_dimension["applicable"])
        self.assertFalse(any(gap["key"] == "content.faq" for gap in result["gaps"]))
        self.assertFalse(
            any(
                token in suggestion
                for suggestion in result["score_suggestions"]
                for token in ("H2", "H3", "FAQ", "总结", "结语")
            )
        )

    def test_article_diagnostic_uses_only_visible_bound_generation_sources(self):
        source = {
            "title": "公开测试报告",
            "url": "https://docs.acmecloud.cn/report",
            "claim": "Acme Cloud 在公开测试中将处理效率提升 40%",
        }
        content = (
            "Acme Cloud 在公开测试中将处理效率提升 40%，"
            "该结论仅适用于报告所述样本与测试环境。\n\n"
            "来源：https://docs.acmecloud.cn/report"
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            article_id = conn.execute(
                """
                INSERT INTO articles (project_id, title, summary, content)
                VALUES (?, ?, '', ?)
                """,
                (self.project_id, "Acme Cloud 如何提高处理效率？", content),
            ).lastrowid
            run_id = conn.execute(
                """
                INSERT INTO content_generation_runs (
                    request_id, request_fingerprint, project_id,
                    project_snapshot, brief_snapshot, configured_engine,
                    status, result_payload, article_id
                ) VALUES (?, ?, ?, '{}', '{}', 'test-engine', 'succeeded', ?, ?)
                """,
                (
                    "diagnostic-visible-source",
                    "fingerprint-visible-source",
                    self.project_id,
                    json.dumps({"sources": [source]}, ensure_ascii=False),
                    article_id,
                ),
            ).lastrowid
            conn.commit()

        supported = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            article_id=article_id,
            persist=False,
        )

        self.assertTrue(supported["publish_ready"])
        self.assertEqual(
            supported["evidence_source_summary"]["generation_run_id"], run_id
        )
        self.assertEqual(supported["evidence_source_summary"]["provided_count"], 1)
        self.assertEqual(supported["evidence_source_summary"]["matched_count"], 1)
        self.assertEqual(
            supported["citeability"]["signals"]["unsupported_quantified_claims"],
            [],
        )

        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE articles SET content = ? WHERE id = ?",
                (
                    "Acme Cloud 在公开测试中将处理效率提升 40%，"
                    "该结论仅适用于报告所述样本与测试环境。",
                    article_id,
                ),
            )
            conn.commit()

        unsupported = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            article_id=article_id,
            persist=False,
        )

        self.assertFalse(unsupported["publish_ready"])
        self.assertEqual(unsupported["evidence_source_summary"]["provided_count"], 1)
        self.assertEqual(unsupported["evidence_source_summary"]["matched_count"], 0)
        self.assertIn(
            "risk.unsupported_quantified_claim",
            {
                item["code"]
                for item in unsupported["citeability"]["blocking_issues"]
            },
        )

    def test_article_diagnostic_digest_fingerprints_bound_source_summary(self):
        source = {
            "title": "公开测试报告",
            "url": "https://docs.acmecloud.cn/report",
            "claim": "Acme Cloud 在公开测试中将处理效率提升 40%",
        }
        content = (
            "Acme Cloud 在公开测试中将处理效率提升 40%，"
            "适用范围仅限报告所述环境。\n\n"
            "来源：https://docs.acmecloud.cn/report"
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            article_id = conn.execute(
                "INSERT INTO articles (project_id, title, content) VALUES (?, ?, ?)",
                (self.project_id, "Acme Cloud 测试说明", content),
            ).lastrowid
            run_id = conn.execute(
                """
                INSERT INTO content_generation_runs (
                    request_id, request_fingerprint, project_id,
                    project_snapshot, brief_snapshot, configured_engine,
                    status, result_payload, article_id
                ) VALUES (?, ?, ?, '{}', '{}', 'test-engine', 'succeeded', ?, ?)
                """,
                (
                    "diagnostic-source-digest",
                    "fingerprint-source-digest",
                    self.project_id,
                    json.dumps({"sources": [source]}, ensure_ascii=False),
                    article_id,
                ),
            ).lastrowid
            conn.commit()

        first = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            article_id=article_id,
            persist=False,
        )
        changed_source = {
            **source,
            "claim": source["claim"] + "，样本与版本边界以报告正文为准",
        }
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE content_generation_runs SET result_payload = ? WHERE id = ?",
                (
                    json.dumps({"sources": [changed_source]}, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()

        second = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            article_id=article_id,
            persist=False,
        )

        self.assertNotEqual(first["input_digest"], second["input_digest"])
        self.assertNotEqual(
            first["evidence_source_summary"]["matched_sources_sha256"],
            second["evidence_source_summary"]["matched_sources_sha256"],
        )

    def test_project_reports_length_profiles_and_limits_comparison_to_same_band(self):
        analyses = (
            {"length_profile": {"key": "compact", "target_length": 200}},
            {"length_profile": {"key": "standard", "target_length": 1000}},
            {},
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            for index, analysis in enumerate(analyses, start=1):
                conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, content, geo_score, geo_analysis
                    ) VALUES (?, ?, '正文', ?, ?)
                    """,
                    (
                        self.project_id,
                        f"文章 {index}",
                        60 + index,
                        json.dumps(analysis, ensure_ascii=False),
                    ),
                )
            conn.commit()

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        distribution = result["length_profile_distribution"]
        counts = {item["key"]: item["count"] for item in distribution["profiles"]}
        self.assertEqual(distribution["total_articles"], 3)
        self.assertEqual(counts, {"compact": 1, "standard": 1, "unknown": 1})
        self.assertTrue(distribution["mixed_profiles"])
        self.assertIn("只建议比较同一长度档位", distribution["comparison_policy"])

    def test_project_diagnostic_uses_latest_visibility_metrics_and_versions(self):
        visibility_result = {
            "summary": {
                "sample_count": 5,
                "mention_rate": 0.8,
                "owned_citation_rate": 0.4,
                "share_of_voice": 0.7,
                "confidence": {"level": "HIGH"},
            },
            "algorithm_versions": {
                "integration": "ai-visibility-python-port-1.1.0",
                "mention": "mention-python-port-1.1.0",
            },
            "recommendation_details": [],
        }
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                """
                UPDATE projects
                SET product = 'Cloud', industry = 'SaaS',
                    description = ?, website = 'https://acme.example',
                    keywords = '["cloud","automation","agent"]',
                    competitors = '["Rival"]'
                WHERE id = ?
                """,
                ("面向企业的自动化云平台，提供清晰能力边界、实施流程、适用对象和可核验的交付证据。" * 2, self.project_id),
            )
            conn.execute(
                """
                INSERT INTO ai_visibility_runs (
                    project_id, prompt, input_samples_json,
                    coverage_items_json, result_json, algorithm_version
                ) VALUES (?, 'prompt', '[]', '{}', ?, ?)
                """,
                (
                    self.project_id,
                    json.dumps(visibility_result),
                    "ai-visibility-python-port-1.1.0",
                ),
            )
            conn.commit()

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        visibility_dimension = next(
            item for item in result["dimensions"] if item["key"] == "ai_visibility"
        )
        self.assertGreaterEqual(visibility_dimension["score"], 70)
        self.assertEqual(
            result["algorithm_versions"]["visibility_integration"],
            "ai-visibility-python-port-1.1.0",
        )
        self.assertFalse(any(gap["key"] == "visibility.baseline" for gap in result["gaps"]))
        self.assertFalse(result["channel_strategy"]["baseline_required"])
        ranked = result["channel_strategy"]["recommendations"]
        self.assertEqual([item["rank"] for item in ranked], list(range(1, 11)))
        self.assertEqual(ranked, sorted(ranked, key=lambda item: (-item["fit_score"], item["platform"])))
        self.assertIn(ranked[0]["platform"], {"zhihu", "sohu", "toutiao", "baijiahao"})
        self.assertEqual(
            result["algorithm_versions"]["channel_fit"],
            CHANNEL_RECOMMENDATION_VERSION,
        )
        for item in ranked:
            self.assertTrue(item["fit_components"])
            self.assertEqual(
                item["validation"]["target"],
                "相对同口径基线改善；没有基线时先建立基线，不预填虚假提升比例。",
            )
            self.assertIn("不可由本软件保证", item["risks"][0])

    def test_project_digest_ignores_stale_score_but_changes_with_current_content(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            article_id = conn.execute(
                """
                INSERT INTO articles (
                    project_id, title, content, geo_score, geo_analysis
                ) VALUES (?, '诊断证据', '正文', 42, '{"structure": 42}')
                """,
                (self.project_id,),
            ).lastrowid
            conn.commit()

        first = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                """
                UPDATE articles
                SET geo_score = 88, geo_analysis = '{"structure": 88}'
                WHERE id = ?
                """,
                (article_id,),
            )
            conn.commit()
        second = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        self.assertEqual(first["input_digest"], second["input_digest"])
        self.assertEqual(first["score"], second["score"])

        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE articles SET content = ? WHERE id = ?",
                (
                    (
                        "Acme Cloud 提供可核验的企业服务说明，包含适用条件、"
                        "实施机制、数据边界和人工复核要求。\n\n"
                    )
                    * 40,
                    article_id,
                ),
            )
            conn.commit()
        third = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        self.assertNotEqual(second["input_digest"], third["input_digest"])
        self.assertNotEqual(second["score"], third["score"])

    @patch("services.geo_diagnostic_service.score_geo_content")
    def test_project_recompute_uses_saved_length_context(self, mock_score):
        mock_score.return_value = {
            "version": GEO_SCORE_VERSION,
            "score": 37,
        }
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE projects SET keywords = ? WHERE id = ?",
                ('["evidence","automation"]', self.project_id),
            )
            conn.execute(
                """
                INSERT INTO articles (
                    project_id, title, summary, content, geo_score, geo_analysis
                ) VALUES (?, ?, ?, ?, 99, ?)
                """,
                (
                    self.project_id,
                    "Acme Cloud 证据说明",
                    "当前摘要",
                    "当前正文",
                    json.dumps(
                        {
                            "length_profile": {
                                "key": "standard",
                                "requested_target_length": 200,
                                "effective_target_length": 1000,
                                "source": "actual_length_upgrade",
                            }
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()

        result = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        mock_score.assert_called_once_with(
            title="Acme Cloud 证据说明",
            content="当前正文",
            brand="Acme Cloud",
            keywords=["evidence", "automation"],
            target_length=1000,
            requested_target_length=200,
            source="actual_length_upgrade",
        )
        self.assertEqual(result["baseline"]["average_geo_score"], 37)
        self.assertEqual(
            result["baseline"]["score_basis"],
            "current_rules_recomputed",
        )
        self.assertEqual(result["baseline"]["score_version"], GEO_SCORE_VERSION)

    def test_project_digest_hashes_inputs_instead_of_timestamp_precision(self):
        first = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            before_timestamp = conn.execute(
                "SELECT updated_at FROM projects WHERE id = ?",
                (self.project_id,),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE projects
                SET product = 'Cloud', industry = 'SaaS',
                    description = ?, website = 'https://acme.example',
                    keywords = '["cloud","agent","automation"]',
                    competitors = '["Rival"]'
                WHERE id = ?
                """,
                (
                    "面向企业的自动化云平台，包含目标客户、能力边界和可验证交付证据。" * 2,
                    self.project_id,
                ),
            )
            after_timestamp = conn.execute(
                "SELECT updated_at FROM projects WHERE id = ?",
                (self.project_id,),
            ).fetchone()[0]
            conn.commit()

        # The default SQL update above intentionally does not touch updated_at;
        # a digest based on that coarse timestamp would therefore miss it.
        self.assertEqual(before_timestamp, after_timestamp)
        second = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            persist=False,
        )

        self.assertNotEqual(first["input_digest"], second["input_digest"])
        self.assertNotEqual(first["score"], second["score"])

    def test_content_digest_changes_with_effective_project_scoring_context(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE projects SET keywords = ? WHERE id = ?",
                ('["alpha","beta","gamma"]', self.project_id),
            )
            conn.commit()

        first = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            title="Acme Cloud alpha 指南",
            content="Acme Cloud alpha 的完整使用说明。" * 80,
            persist=False,
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            # Keep the keyword count unchanged so this specifically proves
            # that the values consumed by content scoring are fingerprinted,
            # rather than only the aggregate project-readiness score.
            conn.execute(
                "UPDATE projects SET name = ?, keywords = ? WHERE id = ?",
                (
                    "Renamed Product",
                    '["delta","epsilon","zeta"]',
                    self.project_id,
                ),
            )
            conn.commit()

        second = diagnose_geo_readiness(
            self.database_path,
            project_id=self.project_id,
            title="Acme Cloud alpha 指南",
            content="Acme Cloud alpha 的完整使用说明。" * 80,
            persist=False,
        )

        self.assertNotEqual(first["input_digest"], second["input_digest"])
        self.assertNotEqual(first["score"], second["score"])


if __name__ == "__main__":
    unittest.main()
