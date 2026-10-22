import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from utils import base_social_media


class OptionalContextInitializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_desktop_resource_returns_plain_context_without_read_or_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            context = SimpleNamespace(add_init_script=AsyncMock())
            with patch.object(base_social_media, 'BASE_DIR', Path(directory)):
                result = await base_social_media.set_init_script(context)
            self.assertIs(result, context)
            context.add_init_script.assert_not_awaited()

    async def test_existing_legacy_resource_preserves_previous_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / 'utils/stealth.min.js'
            resource.parent.mkdir()
            resource.write_text('/* synthetic resource fixture */', encoding='utf-8')
            context = SimpleNamespace(add_init_script=AsyncMock())
            with patch.object(base_social_media, 'BASE_DIR', root):
                result = await base_social_media.set_init_script(context)
            self.assertIs(result, context)
            context.add_init_script.assert_awaited_once_with(path=resource)


if __name__ == '__main__':
    unittest.main()
