import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.weibo_official_connector import (
    WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE,
    WeiboAuthorizationError,
    WeiboCatalogError,
    WeiboConnectorUnavailableError,
    WeiboIdentity,
    WeiboOfficialConnector,
    WeiboPublishUncertainError,
    is_weibo_public_status_url,
    resolve_weibo_cli,
    resolve_weibo_cli_runtime,
)


class WeiboOfficialConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.executable = self.root / "weibo.exe"
        self.executable.write_bytes(b"placeholder")

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _result(payload=None, *, returncode=0):
        return SimpleNamespace(
            returncode=returncode,
            stdout="" if payload is None else json.dumps(payload),
            stderr="",
        )

    def test_missing_cli_fails_with_install_and_authorize_action(self):
        with patch("services.weibo_official_connector.shutil.which", return_value=None), patch.dict(
            "os.environ", {"WEIBO_OFFICIAL_CLI_PATH": ""}, clear=False
        ):
            with self.assertRaisesRegex(
                WeiboConnectorUnavailableError,
                "需安装/授权官方连接器",
            ):
                resolve_weibo_cli(self.root / "missing.exe")
        self.assertIn("@weibo-ai/weibo-cli", WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE)

    def test_bare_command_name_never_executes_a_cwd_file(self):
        with patch.dict(
            "os.environ", {"WEIBO_OFFICIAL_CLI_PATH": ""}, clear=False
        ), patch(
            "services.weibo_official_connector.shutil.which", return_value=None
        ), patch(
            "services.weibo_official_connector.Path.is_file", return_value=True
        ) as is_file:
            with self.assertRaises(WeiboConnectorUnavailableError):
                resolve_weibo_cli()
        is_file.assert_not_called()

    def test_frozen_desktop_prefers_packaged_node_and_official_cli(self):
        runtime_root = self.root / "weibo_cli_runtime"
        node = runtime_root / "node.exe"
        script = (
            runtime_root
            / "node_modules"
            / "@weibo-ai"
            / "weibo-cli"
            / "dist"
            / "index.js"
        )
        script.parent.mkdir(parents=True)
        node.write_bytes(b"node")
        script.write_text("// official cli", encoding="utf-8")
        with patch("services.weibo_official_connector.sys.frozen", True, create=True), patch(
            "services.weibo_official_connector.sys._MEIPASS",
            str(self.root),
            create=True,
        ), patch.dict("os.environ", {"WEIBO_OFFICIAL_CLI_PATH": ""}, clear=False):
            runtime = resolve_weibo_cli_runtime()

        self.assertEqual(runtime.source, "packaged")
        self.assertEqual(runtime.command_prefix, (str(node.resolve()), str(script.resolve())))

    def test_windows_npm_command_resolves_to_node_without_shell(self):
        npm_root = self.root / "npm"
        command = npm_root / "weibo-cli.cmd"
        script = (
            npm_root
            / "node_modules"
            / "@weibo-ai"
            / "weibo-cli"
            / "dist"
            / "index.js"
        )
        node = self.root / "node.exe"
        script.parent.mkdir(parents=True)
        command.write_text("placeholder", encoding="utf-8")
        script.write_text("// official cli", encoding="utf-8")
        node.write_bytes(b"node")
        with patch("services.weibo_official_connector.shutil.which") as which, patch.dict(
            "os.environ", {"WEIBO_OFFICIAL_CLI_PATH": ""}, clear=False
        ):
            which.side_effect = lambda name: str(node) if name == "node" else str(command)
            runtime = resolve_weibo_cli_runtime()

        self.assertEqual(runtime.command_prefix, (str(node.resolve()), str(script.resolve())))

    def test_write_action_and_ai_flag_must_come_from_runtime_catalog(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1:3] == ["commands", "list"]:
                return self._result(
                    {"commands": [{"group": "statuses", "action": "update/biz"}]}
                )
            if command[1:4] == ["commands", "show", "statuses"]:
                return self._result(
                    {
                        "command": {
                            "flags": [
                                {"name": "text", "type": "string"},
                                {
                                    "name": "ai_generated",
                                    "type": "boolean",
                                    "description": "声明 AI 生成内容",
                                },
                            ]
                        }
                    }
                )
            return self._result({"data": {"idstr": "1234567890"}})

        outcome = WeiboOfficialConnector(
            self.executable,
            runner=runner,
        ).publish_status("标题\n\n正文")

        self.assertEqual(outcome["status"], "success")
        self.assertEqual(outcome["url"], "https://m.weibo.cn/detail/1234567890")
        writes = [command for command in commands if command[1:3] == ["statuses", "update/biz"]]
        self.assertEqual(len(writes), 1)
        self.assertIn("--ai_generated", writes[0])
        self.assertNotIn("--browser", writes[0])

    def test_catalog_without_authorized_write_action_never_invokes_write(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return self._result(
                {"commands": [{"group": "statuses", "action": "show/biz"}]}
            )

        connector = WeiboOfficialConnector(self.executable, runner=runner)
        with self.assertRaisesRegex(WeiboCatalogError, "未开放"):
            connector.publish_status("正文")
        self.assertFalse(any(command[1] == "statuses" for command in commands))

    def test_missing_unique_ai_declaration_never_invokes_write(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1:3] == ["commands", "list"]:
                return self._result(
                    {"commands": [{"group": "statuses", "action": "update/biz"}]}
                )
            return self._result(
                {"command": {"flags": [{"name": "text", "type": "string"}]}}
            )

        with self.assertRaisesRegex(WeiboCatalogError, "AI 生成内容"):
            WeiboOfficialConnector(self.executable, runner=runner).publish_status("正文")
        self.assertFalse(any(command[1] == "statuses" for command in commands))

    def test_success_is_not_claimed_without_public_url_or_status_id(self):
        def runner(command, **_kwargs):
            if command[1:3] == ["commands", "list"]:
                return self._result(
                    {"commands": [{"group": "statuses", "action": "update/biz"}]}
                )
            if command[1:3] == ["commands", "show"]:
                return self._result(
                    {
                        "command": {
                            "flags": [
                                {"name": "text", "type": "string"},
                                {"name": "aigc", "type": "boolean"},
                            ]
                        }
                    }
                )
            return self._result({"ok": True})

        outcome = WeiboOfficialConnector(
            self.executable, runner=runner
        ).publish_status("正文")
        self.assertEqual(outcome["status"], "processing")
        self.assertFalse(outcome["url"])

    def test_generic_id_never_becomes_a_status_url(self):
        def runner(command, **_kwargs):
            if command[1:3] == ["commands", "list"]:
                return self._result(
                    {"commands": [{"group": "statuses", "action": "update/biz"}]}
                )
            if command[1:3] == ["commands", "show"]:
                return self._result(
                    {
                        "command": {
                            "flags": [
                                {"name": "text", "type": "string"},
                                {"name": "aigc", "type": "boolean"},
                            ]
                        }
                    }
                )
            return self._result(
                {"data": {"id": "1234567890", "screen_name": "账号身份"}}
            )

        outcome = WeiboOfficialConnector(
            self.executable, runner=runner
        ).publish_status("正文")
        self.assertEqual(outcome["status"], "processing")
        self.assertEqual(outcome["url"], "")

    def test_exit_zero_empty_or_bad_write_json_is_uncertain(self):
        for write_output in ("", "not-json"):
            with self.subTest(write_output=write_output):
                writes = []

                def runner(command, **_kwargs):
                    if command[1:3] == ["commands", "list"]:
                        return self._result(
                            {"commands": [{"group": "statuses", "action": "update/biz"}]}
                        )
                    if command[1:3] == ["commands", "show"]:
                        return self._result(
                            {
                                "command": {
                                    "flags": [
                                        {"name": "text", "type": "string"},
                                        {"name": "aigc", "type": "boolean"},
                                    ]
                                }
                            }
                        )
                    writes.append(command)
                    return SimpleNamespace(returncode=0, stdout=write_output, stderr="")

                connector = WeiboOfficialConnector(self.executable, runner=runner)
                with self.assertRaisesRegex(
                    WeiboPublishUncertainError,
                    "最终状态未知",
                ):
                    connector.publish_status("正文")
                self.assertEqual(len(writes), 1)

    def test_read_rows_require_returned_public_url_and_unrestricted_visibility(self):
        statuses = [
            {
                "idstr": "1111111111",
                "text": "精确标题<br>只有编号",
                "visible": {"type": 0},
                "created_at": "2026-09-15T08:00:00Z",
            },
            {
                "idstr": "2222222222",
                "text": "精确标题<br>受限可见",
                "url": "https://m.weibo.cn/detail/2222222222",
                "visible": {"type": 1},
                "created_at": "2026-09-15T08:01:00Z",
            },
            {
                "idstr": "3333333333",
                "text": "精确标题<br>公开可见",
                "url": "https://m.weibo.cn/detail/3333333333",
                "visible": {"type": 0},
                "created_at": "2026-09-15T08:02:00Z",
            },
        ]

        def runner(command, **_kwargs):
            if command[1:3] == ["commands", "list"]:
                return self._result(
                    {
                        "commands": [
                            {"group": "statuses", "action": "user_timeline/biz"}
                        ]
                    }
                )
            if command[1:3] == ["commands", "show"]:
                return self._result(
                    {"command": {"flags": [{"name": "count", "type": "number"}]}}
                )
            return self._result({"statuses": statuses})

        rows = WeiboOfficialConnector(
            self.executable, runner=runner
        ).recent_status_rows("精确标题")

        self.assertEqual(len(rows), 3)
        self.assertNotIn("已发布", rows[0]["text"])
        self.assertEqual(rows[0]["urls"], ())
        self.assertNotIn("已发布", rows[1]["text"])
        self.assertEqual(
            rows[1]["urls"],
            ("https://m.weibo.cn/detail/2222222222",),
        )
        self.assertIn("已发布", rows[2]["text"])
        self.assertEqual(
            rows[2]["urls"],
            ("https://m.weibo.cn/detail/3333333333",),
        )

    def test_marker_binds_live_default_session_uid(self):
        current_uid = "123456"

        def runner(command, **_kwargs):
            if command[1:3] == ["auth", "whoami"]:
                return self._result(
                    {"user": {"idstr": current_uid, "screen_name": "官方账号"}}
                )
            return self._result({"ok": True})

        connector = WeiboOfficialConnector(self.executable, runner=runner)
        marker = self.root / "account.json"
        connector.write_marker(marker, WeiboIdentity("123456", "官方账号"))
        self.assertEqual(connector.verify_marker(marker).uid, "123456")

        current_uid = "999999"
        with self.assertRaisesRegex(WeiboAuthorizationError, "不一致"):
            connector.verify_marker(marker)

    def test_public_link_allowlist_rejects_management_and_short_links(self):
        self.assertTrue(is_weibo_public_status_url("https://m.weibo.cn/detail/123456"))
        self.assertTrue(is_weibo_public_status_url("https://weibo.com/123456/AbCdE12"))
        self.assertFalse(is_weibo_public_status_url("https://weibo.com/u/123456"))
        self.assertFalse(is_weibo_public_status_url("https://weibo.com/login.php"))
        self.assertFalse(is_weibo_public_status_url("https://t.cn/abc123"))


if __name__ == "__main__":
    unittest.main()
