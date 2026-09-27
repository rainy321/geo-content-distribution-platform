import json
import socket
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from urllib3._collections import HTTPHeaderDict

from db.createTable import initialize_database
from services import geo_site_audit_service as site_audit_module
from services.geo_site_audit_service import (
    GEO_SITE_AUDIT_VERSION,
    GEOSiteAuditConflictError,
    GEOSiteAuditError,
    GEOSiteAuditTimeoutError,
    IN_PROGRESS_EXPIRY_SECONDS,
    audit_geo_site,
    get_geo_site_audit_run,
    list_geo_site_audit_runs,
    run_geo_site_audit,
)


PUBLIC_IP = "93.184.216.34"


def _public_dns(hostname, port, *, type=socket.SOCK_STREAM):
    del hostname, type
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (PUBLIC_IP, port),
        )
    ]


class _FakeResponse:
    def __init__(self, status_code=200, *, body=b"", headers=None):
        self.status_code = status_code
        self.body = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class _FakeHttpClient:
    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url not in self.routes:
            raise AssertionError(f"unexpected HTTP request: {url}")
        response = self.routes[url]
        if isinstance(response, Exception):
            raise response
        return response


def _healthy_html(*, noindex=False):
    robots = '<meta name="robots" content="noindex, nofollow">' if noindex else ""
    body = "可验证的服务端正文、产品事实与来源说明。" * 60
    return f"""<!doctype html>
    <html lang="zh-CN">
      <head>
        <title>示例品牌知识中心</title>
        <meta name="description" content="公开、可验证的品牌与产品资料。">
        {robots}
        <link rel="canonical" href="https://brand.example/">
        <script type="application/ld+json">
          {{"@context":"https://schema.org","@type":"Organization","name":"示例品牌"}}
        </script>
      </head>
      <body><main><h1>示例品牌</h1><h2>产品事实</h2><p>{body}</p></main></body>
    </html>"""


