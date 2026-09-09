import math
import unittest

from services.ai_visibility_service import (
    HANDOFF_ARCHIVE_SHA256,
    MAX_RESPONSES,
    VisibilityAnalysisError,
    analyze_ai_visibility,
    classify_citation_source,
    normalize_citation_url,
)


class AIVisibilityServiceTests(unittest.TestCase):
    def analyze(self, responses, **overrides):
        arguments = {
            "project_id": 7,
            "project_name": "Acme Cloud",
            "project_product": "Acme",
            "project_website": "https://www.acme.com",
            "stored_competitors": ["RivalOS"],
            "prompt": "What are the best enterprise automation platforms?",
            "responses": responses,
        }
        arguments.update(overrides)
        return analyze_ai_visibility(**arguments)

    def test_extracts_mentions_rank_and_perception_with_evidence(self):
        result = self.analyze(
            [
                "Top platforms:\n"
                "1. RivalOS is stable but expensive.\n"
                "2. Acme Cloud is powerful, easy to use, and recommended "
                "because its automation is strong."
            ]
        )

        sample = result["samples"][0]
        self.assertTrue(sample["brand_mentioned"])
        self.assertEqual(sample["recommendation_rank"], 2)
        rank_by_name = {item["entity_name"]: item for item in sample["ranks"]}
        self.assertEqual(rank_by_name["RivalOS"]["rank"], 1)
        self.assertEqual(rank_by_name["Acme Cloud"]["method"], "STRUCTURED_LIST")
        brand_sentiment = next(
            item
            for item in sample["perception"]["sentiments"]
            if item["entity_id"] == "brand"
        )
        self.assertEqual(brand_sentiment["label"], "POSITIVE")
        brand_attributes = {
            item["attribute"]
            for item in sample["perception"]["attributes"]
            if item["entity_id"] == "brand"
        }
        self.assertIn("CAPABILITY", brand_attributes)
        self.assertIn("EASE_OF_USE", brand_attributes)
        self.assertTrue(sample["evidence"])
        self.assertEqual(sample["evidence"][0]["type"], "MENTION")

    def test_mention_spans_use_javascript_utf16_offsets_after_emoji(self):
        result = self.analyze(["😀 Acme Cloud is recommended."])

        mention = next(
            item for item in result["samples"][0]["mentions"] if item["entity_id"] == "brand"
        )
        self.assertEqual(mention["surface_text"], "Acme Cloud")
        self.assertEqual(mention["span_start"], 3)
        self.assertEqual(mention["span_end"], 13)
        self.assertNotIn("_span_start_index", mention)

    def test_nfkc_short_term_ambiguity_and_fuzzy_rules_remain_auditable(self):
        fullwidth = self.analyze(
            ["ＡＣＭＥ is recommended."],
            project_name="ACME",
            project_product="",
        )
        self.assertTrue(fullwidth["samples"][0]["brand_mentioned"])
        self.assertEqual(fullwidth["samples"][0]["mentions"][0]["span_end"], 4)

        without_context = self.analyze(
            ["We go home."],
            project_name="Go",
            project_product="",
            stored_competitors=[],
        )
        with_context = self.analyze(
            ["We recommend Go as the product."],
            project_name="Go",
            project_product="",
            stored_competitors=[],
        )
        self.assertFalse(without_context["samples"][0]["brand_mentioned"])
        self.assertTrue(with_context["samples"][0]["brand_mentioned"])

        ambiguous = self.analyze(
            ["The product Common is recommended."],
            aliases=["Common"],
            competitors=[{"name": "Rival", "aliases": ["Common"]}],
        )
        ambiguous_mentions = ambiguous["samples"][0]["mentions"]
        self.assertEqual(len(ambiguous_mentions), 2)
        self.assertTrue(all(item["ambiguous"] for item in ambiguous_mentions))
        self.assertTrue(all(item["confidence"] <= 0.45 for item in ambiguous_mentions))

        fuzzy = self.analyze(
            ["We recommend Acmee as the product."],
            project_name="Acme",
            project_product="",
            stored_competitors=[],
        )
        self.assertEqual(fuzzy["samples"][0]["mentions"][0]["match_type"], "FUZZY")
        self.assertTrue(fuzzy["samples"][0]["mentions"][0]["needs_review"])

    def test_duplicate_numbering_does_not_create_a_structured_rank(self):
        result = self.analyze(["Options:\n1. Acme Cloud\n1. RivalOS"])

        self.assertTrue(all(item["rank"] is None for item in result["samples"][0]["ranks"]))
        self.assertTrue(
            all(
                item["method"] == "MENTION_POSITION_ONLY"
                for item in result["samples"][0]["ranks"]
            )
        )

    def test_normalizes_citation_and_classifies_ownership_before_source_rules(self):
        normalized = normalize_citation_url(
            "HTTPS://ExAmPle.COM:443/x?utm_source=news&product=pro&GCLID=c&lang=zh#part"
        )

        self.assertEqual(
            normalized["canonical_url"],
            "https://example.com/x?product=pro&lang=zh",
        )
        self.assertEqual(normalized["removed_tracking_parameters"], ["utm_source", "GCLID"])
        self.assertEqual(normalized["registrable_domain"], "example.com")
        self.assertRegex(normalized["url_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            normalized["url_hash"],
            "2ef040c79d146a270f8ae9358d01b0366fc6488dfebb2a9a2a70f081f7ae15ff",
        )
        idn = normalize_citation_url("https://例子.com/产品?id=42#说明")
        self.assertEqual(idn["domain"], "xn--fsqu00a.com")
        self.assertEqual(
            idn["canonical_url"],
            "https://xn--fsqu00a.com/%E4%BA%A7%E5%93%81?id=42",
        )
        self.assertEqual(
            normalize_citation_url("https://faß.de/path")["canonical_url"],
            normalize_citation_url("https://xn--fa-hia.de/path")["canonical_url"],
        )
        with self.assertRaises(VisibilityAnalysisError):
            normalize_citation_url("https://user:secret@example.com/private")
        encoded_slash = normalize_citation_url("https://example.com/a%2Fb")
        path_slash = normalize_citation_url("https://example.com/a/b")
        self.assertNotEqual(encoded_slash["canonical_url"], path_slash["canonical_url"])
        self.assertNotEqual(encoded_slash["url_hash"], path_slash["url_hash"])
        owned = classify_citation_source(
            domain="docs.example.com",
            registrable_domain="example.com",
            owned_domains=("example.com",),
        )
        competitor = classify_citation_source(
            domain="support.rival.com",
            registrable_domain="rival.com",
            competitor_domains=("rival.com",),
        )
        publisher = classify_citation_source(domain="www.reuters.com")
        self.assertEqual(owned["source_type"], "OWNED")
        self.assertEqual(competitor["source_type"], "COMPETITOR_OWNED")
        self.assertEqual(publisher["source_type"], "PUBLISHER")

    def test_aggregates_normalized_citations_and_keeps_sample_evidence(self):
        result = self.analyze(
            [
                "Acme sources https://docs.acme.com/guide?utm_source=ai and "
                "https://www.reuters.com/technology/story#section.",
                "Read https://docs.acme.com/guide?gclid=click and "
                "https://www.g2.com/products/acme/reviews.",
            ]
        )

        by_domain = {item["domain"]: item for item in result["citations"]}
        self.assertEqual(len(result["citations"]), 3)
        self.assertTrue(by_domain["docs.acme.com"]["is_owned"])
        self.assertEqual(by_domain["docs.acme.com"]["count"], 2)
        self.assertEqual(by_domain["docs.acme.com"]["samples"], ["sample-1", "sample-2"])
        self.assertEqual(by_domain["www.reuters.com"]["source_type"], "PUBLISHER")
        self.assertEqual(by_domain["www.g2.com"]["source_type"], "REVIEW")
        self.assertEqual(result["summary"]["owned_citation_count"], 1)
        self.assertEqual(result["summary"]["owned_citation_rate"], 1.0)

    def test_calculates_position_weighted_sov_and_versioned_confidence(self):
        result = self.analyze(
            [
                "Recommended list:\n1. Acme Cloud\n2. RivalOS",
                "Recommended list:\n1. RivalOS\n2. Acme Cloud",
                "Acme Cloud and RivalOS are recommended platforms.",
                "RivalOS is the best platform.",
                "No tracked option was named.",
            ]
        )

        summary = result["summary"]
        self.assertEqual(summary["mentioned_count"], 3)
        self.assertEqual(summary["mention_rate"], 0.6)
        brand = next(item for item in result["entities"] if item["id"] == "brand")
        rival = next(item for item in result["entities"] if item["id"] == "competitor-1")
        expected_brand_voice = 2 + 1 / math.log2(3)
        expected_rival_voice = 2 + 2 / math.log2(3)
        self.assertAlmostEqual(brand["weighted_voice"], expected_brand_voice)
        self.assertAlmostEqual(rival["weighted_voice"], expected_rival_voice)
        self.assertAlmostEqual(
            summary["share_of_voice"],
            expected_brand_voice / (expected_brand_voice + expected_rival_voice),
        )
        self.assertIsNotNone(summary["confidence"]["interval_95"])
        self.assertEqual(summary["confidence"]["level"], "MEDIUM")
        self.assertEqual(
            summary["confidence"]["algorithm_version"],
            "metrics-ad-hoc-python-1.1.0",
        )
        self.assertAlmostEqual(
            summary["mention_rate_interval_95"]["lower"], 0.2307242813, places=9
        )
        self.assertAlmostEqual(
            summary["mention_rate_interval_95"]["upper"], 0.8823792258, places=9
        )

    def test_zero_voice_stays_unknown_and_five_consistent_samples_are_stable(self):
        zero = self.analyze(["No tracked option."] * 5)
        self.assertIsNone(zero["summary"]["share_of_voice"])
        self.assertTrue(
            all(item["share_of_voice"] is None for item in zero["entities"])
        )

        consistent = self.analyze(["Acme Cloud is recommended."] * 5)
        self.assertEqual(consistent["summary"]["confidence"]["level"], "HIGH")
        self.assertTrue(consistent["summary"]["confidence"]["stable"])
        self.assertEqual(
            consistent["summary"]["confidence"]["interval_95"],
            {"confidence": 0.95, "lower": 1.0, "upper": 1.0},
        )

    def test_recommendations_are_suppressed_without_committed_sample_evidence(self):
        one_sample = self.analyze(["RivalOS is best. https://www.reuters.com/x"])
        self.assertEqual(one_sample["recommendation_details"], [])

        result = self.analyze(
            [
                "RivalOS is best. https://www.reuters.com/a",
                "RivalOS is recommended. https://www.reuters.com/b",
                "RivalOS is the top platform without a citation.",
            ]
        )
        rules = [item["rule_id"] for item in result["recommendation_details"]]
        self.assertEqual(
            rules,
            ["content.competitor-coverage-gap", "source.owned-citation-gap"],
        )
        for recommendation in result["recommendation_details"]:
            self.assertTrue(recommendation["evidence"]["sample_ids"])
            self.assertEqual(recommendation["evidence"]["execution_ids"], [])
            self.assertEqual(
                recommendation["evidence"]["source_kind"], "AD_HOC_ANALYSIS"
            )
            self.assertIn("project:7:prompt:", recommendation["suppression_key"])
            self.assertGreater(recommendation["priority_score"], 0)

        source_suppression = self.analyze(
            [
                "Acme Cloud is recommended. https://www.reuters.com/a",
                "Acme Cloud is recommended. https://www.reuters.com/b",
                "Acme Cloud is recommended. https://www.reuters.com/c",
            ]
        )
        source_rules = [
            item["rule_id"] for item in source_suppression["recommendation_details"]
        ]
        self.assertIn("source.owned-citation-gap", source_rules)
        self.assertNotIn("source.mentioned-not-cited", source_rules)

        competitor_owned_only = self.analyze(
            [
                "Acme Cloud is recommended. https://rival.com/a",
                "Acme Cloud is recommended. https://rival.com/b",
            ],
            competitors=[{"name": "RivalOS", "domains": ["rival.com"]}],
        )
        competitor_rules = [
            item["rule_id"]
            for item in competitor_owned_only["recommendation_details"]
        ]
        self.assertIn("source.mentioned-not-cited", competitor_rules)
        self.assertNotIn("source.owned-citation-gap", competitor_rules)

    def test_unicode_case_matching_does_not_claim_javascript_exact_equivalence(self):
        result = self.analyze(
            ["Indıgo is relıable."],
            project_name="Indigo",
            project_product="",
            stored_competitors=[],
        )

        brand_mentions = [
            item
            for item in result["samples"][0]["mentions"]
            if item["entity_id"] == "brand"
        ]
        self.assertTrue(all(item["match_type"] != "EXACT" for item in brand_mentions))
        self.assertTrue(all(item["needs_review"] for item in brand_mentions))
        sentiments = result["samples"][0]["perception"]["sentiments"]
        self.assertTrue(all(item["label"] == "NEUTRAL" for item in sentiments))

    def test_response_metadata_and_handoff_trace_are_preserved_without_model_use(self):
        result = self.analyze(
            [{"text": "Acme Cloud is reliable.", "provider": "qwen", "model": "qwen-plus"}]
        )

        sample = result["samples"][0]
        self.assertEqual(sample["provider"], "qwen")
        self.assertEqual(sample["model"], "qwen-plus")
        self.assertFalse(result["methodology"]["external_model_used"])
        self.assertEqual(
            result["source_trace"]["handoff_archive_sha256"],
            HANDOFF_ARCHIVE_SHA256,
        )
        self.assertEqual(
            result["algorithm_versions"]["mention"],
            "mention-python-port-1.1.0",
        )

    def test_explicit_empty_competitors_disables_stored_comparison(self):
        result = self.analyze(
            ["Acme Cloud is recommended."],
            competitors=[],
        )

        self.assertEqual(len(result["entities"]), 1)
        self.assertTrue(any("未配置竞品" in warning for warning in result["warnings"]))

    def test_rejects_oversized_or_malformed_input(self):
        with self.assertRaisesRegex(VisibilityAnalysisError, "最多支持"):
            self.analyze(["answer"] * (MAX_RESPONSES + 1))
        with self.assertRaisesRegex(VisibilityAnalysisError, "不支持的字段"):
            self.analyze([{"text": "answer", "id": "external-id"}])
        with self.assertRaisesRegex(VisibilityAnalysisError, "aliases"):
            self.analyze(["answer"], aliases="Acme")
        with self.assertRaisesRegex(VisibilityAnalysisError, "不能为空"):
            self.analyze(["   "])
        with self.assertRaisesRegex(VisibilityAnalysisError, "显式 URL"):
            self.analyze([" ".join(f"https://example.com/{index}" for index in range(201))])


if __name__ == "__main__":
    unittest.main()
