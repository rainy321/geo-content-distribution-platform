import unittest

from services.ai_visibility_service import AI_VISIBILITY_ALGORITHM_VERSION
from services.content_diversity_service import DIVERSITY_VERSION
from services.geo_article_compiler_service import GEO_ARTICLE_COMPILER_VERSION
from services.geo_score_service import GEO_SCORE_VERSION
from services.release_version_registry import release_version_registry
from services.target_length_score_profile import TARGET_LENGTH_SCORE_PROFILE_VERSION


class ReleaseVersionRegistryTests(unittest.TestCase):
    def test_registry_reuses_runtime_constants(self):
        registry = release_version_registry()

        self.assertEqual(
            registry["algorithm"]["integration_version"],
            AI_VISIBILITY_ALGORITHM_VERSION,
        )
        self.assertEqual(
            registry["algorithm"]["article_compiler_version"],
            GEO_ARTICLE_COMPILER_VERSION,
        )
        self.assertEqual(
            registry["algorithm"]["geo_score_version"],
            GEO_SCORE_VERSION,
        )
        self.assertEqual(
            registry["algorithm"]["target_length_score_profile_version"],
            TARGET_LENGTH_SCORE_PROFILE_VERSION,
        )
        self.assertEqual(
            registry["product_features"]["content_diversity_version"],
            DIVERSITY_VERSION,
        )

    def test_registry_contains_versions_for_active_release_surfaces(self):
        registry = release_version_registry()

        self.assertIn("visibility_sampling_version", registry["algorithm"])
        self.assertIn("geo_blackbox_truth_matrix_version", registry["product_features"])
        self.assertIn("dongclaw_evaluation_report_version", registry["product_features"])
        self.assertIn("unified_ai_role_router_version", registry["product_features"])


if __name__ == "__main__":
    unittest.main()