def _healthy_routes(*, html=None, robots=None, llms_status=200, sitemap_status=200):
    return {
        "https://brand.example/": _FakeResponse(
            200,
            body=html if html is not None else _healthy_html(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        ),
        "https://brand.example/robots.txt": _FakeResponse(
            200,
            body=(
                robots
                if robots is not None
                else "User-agent: *\nAllow: /\nSitemap: https://brand.example/sitemap.xml\n"
            ),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        ),
        "https://brand.example/llms.txt": _FakeResponse(
            llms_status,
            body="# 示例品牌\n\n- [产品事实](https://brand.example/)"
            if llms_status == 200
            else "",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        ),
        "https://brand.example/sitemap.xml": _FakeResponse(
            sitemap_status,
            body=(
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://brand.example/</loc></url></urlset>"
            )
            if sitemap_status == 200
            else "",
            headers={"Content-Type": "application/xml"},
        ),
    }


class GEOSiteAuditServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "site-audit.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "示例品牌",
                        "https://brand.example/",
                        "示例产品",
                        "软件",
                        "仅用于站点审计测试",
                        "[]",
                        "[]",
                    ),
                )
                self.project_id = int(cursor.lastrowid)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, client, **overrides):
        values = {
            "project_id": self.project_id,
            "confirm_external_fetch": True,
            "request_id": "site-audit-request-1",
            "idempotency_key": "site-audit-key-1",
            "http_client": client,
            "dns_resolver": _public_dns,
            "now": datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return run_geo_site_audit(self.database_path, **values)

    def test_healthy_site_scores_ready_without_overclaiming_llms_txt(self):
        client = _FakeHttpClient(_healthy_routes())

        result = audit_geo_site(
            "https://brand.example/",
            http_client=client,
            dns_resolver=_public_dns,
            now=datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(result["audit_version"], GEO_SITE_AUDIT_VERSION)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["grade"], "A")
        self.assertEqual(result["score"], 100)
        self.assertTrue(all(item["status"] == "pass" for item in result["checks"]))
        self.assertEqual(result["structured_data"]["types"], ["Organization"])
        self.assertTrue(result["resources"]["llms_txt"]["present"])
        self.assertTrue(result["resources"]["llms_txt"]["proposal_only"])
        self.assertEqual(result["resources"]["llms_txt"]["score_weight"], 0)
        self.assertEqual(result["resources"]["page"]["http_status"], 200)
        self.assertEqual(len(result["resources"]["page"]["sha256"]), 64)
        self.assertNotIn("body", result["resources"]["page"])
        self.assertFalse(result["methodology"]["javascript_executed"])
        self.assertFalse(result["methodology"]["external_model_used"])
        self.assertEqual(result["methodology"]["logical_http_requests"], 4)
        self.assertTrue(any("不是" in item for item in result["boundaries"]))
        self.assertEqual(
            [url for url, _kwargs in client.calls],
            [
                "https://brand.example/",
                "https://brand.example/robots.txt",
                "https://brand.example/llms.txt",
                "https://brand.example/sitemap.xml",
            ],
        )
        for _url, kwargs in client.calls:
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(kwargs["stream"])
            self.assertEqual(kwargs["timeout"], (5.0, 15.0))

    def test_noindex_and_ai_retrieval_robots_block_are_blocking_failures(self):
        robots = """User-agent: *
Disallow: /
"""
        client = _FakeHttpClient(
            _healthy_routes(
                html=_healthy_html(noindex=True),
                robots=robots,
                llms_status=404,
                sitemap_status=404,
            )
        )

        result = audit_geo_site(
            "https://brand.example/",
            http_client=client,
            dns_resolver=_public_dns,
        )

        checks = {item["key"]: item for item in result["checks"]}
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["grade"], "D")
        self.assertEqual(checks["indexability"]["status"], "fail")
        self.assertEqual(checks["crawler_access"]["status"], "fail")
        retrieval = {
            item["user_agent"]: item["allowed"]
            for item in result["crawler_access"]
            if item["user_agent"]
            in {"OAI-SearchBot", "ChatGPT-User", "PerplexityBot"}
        }
        self.assertEqual(
            retrieval,
            {
                "OAI-SearchBot": False,
                "ChatGPT-User": False,
                "PerplexityBot": False,
            },
        )
        recommendation_codes = {item["code"] for item in result["recommendations"]}
        self.assertIn("page.noindex", recommendation_codes)
        self.assertIn("robots.ai_retrieval", recommendation_codes)

    def test_robots_unknown_or_ai_specific_denial_cannot_report_ready(self):
        unavailable_routes = _healthy_routes()
        unavailable_routes["https://brand.example/robots.txt"] = _FakeResponse(503)
        unavailable = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(unavailable_routes),
            dns_resolver=_public_dns,
        )
        self.assertEqual(unavailable["raw_score"], 90)
        self.assertEqual(unavailable["score"], 79)
        self.assertEqual(unavailable["grade"], "C")
        self.assertEqual(unavailable["status"], "needs_work")

        blocked_robots = """User-agent: OAI-SearchBot
Disallow: /
User-agent: PerplexityBot
Disallow: /
User-agent: Claude-SearchBot
Disallow: /
User-agent: Googlebot
Allow: /
"""
        blocked = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(robots=blocked_robots)),
            dns_resolver=_public_dns,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["grade"], "D")
        self.assertLessEqual(blocked["score"], 59)

    def test_robots_longest_path_rule_and_equal_allow_precedence(self):
        robots = """User-agent: OAI-SearchBot
Allow: /
Disallow: /private
Allow: /private/public$
User-agent: *
Allow: /
"""
        routes = _healthy_routes(robots=robots)
        routes["https://brand.example/private"] = routes.pop(
            "https://brand.example/"
        )
        private = audit_geo_site(
            "https://brand.example/private",
            http_client=_FakeHttpClient(routes),
            dns_resolver=_public_dns,
        )
        access = {
            item["user_agent"]: item["allowed"]
            for item in private["crawler_access"]
        }
        self.assertFalse(access["OAI-SearchBot"])

        public_routes = _healthy_routes(robots=robots)
        public_routes["https://brand.example/private/public"] = public_routes.pop(
            "https://brand.example/"
        )
        public = audit_geo_site(
            "https://brand.example/private/public",
            http_client=_FakeHttpClient(public_routes),
            dns_resolver=_public_dns,
        )
        access = {
            item["user_agent"]: item["allowed"]
            for item in public["crawler_access"]
        }
        self.assertTrue(access["OAI-SearchBot"])

    def test_robots_percent_encoding_is_normalized_per_rfc_9309(self):
        cases = [
            (
                "https://brand.example/私密",
                "User-agent: *\nDisallow: /%E7%A7%81%E5%AF%86\n",
            ),
            (
                "https://brand.example/%70rivate",
                "User-agent: *\nDisallow: /private\n",
            ),
        ]
        for target_url, robots in cases:
            with self.subTest(target_url=target_url):
                routes = _healthy_routes(robots=robots)
                routes[target_url] = routes.pop("https://brand.example/")
                result = audit_geo_site(
                    target_url,
                    http_client=_FakeHttpClient(routes),
                    dns_resolver=_public_dns,
                )
                access = {
                    item["user_agent"]: item["allowed"]
                    for item in result["crawler_access"]
                }
                self.assertFalse(access["OAI-SearchBot"])
                self.assertEqual(result["status"], "blocked")

    def test_repeated_x_robots_tag_values_are_coalesced(self):
        routes = _healthy_routes()
        repeated_headers = HTTPHeaderDict()
        repeated_headers.add("Content-Type", "text/html; charset=utf-8")
        repeated_headers.add("X-Robots-Tag", "noindex")
        repeated_headers.add("X-Robots-Tag", "index")
        routes["https://brand.example/"].headers = repeated_headers

        result = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(routes),
            dns_resolver=_public_dns,
        )

        checks = {item["key"]: item for item in result["checks"]}
        self.assertEqual(checks["indexability"]["status"], "fail")
        self.assertEqual(result["status"], "blocked")

    def test_none_cross_domain_canonical_and_https_downgrade_are_blocking(self):
        none_html = _healthy_html().replace(
            "<meta name=\"description\" content=\"公开、可验证的品牌与产品资料。\">",
            "<meta name=\"description\" content=\"公开、可验证的品牌与产品资料。\">"
            "<meta name=\"robots\" content=\"index\">"
            "<meta name=\"robots\" content=\"none\">",
        )
        none_result = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(html=none_html)),
            dns_resolver=_public_dns,
        )
        self.assertEqual(none_result["status"], "blocked")
        self.assertEqual(
            {item["key"]: item["status"] for item in none_result["checks"]}[
                "indexability"
            ],
            "fail",
        )

        canonical_html = _healthy_html().replace(
            "https://brand.example/",
            "https://other.example/",
            1,
        )
        canonical_result = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(html=canonical_html)),
            dns_resolver=_public_dns,
        )
        canonical_check = {
            item["key"]: item for item in canonical_result["checks"]
        }["canonical"]
        self.assertTrue(canonical_check["blocking"])
        self.assertEqual(canonical_result["status"], "blocked")

        downgrade_routes = _healthy_routes()
        downgrade_routes["https://brand.example/"] = _FakeResponse(
            302,
            headers={"Location": "http://brand.example/"},
        )
        downgrade_client = _FakeHttpClient(downgrade_routes)
        downgrade = audit_geo_site(
            "https://brand.example/",
            http_client=downgrade_client,
            dns_resolver=_public_dns,
        )
        self.assertEqual(downgrade["status"], "blocked")
        self.assertEqual(
            downgrade["resources"]["page"]["error"],
            "insecure_redirect_downgrade",
        )
        self.assertNotIn(
            "http://brand.example/",
            [url for url, _kwargs in downgrade_client.calls],
        )

    def test_invalid_or_conflicting_canonical_is_blocking(self):
        invalid_targets = [
            "javascript://brand.example/x",
            "ftp://brand.example/x",
            "https://user:pass@brand.example/x",
            "https://brand.example:444/x",
            "http://brand.example/x",
        ]
        for canonical in invalid_targets:
            with self.subTest(canonical=canonical):
                html = _healthy_html().replace(
                    "https://brand.example/",
                    canonical,
                    1,
                )
                result = audit_geo_site(
                    "https://brand.example/",
                    http_client=_FakeHttpClient(_healthy_routes(html=html)),
                    dns_resolver=_public_dns,
                )
                check = {item["key"]: item for item in result["checks"]}[
                    "canonical"
                ]
                self.assertTrue(check["blocking"])
                self.assertEqual(result["status"], "blocked")

        conflicting_html = _healthy_html().replace(
            "</head>",
            '<link rel="canonical" href="https://brand.example/other"></head>',
        )
        conflicting = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(html=conflicting_html)),
            dns_resolver=_public_dns,
        )
        canonical_check = {
            item["key"]: item for item in conflicting["checks"]
        }["canonical"]
        self.assertTrue(canonical_check["blocking"])
        self.assertIn("冲突", canonical_check["evidence"])
        self.assertEqual(conflicting["status"], "blocked")

    def test_fake_ip_is_opt_in_and_rebinding_never_reaches_connection_pool(self):
        def fake_ip_dns(hostname, port, *, type=socket.SOCK_STREAM):
            del hostname, type
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.2", port))]

        fake_client = _FakeHttpClient({})
        with self.assertRaisesRegex(GEOSiteAuditError, "私网或保留地址"):
            audit_geo_site(
                "https://brand.example/",
                http_client=fake_client,
                dns_resolver=fake_ip_dns,
            )
        self.assertEqual(fake_client.calls, [])
        compatible = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes()),
            dns_resolver=fake_ip_dns,
            allow_proxy_fake_ip=True,
        )
        self.assertTrue(compatible["methodology"]["proxy_fake_ip_compatibility"])

        answers = [PUBLIC_IP, "127.0.0.1"]
        dns_calls = 0

        def rebinding_dns(hostname, port, *, type=socket.SOCK_STREAM):
            nonlocal dns_calls
            del hostname, type
            dns_calls += 1
            address = answers.pop(0) if answers else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        cached_dns = site_audit_module._AuditDnsCache(rebinding_dns)
        pinned_client = site_audit_module._PinnedPublicHttpClient(
            cached_dns,
            allow_proxy_fake_ip=False,
            deadline=time.monotonic() + 5,
        )
        with patch.object(site_audit_module.urllib3, "HTTPSConnectionPool") as pool:
            pool.return_value.urlopen.return_value.status = 200
            pool.return_value.urlopen.return_value.headers = HTTPHeaderDict()
            pool.return_value.urlopen.return_value.read.return_value = b""
            response = pinned_client.get("https://brand.example/")
            response.close()

        self.assertEqual(dns_calls, 1)
        self.assertEqual(answers, ["127.0.0.1"])
        self.assertEqual(pool.call_args.args[0], PUBLIC_IP)
        self.assertEqual(pool.call_args.kwargs["server_hostname"], "brand.example")

    def test_pinned_client_limits_validated_address_attempts(self):
        def many_public_addresses(hostname, port, *, type=socket.SOCK_STREAM):
            del hostname, type
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (f"93.184.216.{index}", port),
                )
                for index in range(30, 38)
            ]

        client = site_audit_module._PinnedPublicHttpClient(
            many_public_addresses,
            allow_proxy_fake_ip=False,
            deadline=time.monotonic() + 5,
        )
        with patch.object(site_audit_module.urllib3, "HTTPSConnectionPool") as pool:
            pool.return_value.urlopen.side_effect = OSError("offline")
            with self.assertRaises(site_audit_module.requests.ConnectionError):
                client.get("https://brand.example/")

        self.assertEqual(
            pool.call_count,
            site_audit_module.MAX_PINNED_ADDRESS_ATTEMPTS,
        )

    def test_schema_requires_context_and_html_fallback_is_not_llms_txt(self):
        html = _healthy_html().replace(
            '"@context":"https://schema.org",',
            "",
        )
        routes = _healthy_routes(html=html)
        routes["https://brand.example/llms.txt"] = _FakeResponse(
            200,
            body="<html><body>login</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        result = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(routes),
            dns_resolver=_public_dns,
        )

        self.assertEqual(result["structured_data"]["valid_blocks"], 0)
        self.assertEqual(result["structured_data"]["invalid_blocks"], 1)
        self.assertFalse(result["resources"]["llms_txt"]["present"])
        self.assertEqual(result["resources"]["llms_txt"]["status"], "invalid")
        self.assertIn(
            "discovery.llms_txt",
            {item["code"] for item in result["recommendations"]},
        )

    def test_schema_context_requires_the_real_schema_org_host(self):
        fake_contexts = [
            "https://schema.org.evil.example",
            "https://not-schema.org.invalid",
            "schema.org-ish",
        ]
        for context in fake_contexts:
            with self.subTest(context=context):
                html = _healthy_html().replace("https://schema.org", context)
                result = audit_geo_site(
                    "https://brand.example/",
                    http_client=_FakeHttpClient(_healthy_routes(html=html)),
                    dns_resolver=_public_dns,
                )
                self.assertEqual(result["structured_data"]["valid_blocks"], 0)
                self.assertEqual(result["structured_data"]["invalid_blocks"], 1)
                schema_check = {
                    item["key"]: item for item in result["checks"]
                }["structured_data"]
                self.assertEqual(schema_check["score"], 0)

        fake_mapping_html = _healthy_html().replace(
            '"@context":"https://schema.org"',
            '"@context":{"foo":"https://schema.org"}',
        )
        fake_mapping = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(html=fake_mapping_html)),
            dns_resolver=_public_dns,
        )
        self.assertEqual(fake_mapping["structured_data"]["valid_blocks"], 0)

        vocab_html = _healthy_html().replace(
            '"@context":"https://schema.org"',
            '"@context":{"@vocab":"https://schema.org/"}',
        )
        vocab = audit_geo_site(
            "https://brand.example/",
            http_client=_FakeHttpClient(_healthy_routes(html=vocab_html)),
            dns_resolver=_public_dns,
        )
        self.assertEqual(vocab["structured_data"]["valid_blocks"], 1)
        self.assertTrue(vocab["structured_data"]["recommended_entity_complete"])

    def test_total_deadline_bounds_dns_and_stops_later_requests(self):
        dns_calls = 0

        def slow_dns(hostname, port, *, type=socket.SOCK_STREAM):
            nonlocal dns_calls
            del hostname, port, type
            dns_calls += 1
            time.sleep(0.2)
            return []

        client = _FakeHttpClient({})
        started = time.monotonic()
        with patch.object(site_audit_module, "MAX_AUDIT_SECONDS", 0.01):
            with self.assertRaises(GEOSiteAuditTimeoutError):
                audit_geo_site(
                    "https://brand.example/",
                    http_client=client,
                    dns_resolver=slow_dns,
                )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.1)
        self.assertEqual(dns_calls, 1)
        self.assertEqual(client.calls, [])
        time.sleep(0.21)

    def test_private_targets_and_private_redirects_are_never_requested(self):
        direct_client = _FakeHttpClient({})
        with self.assertRaisesRegex(GEOSiteAuditError, "私网或保留地址"):
            audit_geo_site(
                "http://127.0.0.1/secret",
                http_client=direct_client,
                dns_resolver=_public_dns,
            )
        self.assertEqual(direct_client.calls, [])

        routes = _healthy_routes(llms_status=404, sitemap_status=404)
        routes["https://brand.example/"] = _FakeResponse(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )
        redirect_client = _FakeHttpClient(routes)

        result = audit_geo_site(
            "https://brand.example/",
            http_client=redirect_client,
            dns_resolver=_public_dns,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            {item["key"]: item["status"] for item in result["checks"]}[
                "page_access"
            ],
            "fail",
        )
        requested_urls = [url for url, _kwargs in redirect_client.calls]
        self.assertNotIn("http://169.254.169.254/latest/meta-data", requested_urls)
        self.assertEqual(requested_urls.count("https://brand.example/"), 1)

    def test_public_ipv6_literal_keeps_url_brackets(self):
        origin = "https://[2606:4700:4700::1111]"
        client = _FakeHttpClient(
            {
                f"{origin}/": _FakeResponse(
                    200,
                    body="<html lang='zh-CN'><head><title>IPv6</title></head>"
                    "<body><main><h1>IPv6</h1></main></body></html>",
                    headers={"Content-Type": "text/html; charset=utf-8"},
                ),
                f"{origin}/robots.txt": _FakeResponse(404),
                f"{origin}/llms.txt": _FakeResponse(404),
                f"{origin}/sitemap.xml": _FakeResponse(404),
            }
        )

        result = audit_geo_site(
            f"{origin}/",
            http_client=client,
            dns_resolver=_public_dns,
        )

        self.assertEqual(result["requested_url"], f"{origin}/")
        self.assertEqual(result["final_url"], f"{origin}/")
        self.assertEqual(
            [url for url, _kwargs in client.calls],
            [
                f"{origin}/",
                f"{origin}/robots.txt",
                f"{origin}/llms.txt",
                f"{origin}/sitemap.xml",
            ],
        )

    def test_cross_site_redirect_and_cross_site_sitemap_are_never_requested(self):
        robots = (
            "User-agent: *\nAllow: /\n"
            "Sitemap: https://attacker.example/stolen-sitemap.xml\n"
        )
        routes = _healthy_routes(
            robots=robots,
            llms_status=404,
            sitemap_status=404,
        )
        routes["https://brand.example/"] = _FakeResponse(
            302,
            headers={"Location": "https://attacker.example/landing"},
        )
        client = _FakeHttpClient(routes)

        result = audit_geo_site(
            "https://brand.example/",
            http_client=client,
            dns_resolver=_public_dns,
        )

        requested_urls = [url for url, _kwargs in client.calls]
        self.assertNotIn("https://attacker.example/landing", requested_urls)
        self.assertNotIn("https://attacker.example/stolen-sitemap.xml", requested_urls)
        self.assertEqual(result["status"], "blocked")
        rejected = next(
            item
            for item in result["resources"]["sitemaps"]
            if "attacker.example" in item["url"]
        )
        self.assertEqual(rejected["error"], "cross_site_or_unsafe_resource")

    def test_runner_requires_explicit_confirmation_before_any_fetch(self):
        client = _FakeHttpClient({})

        for confirmation in (False, None, "false", "0"):
            with self.subTest(confirmation=confirmation):
                with self.assertRaisesRegex(GEOSiteAuditError, "必须明确确认"):
                    self._run(
                        client,
                        confirm_external_fetch=confirmation,
                        request_id=f"confirmation-{confirmation}",
                        idempotency_key=f"confirmation-key-{confirmation}",
                    )

        self.assertEqual(client.calls, [])
        with closing(sqlite3.connect(self.database_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM geo_site_audit_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_runner_rejects_override_outside_project_site_before_fetch(self):
        client = _FakeHttpClient({})

        with self.assertRaisesRegex(GEOSiteAuditError, "必须与品牌项目官网属于同一站点"):
            self._run(client, url="https://other.example/")

        self.assertEqual(client.calls, [])

    def test_persistence_replay_and_history_are_stable_without_refetch(self):
        client = _FakeHttpClient(_healthy_routes())

        first = self._run(client)
        call_count = len(client.calls)
        replayed = self._run(client)

        self.assertIsInstance(first["id"], int)
        self.assertEqual(first["run_status"], "completed")
        self.assertEqual(replayed, first)
        self.assertEqual(len(client.calls), call_count)
        self.assertEqual(
            list_geo_site_audit_runs(
                self.database_path,
                project_id=self.project_id,
            ),
            [first],
        )
        self.assertEqual(get_geo_site_audit_run(self.database_path, first["id"]), first)
        with closing(sqlite3.connect(self.database_path)) as conn:
            stored = conn.execute(
                """
                SELECT status, request_id, idempotency_key, result_json
                FROM geo_site_audit_runs
                """
            ).fetchall()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][:3], ("completed", "site-audit-request-1", "site-audit-key-1"))
        self.assertEqual(json.loads(stored[0][3]), first)

    def test_started_checkpoints_remain_visible_without_automatic_refetch(self):
        crash_client = _FakeHttpClient(
            {"https://brand.example/": RuntimeError("synthetic process exit")}
        )
        for request_id, key in (
            ("fresh-started-request", "fresh-started-key"),
            ("expired-started-request", "expired-started-key"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic process exit"):
                self._run(
                    crash_client,
                    request_id=request_id,
                    idempotency_key=key,
                )

        expired_at = (
            datetime.now(timezone.utc)
            - timedelta(seconds=IN_PROGRESS_EXPIRY_SECONDS + 60)
        ).isoformat().replace("+00:00", "Z")
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE geo_site_audit_runs
                    SET created_at = ?, updated_at = ?
                    WHERE request_id = 'expired-started-request'
                    """,
                    (expired_at, expired_at),
                )

        runs = list_geo_site_audit_runs(
            self.database_path,
            project_id=self.project_id,
        )
        by_request = {item["request_id"]: item for item in runs}
        fresh = by_request["fresh-started-request"]
        expired = by_request["expired-started-request"]
        self.assertEqual(fresh["status"], "processing")
        self.assertTrue(fresh["in_progress"])
        self.assertFalse(fresh["outcome_unknown"])
        self.assertFalse(fresh["result_available"])
        self.assertEqual(expired["status"], "outcome_unknown")
        self.assertFalse(expired["in_progress"])
        self.assertTrue(expired["in_progress_expired"])
        self.assertTrue(expired["outcome_unknown"])
        self.assertTrue(expired["retry_policy"]["requires_new_identity"])
        expired_detail = get_geo_site_audit_run(self.database_path, expired["id"])
        self.assertEqual(expired_detail["request_id"], expired["request_id"])
        self.assertEqual(expired_detail["status"], "outcome_unknown")
        self.assertTrue(expired_detail["outcome_unknown"])

        replay_client = _FakeHttpClient(_healthy_routes())
        with self.assertRaisesRegex(GEOSiteAuditConflictError, "结果未知.*不会"):
            self._run(
                replay_client,
                request_id="expired-started-request",
                idempotency_key="expired-started-key",
            )
        with self.assertRaisesRegex(GEOSiteAuditConflictError, "仍在执行"):
            self._run(
                replay_client,
                request_id="fresh-started-request",
                idempotency_key="fresh-started-key",
            )
        self.assertEqual(replay_client.calls, [])

        completed = self._run(
            replay_client,
            request_id="replacement-request",
            idempotency_key="replacement-key",
        )
        self.assertEqual(completed["run_status"], "completed")
        self.assertEqual(
            len(
                list_geo_site_audit_runs(
                    self.database_path,
                    project_id=self.project_id,
                )
            ),
            3,
        )

    def test_request_and_idempotency_identity_conflicts_do_not_refetch(self):
        client = _FakeHttpClient(_healthy_routes())
        self._run(client)
        call_count = len(client.calls)

        conflict_cases = (
            {
                "request_id": "site-audit-request-1",
                "idempotency_key": "different-key",
            },
            {
                "request_id": "different-request",
                "idempotency_key": "site-audit-key-1",
            },
        )
        for overrides in conflict_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    GEOSiteAuditConflictError,
                    "已用于其他站点审计请求",
                ):
                    self._run(client, **overrides)

        self.assertEqual(len(client.calls), call_count)
        with closing(sqlite3.connect(self.database_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM geo_site_audit_runs").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
