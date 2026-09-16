import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from services.publisher_adapter import PublishContent
from services.weibo_official_connector import (
    WeiboCatalogError,
    WeiboConnectorUnavailableError,
    WeiboIdentity,
    WeiboOfficialConnector,
    WeiboPublishUncertainError,
)
from services.weibo_publisher_adapter import WeiboPublisherAdapter


class _Connector:
    def __init__(self, *, outcome=None, verify_error=None, publish_error=None):
        self.outcome = outcome or {
            "status": "success",
            "url": "https://m.weibo.cn/detail/1234567890",
        }
        self.verify_error = verify_error
        self.publish_error = publish_error
        self.calls = []

    def verify_marker(self, marker):
        self.calls.append(("verify", Path(marker)))
        if self.verify_error:
            raise self.verify_error
        return WeiboIdentity("123", "账号")

    def publish_status(self, text, *, image_path=""):
        self.calls.append(("publish", text, image_path))
        if self.publish_error:
            raise self.publish_error
        return self.outcome


class WeiboPublisherAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.marker = Path(self.temp_dir.name) / "weibo.json"
        self.marker.write_text("{}", encoding="utf-8")
        self.content = PublishContent(
            platform="weibo",
            title="企业接口说明",
            content="说明主体、来源与适用边界。",
            tags=("人工智能",),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_connector_returns_need_action(self):
        connector = _Connector(
            verify_error=WeiboConnectorUnavailableError("需安装/授权官方连接器")
        )
        result = WeiboPublisherAdapter(self.marker, connector=connector).publish(
            self.content
        )
        self.assertEqual(result.status, "need_action")
        self.assertFalse(result.success)
        self.assertIn("需安装/授权官方连接器", result.message)

    def test_success_requires_strict_public_weibo_url(self):
        connector = _Connector()
        result = WeiboPublisherAdapter(self.marker, connector=connector).publish(
            self.content
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        publish_call = connector.calls[-1]
        self.assertIn("企业接口说明\n\n说明主体", publish_call[1])
        self.assertIn("#人工智能#", publish_call[1])

        invalid = _Connector(outcome={"status": "success", "url": "https://weibo.com/u/1"})
        result = WeiboPublisherAdapter(self.marker, connector=invalid).publish(self.content)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "processing")

    def test_catalog_gap_is_manual_and_write_uncertainty_is_processing(self):
        catalog = _Connector(publish_error=WeiboCatalogError("命令目录未开放"))
        result = WeiboPublisherAdapter(self.marker, connector=catalog).publish(self.content)
        self.assertEqual(result.status, "need_action")

        uncertain = _Connector(
            publish_error=WeiboPublishUncertainError("平台最终状态未知；系统不会自动重试")
        )
        result = WeiboPublisherAdapter(self.marker, connector=uncertain).publish(
            self.content
        )
        self.assertEqual(result.status, "processing")
        self.assertIn("不会自动重试", result.message)

    def test_exit_zero_bad_write_json_stays_processing_without_retry(self):
        executable = Path(self.temp_dir.name) / "weibo.exe"
        executable.write_bytes(b"placeholder")
        writes = []

        def runner(command, **_kwargs):
            if command[1:3] == ["commands", "list"]:
                payload = '{"commands":[{"group":"statuses","action":"update/biz"}]}'
            elif command[1:3] == ["commands", "show"]:
                payload = (
                    '{"command":{"flags":['
                    '{"name":"text","type":"string"},'
                    '{"name":"aigc","type":"boolean"}]}}'
                )
            else:
                writes.append(command)
                payload = "not-json"
            return SimpleNamespace(returncode=0, stdout=payload, stderr="")

        connector = WeiboOfficialConnector(executable, runner=runner)
        connector.verify_marker = lambda _path: WeiboIdentity("123", "账号")
        result = WeiboPublisherAdapter(self.marker, connector=connector).publish(
            self.content
        )

        self.assertEqual(result.status, "processing")
        self.assertFalse(result.success)
        self.assertIn("不会自动重试", result.message)
        self.assertEqual(len(writes), 1)

    def test_multiple_images_are_rejected_before_connector_write(self):
        connector = _Connector()
        content = PublishContent(
            platform="weibo",
            title="标题",
            content="正文",
            images=("one.png", "two.png"),
        )
        result = WeiboPublisherAdapter(self.marker, connector=connector).publish(content)
        self.assertEqual(result.status, "need_action")
        self.assertFalse(any(call[0] == "publish" for call in connector.calls))


if __name__ == "__main__":
    unittest.main()
