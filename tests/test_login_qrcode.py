import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.login_qrcode import print_terminal_qrcode


class _FakeQrcode:
    matrix = ((True, False), (False, True))

    def __init__(self, terminal_error=None):
        self.terminal_error = terminal_error
        self.terminal_calls = []

    def terminal(self, **kwargs):
        self.terminal_calls.append(kwargs)
        if self.terminal_error is not None:
            raise self.terminal_error
        kwargs["out"].write("<unicode-qr>\n")


class _UnicodeRejectingStdout:
    def __init__(self, reconfigure_error=None):
        self.written = []
        self.reconfigure_calls = []
        self.reconfigure_error = reconfigure_error

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)
        if self.reconfigure_error is not None:
            raise self.reconfigure_error

    def write(self, text):
        text.encode("ascii")
        self.written.append(text)


class LoginQrcodeConsoleSafetyTests(unittest.TestCase):
    def test_windowed_runtime_without_stdout_does_not_fail(self):
        qrcode = _FakeQrcode()

        with patch.object(sys, "stdout", None), patch(
            "utils.login_qrcode.segno.make", return_value=qrcode
        ) as make:
            print_terminal_qrcode(
                "private-login-token", Path("login.png"), "小红书APP"
            )

        make.assert_called_once_with(
            "private-login-token", error="L", boost_error=False
        )
        self.assertEqual([], qrcode.terminal_calls)

    def test_unicode_write_and_terminal_failures_fall_back_without_leaking_payload(self):
        stream = _UnicodeRejectingStdout()
        qrcode = _FakeQrcode(
            terminal_error=UnicodeEncodeError("gbk", "█", 0, 1, "unsupported")
        )

        with patch.object(sys, "stdout", stream), patch(
            "utils.login_qrcode.segno.make", return_value=qrcode
        ):
            print_terminal_qrcode(
                "COOKIE=do-not-log", Path("login.png"), "快手APP", compact=False, border=2
            )

        output = "".join(stream.written)
        self.assertIn("##", output)
        self.assertNotIn("COOKIE=do-not-log", output)
        self.assertEqual(
            [{"encoding": "utf-8", "errors": "replace"}],
            stream.reconfigure_calls,
        )
        self.assertEqual(
            [{"out": stream, "compact": False, "border": 2}],
            qrcode.terminal_calls,
        )

    def test_reconfigure_oserror_is_non_fatal(self):
        stream = _UnicodeRejectingStdout(
            reconfigure_error=OSError("stream cannot be reconfigured")
        )
        qrcode = _FakeQrcode()

        with patch.object(sys, "stdout", stream), patch(
            "utils.login_qrcode.segno.make", return_value=qrcode
        ):
            print_terminal_qrcode(
                "private-login-token", Path("saved-login.png"), "微信"
            )

        self.assertIn("##", "".join(stream.written))
        self.assertEqual([], qrcode.terminal_calls)

    def test_writable_console_keeps_terminal_qrcode_output(self):
        stream = io.StringIO()
        qrcode = _FakeQrcode()

        with patch.object(sys, "stdout", stream), patch(
            "utils.login_qrcode.segno.make", return_value=qrcode
        ):
            print_terminal_qrcode(
                "private-login-token", Path("saved-login.png"), "微信"
            )

        output = stream.getvalue()
        self.assertIn("请使用微信扫描下方二维码登录", output)
        self.assertIn("<unicode-qr>", output)
        self.assertIn("saved-login.png", output)
        self.assertNotIn("private-login-token", output)
        self.assertEqual(
            [{"out": stream, "compact": True, "border": 0}],
            qrcode.terminal_calls,
        )


if __name__ == "__main__":
    unittest.main()
