"""Single-source release manifest versions.

The Windows release script imports this registry after installing the locked
environment.  Values come from the same public constants used by the runtime,
so the manifest cannot silently drift from the code it describes.
"""

from __future__ import annotations

from services.ai_cost_ledger_service import AI_COST_LEDGER_VERSION
from services.ai_visibility_sampling_service import VISIBILITY_SAMPLING_VERSION
from services.ai_visibility_service import AI_VISIBILITY_ALGORITHM_VERSION
from services.content_diversity_service import DIVERSITY_VERSION
from services.content_engine_contract import CONTENT_ENGINE_CONTRACT_VERSION
from services.content_hygiene_service import CONTENT_HYGIENE_VERSION
from services.dongclaw_evaluation_service import (
    DONGCLAW_EVALUATION_REPORT_VERSION,
    DONGCLAW_EVALUATION_SCHEMA_VERSION,
)
from services.geo_article_compiler_service import (
    AI_CITEABILITY_SCORE_VERSION,
    GEO_ARTICLE_COMPILER_VERSION,
)
from services.geo_blackbox_experiment_service import (
    GEO_BLACKBOX_EXPERIMENT_VERSION,
    GEO_BLACKBOX_TRUTH_MATRIX_VERSION,
)
from services.geo_diagnostic_service import (
    CHANNEL_RECOMMENDATION_VERSION,
    GEO_DIAGNOSTIC_VERSION,
)
from services.geo_score_service import GEO_SCORE_VERSION
from services.media_account_risk_service import MEDIA_ACCOUNT_RISK_VERSION
from services.platform_publish_policy_service import PLATFORM_PUBLISH_POLICY_VERSION
from services.publish_outcome_service import PUBLISH_OUTCOME_VERSION
from services.target_length_score_profile import TARGET_LENGTH_SCORE_PROFILE_VERSION
from services.unified_ai_config_service import UNIFIED_AI_ROLE_ROUTER_VERSION


ALGORITHM_VERSION_REGISTRY = {
    "integration_version": AI_VISIBILITY_ALGORITHM_VERSION,
    "visibility_sampling_version": VISIBILITY_SAMPLING_VERSION,
    "geo_score_version": GEO_SCORE_VERSION,
    "target_length_score_profile_version": TARGET_LENGTH_SCORE_PROFILE_VERSION,
    "article_compiler_version": GEO_ARTICLE_COMPILER_VERSION,
    "ai_citeability_version": AI_CITEABILITY_SCORE_VERSION,
}

PRODUCT_FEATURE_VERSION_REGISTRY = {
    "content_engine_contract_version": CONTENT_ENGINE_CONTRACT_VERSION,
    "content_diversity_version": DIVERSITY_VERSION,
    "content_hygiene_version": CONTENT_HYGIENE_VERSION,
    "geo_diagnostic_version": GEO_DIAGNOSTIC_VERSION,
    "channel_recommendation_version": CHANNEL_RECOMMENDATION_VERSION,
    "ai_cost_ledger_version": AI_COST_LEDGER_VERSION,
    "platform_content_policy_version": PLATFORM_PUBLISH_POLICY_VERSION,
    "publish_outcome_version": PUBLISH_OUTCOME_VERSION,
    "geo_blackbox_panel_version": GEO_BLACKBOX_EXPERIMENT_VERSION,
    "geo_blackbox_truth_matrix_version": GEO_BLACKBOX_TRUTH_MATRIX_VERSION,
    "dongclaw_evaluation_schema_version": DONGCLAW_EVALUATION_SCHEMA_VERSION,
    "dongclaw_evaluation_report_version": DONGCLAW_EVALUATION_REPORT_VERSION,
    "media_account_risk_version": MEDIA_ACCOUNT_RISK_VERSION,
    "unified_ai_role_router_version": UNIFIED_AI_ROLE_ROUTER_VERSION,
}


def release_version_registry() -> dict[str, dict[str, str]]:
    """Return a fresh JSON-safe registry for release tooling."""

    return {
        "algorithm": dict(ALGORITHM_VERSION_REGISTRY),
        "product_features": dict(PRODUCT_FEATURE_VERSION_REGISTRY),
    }


__all__ = [
    "ALGORITHM_VERSION_REGISTRY",
    "PRODUCT_FEATURE_VERSION_REGISTRY",
    "release_version_registry",
]
