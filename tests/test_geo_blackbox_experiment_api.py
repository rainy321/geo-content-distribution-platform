import json
import unittest
from unittest.mock import patch

from sau_backend import app
from services.geo_blackbox_experiment_service import (
    GEO_BLACKBOX_EXPERIMENT_VERSION,
    GEO_BLACKBOX_TRUTH_MATRIX_VERSION,
)


class GEOBlackboxExperimentApiTests(unittest.TestCase):
    def setUp(self):
        self.original_config = {
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(ACCESS_CONTROL_ENABLED=False, TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)

    def test_truth_matrix_is_read_only_and_rejects_query_parameters(self):
        with patch("sau_backend.sqlite3.connect") as connect:
            response = self.client.get("/api/geo/blackbox/truth-matrix")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["version"], GEO_BLACKBOX_TRUTH_MATRIX_VERSION)
        rows = {item["capability"]: item for item in body["data"]["rows"]}
        self.assertEqual(rows["crawl"]["state_after_this_service"], "UNOBSERVABLE_INTERNAL")
        self.assertEqual(rows["fact_accuracy"]["state_after_this_service"], "NOT_MEASURED")
        connect.assert_not_called()

        rejected = self.client.get(
            "/api/geo/blackbox/truth-matrix", query_string={"project_id": 1}
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("不接受查询参数", rejected.get_json()["msg"])

    def test_imported_panel_evaluates_empty_panel_without_storage_or_calls(self):
        with patch("sau_backend.sqlite3.connect") as connect:
            response = self.client.post(
                "/api/geo/blackbox/imported-panel",
                json={"snapshots": []},
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["version"], GEO_BLACKBOX_EXPERIMENT_VERSION)
        self.assertEqual(data["status"], "INCOMPLETE")
        self.assertEqual(len(data["blockers"]), 8)
        self.assertTrue(all(item["code"] == "SNAPSHOT_MISSING" for item in data["blockers"]))
        connect.assert_not_called()

    def test_imported_panel_passes_only_snapshots_to_service(self):
        expected = {
            "version": GEO_BLACKBOX_EXPERIMENT_VERSION,
            "status": "INCOMPLETE",
        }
        snapshots = [{"synthetic": True}]
        with patch(
            "sau_backend.evaluate_geo_blackbox_panel", return_value=expected
        ) as evaluate:
            response = self.client.post(
                "/api/geo/blackbox/imported-panel",
                json={"snapshots": snapshots},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], expected)
        evaluate.assert_called_once_with(snapshots)

    def test_imported_panel_rejects_invalid_root_contract_before_evaluation(self):
        cases = (
            ([], "JSON 对象"),
            ({}, "snapshots 字段必填"),
            ({"snapshots": [], "persist": True}, "不支持的字段"),
            ({"snapshots": "not-an-array"}, "snapshots must be an array"),
            ({"snapshots": [None]}, "snapshots[0] must be an object"),
            ({"snapshots": []}, None),
        )
        for payload, expected_message in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/geo/blackbox/imported-panel", json=payload
                )
                expected_status = 200 if expected_message is None else 400
                self.assertEqual(response.status_code, expected_status)
                if expected_message is not None:
                    self.assertIn(expected_message, response.get_json()["msg"])

    def test_imported_panel_rejects_invalid_media_type_encoding_and_json(self):
        non_json = self.client.post(
            "/api/geo/blackbox/imported-panel", data=b"snapshots=[]"
        )
        compressed = self.client.post(
            "/api/geo/blackbox/imported-panel",
            data=b' {"snapshots": []}',
            content_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )
        malformed = self.client.post(
            "/api/geo/blackbox/imported-panel",
            data=b'{"snapshots":',
            content_type="application/json",
        )

        self.assertEqual(non_json.status_code, 415)
        self.assertEqual(compressed.status_code, 415)
        self.assertEqual(malformed.status_code, 400)
        self.assertIn("JSON 无效", malformed.get_json()["msg"])

    def test_imported_panel_rejects_declared_and_actual_oversize_requests(self):
        maximum = 8 * 1024 * 1024
        declared = self.client.post(
            "/api/geo/blackbox/imported-panel",
            data=b"{}",
            content_type="application/json",
            environ_overrides={"CONTENT_LENGTH": str(maximum + 1)},
        )
        actual = self.client.post(
            "/api/geo/blackbox/imported-panel",
            data=json.dumps({"snapshots": [], "padding": "x" * maximum}),
            content_type="application/json",
        )

        self.assertEqual(declared.status_code, 413)
        self.assertEqual(actual.status_code, 413)
        self.assertEqual(actual.get_json()["code"], 413)

    def test_existing_operator_gate_protects_both_endpoints(self):
        app.config["ACCESS_CONTROL_ENABLED"] = True

        truth = self.client.get("/api/geo/blackbox/truth-matrix")
        panel = self.client.post(
            "/api/geo/blackbox/imported-panel", json={"snapshots": []}
        )

        self.assertEqual(truth.status_code, 401)
        self.assertEqual(panel.status_code, 401)
        self.assertTrue(truth.get_json()["data"]["authentication_required"])
        self.assertTrue(panel.get_json()["data"]["authentication_required"])


if __name__ == "__main__":
    unittest.main()
