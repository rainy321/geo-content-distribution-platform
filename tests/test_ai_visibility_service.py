import math
import unittest

from services.ai_visibility_service import (
    HANDOFF_ARCHIVE_SHA256,
    MAX_RESPONSES,
    VisibilityAnalysisError,
    analyze_ai_visibility,
    classify_citation_source,
    current_ai_visibility_algorithm_versions,
    inspect_ai_visibility_analysis_freshness,
    normalize_citation_url,
    reanalyze_ai_visibility_locally,
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
            ["The product SharedMark is recommended."],
            aliases=["SharedMark"],
            competitors=[{"name": "Rival", "aliases": ["SharedMark"]}],
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

    def test_project_keywords_admit_only_brand_anchored_aliases_and_keep_evidence(self):
        arguments = {
            "project_name": "北京同于野科技有限公司",
            "project_product": "懂AI 国内模型聚合与中转站",
            "project_keywords": [
                "同于野",
                "同于野科技",
                "北京同于野科技有限公司",
                "同于野AI中转站",
                "AI API中转站",
                "AI模型接口",
                "API中转服务",
                "大模型API接入",
            ],
            "stored_competitors": [],
        }
        matched = self.analyze(
            ["同于野AI API中转站主要面向需要统一模型接口的开发者。"],
            **arguments,
        )

        sample = matched["samples"][0]
        self.assertTrue(sample["brand_mentioned"])
        self.assertEqual(sample["matched_aliases"], ["同于野"])
        self.assertEqual(
            sample["matched_entity_terms"],
            [
                {
                    "match_type": "ALIAS",
                    "sources": ["project_keyword"],
                    "term": "同于野",
                }
            ],
        )
        self.assertEqual(sample["evidence"][0]["canonical_entity"], arguments["project_name"])
        self.assertEqual(sample["evidence"][0]["matched_term"], "同于野")
        self.assertEqual(sample["evidence"][0]["matched_term_sources"], ["project_keyword"])

        aliases = {
            item["term"]: item["sources"]
            for item in matched["project"]["effective_brand_aliases"]
        }
        self.assertEqual(aliases["同于野"], ["project_keyword"])
        self.assertEqual(aliases["同于野AI中转站"], ["project_keyword"])
        self.assertNotIn("AI API中转站", aliases)
        self.assertNotIn("AI模型接口", aliases)
        self.assertNotIn("API中转服务", aliases)
        self.assertNotIn("大模型API接入", aliases)

        generic_only = self.analyze(
            ["AI API中转站是一类常见服务。"],
            **arguments,
        )
        self.assertFalse(generic_only["samples"][0]["brand_mentioned"])

        embedded_cjk = self.analyze(
            ["这是不同于野外工具的另一种品牌方案。"],
            **arguments,
        )
        self.assertFalse(embedded_cjk["samples"][0]["brand_mentioned"])

        legal_name_in_chinese_prose = self.analyze(
            ["北京同于野科技有限公司提供AI服务。"],
            **arguments,
        )
        self.assertTrue(
            legal_name_in_chinese_prose["samples"][0]["brand_mentioned"]
        )
        self.assertEqual(
            legal_name_in_chinese_prose["samples"][0]["mentions"][0]["match_type"],
            "EXACT",
        )

        short_alias_in_chinese_prose = self.analyze(
            ["同于野是一个品牌。"],
            **arguments,
        )
        self.assertTrue(
            short_alias_in_chinese_prose["samples"][0]["brand_mentioned"]
        )
        self.assertEqual(
            short_alias_in_chinese_prose["samples"][0]["matched_aliases"],
            ["同于野"],
        )

    def test_request_aliases_reject_generic_category_terms_but_keep_real_aliases(self):
        arguments = {
            "project_name": "北京同于野科技有限公司",
            "project_product": "懂AI 国内模型聚合与中转站",
            "project_keywords": [],
            "stored_competitors": [],
        }

        generic = self.analyze(
            [
                "企业知识库、知识库、云服务、人工智能、开发平台、模型服务、"
                "AI API 中转站和业务协同门户都是常见的产品类别。"
            ],
            aliases=[
                "企业知识库",
                "知识库",
                "云服务",
                "人工智能",
                "开发平台",
                "模型服务",
                "AI API 中转站",
                # This phrase is intentionally absent from the category
                # fragment table: unanchored CJK prose must still be rejected.
                "业务协同门户",
            ],
            **arguments,
        )
        self.assertFalse(generic["samples"][0]["brand_mentioned"])
        effective_generic_aliases = {
            item["term"]
            for item in generic["project"]["effective_brand_aliases"]
        }
        self.assertTrue(
            {
                "企业知识库",
                "知识库",
                "云服务",
                "人工智能",
                "开发平台",
                "模型服务",
                "AI API 中转站",
                "业务协同门户",
            }.isdisjoint(effective_generic_aliases)
        )
        self.assertIn(
            "已忽略 8 个缺少可辨识品牌锚点的显式别名。",
            generic["warnings"],
        )
        self.assertIn(arguments["project_product"], effective_generic_aliases)

        chinese_alias = self.analyze(
            ["同于野AI 中转站面向需要统一接口的开发者。"],
            aliases=["同于野"],
            **arguments,
        )
        self.assertTrue(chinese_alias["samples"][0]["brand_mentioned"])
        self.assertEqual(chinese_alias["samples"][0]["matched_aliases"], ["同于野"])

        english_alias = self.analyze(
            ["DongClaw is a product option for model API routing."],
            aliases=["DongClaw"],
            **arguments,
        )
        self.assertTrue(english_alias["samples"][0]["brand_mentioned"])
        self.assertEqual(
            english_alias["samples"][0]["matched_aliases"], ["DongClaw"]
        )

        uppercase_alias = self.analyze(
            ["ACME is another independently configured trade name."],
            aliases=["ACME"],
            **arguments,
        )
        self.assertTrue(uppercase_alias["samples"][0]["brand_mentioned"])

    def test_generic_project_product_is_not_automatically_a_brand_alias(self):
        result = self.analyze(
            ["企业知识库和超级企业知识库平台都可以集中管理内部文档。"],
            project_name="北京同于野科技有限公司",
            project_product="企业知识库",
            project_keywords=[],
            aliases=["超级企业知识库平台"],
            stored_competitors=[],
        )

        self.assertFalse(result["samples"][0]["brand_mentioned"])
        self.assertEqual(result["project"]["effective_brand_aliases"], [])
        self.assertIn("项目产品仅包含通用品类词，不作为品牌命中别名。", result["warnings"])
        self.assertIn(
            "已忽略 1 个缺少可辨识品牌锚点的显式别名。",
            result["warnings"],
        )

    def test_saved_analysis_freshness_is_version_aware(self):
        current = self.analyze(["Acme Cloud is recommended."])
        fresh = inspect_ai_visibility_analysis_freshness(current)

        self.assertEqual(fresh["status"], "current")
        self.assertFalse(fresh["reanalysis_required"])
        self.assertEqual(fresh["stale_components"], [])
        self.assertEqual(
            fresh["current_versions"], current_ai_visibility_algorithm_versions()
        )

        historical = dict(current)
        historical["algorithm_versions"] = {
            **current["algorithm_versions"],
            "mention": "mention-python-port-1.1.0",
        }
        stale = inspect_ai_visibility_analysis_freshness(historical)
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["reanalysis_required"])
        self.assertEqual(stale["stale_components"], ["mention"])

        pre_cjk_boundary_result = dict(current)
        pre_cjk_boundary_result["algorithm_versions"] = {
            **current["algorithm_versions"],
            "integration": "ai-visibility-python-port-1.4.0",
            "mention": "mention-python-port-1.4.0",
        }
        pre_cjk_boundary_stale = inspect_ai_visibility_analysis_freshness(
            pre_cjk_boundary_result
        )
        self.assertEqual(
            pre_cjk_boundary_stale["stale_components"],
            ["integration", "mention"],
        )

        unversioned = inspect_ai_visibility_analysis_freshness({"summary": {}})
        self.assertEqual(unversioned["status"], "unversioned")
        self.assertTrue(unversioned["reanalysis_required"])

    def test_local_reanalysis_updates_old_parser_result_without_external_call(self):
        old = self.analyze(
            ["同于野AI API中转站面向开发者。"],
            project_name="北京同于野科技有限公司",
            project_product="懂AI 国内模型聚合与中转站",
            project_keywords=[],
            stored_competitors=[],
        )
        self.assertFalse(old["samples"][0]["brand_mentioned"])
        old["algorithm_versions"] = {
            **old["algorithm_versions"],
            "mention": "mention-python-port-1.1.0",
        }

        outcome = reanalyze_ai_visibility_locally(
            previous_analysis=old,
            project_id=7,
            project_name="北京同于野科技有限公司",
            project_product="懂AI 国内模型聚合与中转站",
            project_keywords=["同于野"],
            project_website="https://tongyuye.com",
            stored_competitors=[],
            prompt="适合开发者的 AI API 中转站有哪些？",
            responses=["同于野AI API中转站面向开发者。"],
        )

        self.assertEqual(outcome["refresh"]["status"], "refreshed")
        self.assertTrue(outcome["refresh"]["performed"])
        self.assertEqual(outcome["refresh"]["provider_calls"], 0)
        self.assertEqual(outcome["refresh"]["network_requests"], 0)
        self.assertTrue(outcome["analysis"]["samples"][0]["brand_mentioned"])
        provenance = outcome["analysis"]["methodology"]["local_reanalysis"]
        self.assertEqual(provenance["previous_algorithm_versions"]["mention"], "mention-python-port-1.1.0")
        self.assertEqual(provenance["provider_calls"], 0)

    def test_current_analysis_is_returned_without_reanalysis_unless_forced(self):
        current = self.analyze(["Acme Cloud is recommended."])
        outcome = reanalyze_ai_visibility_locally(
            previous_analysis=current,
            project_id=7,
            project_name="Acme Cloud",
            project_product="Acme",
            project_website="https://www.acme.com",
            stored_competitors=["RivalOS"],
            prompt="What are the best enterprise automation platforms?",
            responses=["This input must not be parsed on the current fast path."],
        )

        self.assertEqual(outcome["refresh"]["status"], "current")
        self.assertFalse(outcome["refresh"]["performed"])
        self.assertEqual(outcome["analysis"], current)
        self.assertIsNot(outcome["analysis"], current)

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
            "mention-python-port-1.5.0",
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
