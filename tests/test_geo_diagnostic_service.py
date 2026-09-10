import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services.geo_diagnostic_service import (
    GEO_DIAGNOSTIC_VERSION,
    diagnose_geo_readiness,
    get_geo_diagnostic_run,
    list_geo_diagnostic_runs,
)


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
        self.assertNotIn("Acme Cloud", result["input_digest"])

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
            "ai-citeability-rules-2.1.0",
        )
        self.assertIn("citeability", result)
        self.assertEqual(len(result["citeability"]["dimensions"]), 7)
        self.assertTrue(any(gap["dimension"] == "structure" for gap in result["gaps"]))
        self.assertFalse(result["publish_ready"])
        self.assertEqual(result["readiness_status"], "blocked")
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
            "geo-score-mvp-rules-1.0.0",
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
            "dimensions": {},
            "blocking_issues": [],
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
        self.assertFalse(result["publish_ready"])
        self.assertEqual(result["readiness_status"], "blocked")
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
            "dimensions": {},
            "blocking_issues": [],
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

    def test_project_digest_changes_when_scored_article_evidence_changes(self):
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

        self.assertNotEqual(first["input_digest"], second["input_digest"])
        self.assertNotEqual(first["score"], second["score"])

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
