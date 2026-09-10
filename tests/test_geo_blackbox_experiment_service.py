import unittest

from services.dongclaw_evaluation_service import (
    build_dongclaw_input_template,
    evaluate_dongclaw_manifest,
)
from services.geo_blackbox_experiment_service import (
    GEOBlackboxExperimentError,
    build_geo_blackbox_truth_matrix,
    evaluate_geo_blackbox_panel,
)


OPTIMIZED_AT = "2026-09-01T00:00:00+08:00"


class GEOBlackboxExperimentServiceTests(unittest.TestCase):
    def _report(
        self,
        *,
        phase,
        captured_at,
        brand_mentioned,
        model="qwen-max",
        source_reference="evidence/capture.json",
    ):
        manifest = build_dongclaw_input_template(study_id=f"study-{phase}-{captured_at}")
        manifest["study"]["optimized_at"] = OPTIMIZED_AT
        response = (
            "1. DongClaw 小龙虾，适合需要本地部署的团队。https://example.com/dongclaw\n"
            "2. RanClaw，可作为对照方案。"
            if brand_mentioned
            else "1. RanClaw，可作为候选方案。https://example.com/ranclaw"
        )
        manifest["attempts"] = [
            {
                "id": f"{phase}-{captured_at}-{model}",
                "phase": phase,
                "question_id": "cn-discovery",
                "provider": "qwen",
                "model": model,
                "surface": "api",
                "web_search_enabled": False,
                "replicate": 1,
                "captured_at": captured_at,
                "status": "success",
                "response_text": response,
                "error_code": None,
                "source_reference": source_reference,
            }
        ]
        return evaluate_dongclaw_manifest(manifest)

    def _snapshot(self, arm, day, report):
        return {
            "arm": arm,
            "checkpoint_day": day,
            "unit_id": f"{arm}-pages",
            "phase": "baseline" if day == 0 else "post_optimization",
            "evidence_reference": f"panel/{arm}/day-{day}.json",
            "report": report,
        }

    def _complete_panel(self):
        captures = {
            0: "2026-08-31T00:00:00+08:00",
            7: "2026-09-08T00:00:00+08:00",
            14: "2026-09-16T00:00:00+08:00",
            30: "2026-10-02T00:00:00+08:00",
        }
        snapshots = []
        for arm in ("treatment", "control"):
            for day, captured_at in captures.items():
                report = self._report(
                    phase="baseline" if day == 0 else "post_optimization",
                    captured_at=captured_at,
                    brand_mentioned=arm == "treatment" and day > 0,
                    source_reference=f"evidence/{arm}-{day}.json",
                )
                snapshots.append(self._snapshot(arm, day, report))
        return snapshots

    def test_truth_matrix_marks_hidden_and_unmeasured_stages_honestly(self):
        matrix = build_geo_blackbox_truth_matrix()
        rows = {item["capability"]: item for item in matrix["rows"]}

        self.assertEqual(rows["crawl"]["existing_state"], "UNOBSERVABLE_INTERNAL")
        self.assertEqual(rows["fact_accuracy"]["existing_state"], "NOT_MEASURED")
        self.assertEqual(rows["brand_evidence_graph"]["existing_state"], "MISSING")
        self.assertEqual(
            rows["retest_day_7_14_30"]["state_after_this_service"],
            "IMPLEMENTED_IMPORT_ONLY",
        )
        self.assertEqual(
            rows["matched_control"]["state_after_this_service"],
            "IMPLEMENTED_IMPORT_ONLY",
        )

    def test_missing_snapshots_are_blockers_and_never_imputed_as_zero(self):
        result = evaluate_geo_blackbox_panel([])

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(len(result["blockers"]), 8)
        self.assertTrue(all(item["code"] == "SNAPSHOT_MISSING" for item in result["blockers"]))
        self.assertIsNone(result["slots"][0]["metrics"])

    def test_complete_panel_computes_exact_match_descriptive_differences(self):
        result = evaluate_geo_blackbox_panel(self._complete_panel())

        self.assertEqual(result["status"], "READY_FOR_DESCRIPTIVE_COMPARISON")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(
            [item["checkpoint_day"] for item in result["checkpoint_comparisons"]],
            [7, 14, 30],
        )
        for comparison in result["checkpoint_comparisons"]:
            self.assertEqual(comparison["status"], "COMPARABLE")
            self.assertEqual(comparison["matched_key_count"], 1)
            mention = comparison["metrics"]["brand_mention_rate_success"]
            self.assertEqual(mention["treatment_change"], 1.0)
            self.assertEqual(mention["control_change"], 0.0)
            self.assertEqual(mention["difference_in_differences"], 1.0)
        self.assertIn("not proof", result["claim_boundary"])
        self.assertIsNone(result["slots"][0]["metrics"]["fact_accuracy"])

    def test_snapshot_without_raw_evidence_references_is_rejected(self):
        report = self._report(
            phase="baseline",
            captured_at="2026-08-31T00:00:00+08:00",
            brand_mentioned=False,
            source_reference=None,
        )
        snapshot = self._snapshot("treatment", 0, report)

        with self.assertRaisesRegex(GEOBlackboxExperimentError, "source_reference"):
            evaluate_geo_blackbox_panel([snapshot])

    def test_checkpoint_label_cannot_hide_an_early_observation(self):
        report = self._report(
            phase="post_optimization",
            captured_at="2026-09-03T00:00:00+08:00",
            brand_mentioned=True,
        )
        snapshot = self._snapshot("treatment", 7, report)

        with self.assertRaisesRegex(GEOBlackboxExperimentError, "checkpoint window"):
            evaluate_geo_blackbox_panel([snapshot])

    def test_model_drift_prevents_a_false_exact_match(self):
        snapshots = self._complete_panel()
        replacement = self._report(
            phase="post_optimization",
            captured_at="2026-09-08T00:00:00+08:00",
            brand_mentioned=False,
            model="qwen-plus",
        )
        for index, item in enumerate(snapshots):
            if item["arm"] == "control" and item["checkpoint_day"] == 7:
                snapshots[index] = self._snapshot("control", 7, replacement)
                break

        result = evaluate_geo_blackbox_panel(snapshots)
        day_seven = result["checkpoint_comparisons"][0]
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(day_seven["status"], "NO_EXACT_MATCHES")
        self.assertEqual(day_seven["matched_key_count"], 0)

    def test_one_capture_cannot_be_reused_as_two_panel_observations(self):
        treatment = self._report(
            phase="baseline",
            captured_at="2026-08-31T00:00:00+08:00",
            brand_mentioned=False,
            source_reference="evidence/shared.json",
        )
        control = self._report(
            phase="baseline",
            captured_at="2026-08-31T00:00:00+08:00",
            brand_mentioned=False,
            source_reference="evidence/shared.json",
        )
        snapshots = [
            self._snapshot("treatment", 0, treatment),
            self._snapshot("control", 0, control),
        ]

        with self.assertRaisesRegex(GEOBlackboxExperimentError, "raw source_reference"):
            evaluate_geo_blackbox_panel(snapshots)


if __name__ == "__main__":
    unittest.main()
