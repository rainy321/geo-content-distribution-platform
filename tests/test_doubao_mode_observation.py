"""Execute both real JS readers against a synthetic DOM in Node, without a browser."""
import json
import shutil
import subprocess
import unittest

from services.doubao_source_collector import (
    CollectorConfig, _LOGIN_OBSERVATION_JS, _VISIBLE_CLIENT_MODE_JS, read_snapshot,
    _read_login_observation,
)


NODE = shutil.which('node')
RUNNER = r'''
const vm = require('node:vm');
const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const node = item => ({
    textContent: item.label,
    disabled: item.disabled === true,
    getAttribute: name => name === 'aria-disabled' ? item.aria_disabled || null : null,
    getClientRects: () => item.rect === false ? [] : [{}],
    style: {display: item.display || 'block', visibility: item.visibility || 'visible'}
});
const nodes = data.dom.chips.map(node);
const composer = (data.dom.composer || []).map(node);
const document = {
    title: data.dom.title,
    body: {innerText: ''},
    querySelectorAll: selector => selector === '[data-testid="workspace-switch-chip"]' ? nodes
        : selector === '[data-testid="chat_input_action_mode"]' ? composer : []
};
const url = new URL(data.dom.url);
const context = vm.createContext({document, location: url, URLSearchParams,
    getComputedStyle: node => node.style});
const fn = vm.runInContext('(' + data.script + ')', context, {timeout: 1000});
process.stdout.write(JSON.stringify(fn(data.payload)));
'''


class NodePage:
    """Only executes collector JS; no Playwright, network, or persistent profile."""
    def __init__(self, *, chips=(), composer=(), title='豆包', url='https://www.doubao.com/chat/'):
        self.dom = {'chips': list(chips), 'composer': list(composer), 'title': title, 'url': url}
        self.calls = []

    async def evaluate(self, script, payload):
        self.calls.append(script)
        process = subprocess.run(
            [NODE, '-e', RUNNER],
            input=json.dumps({'dom': self.dom, 'script': script, 'payload': payload}, ensure_ascii=False),
            text=True, encoding='utf-8', capture_output=True, check=True, timeout=5,
        )
        return json.loads(process.stdout)


@unittest.skipUnless(NODE, 'Node is required to execute the actual JS readers offline')
class ConservativeModeTests(unittest.IsolatedAsyncioTestCase):
    async def assert_modes(self, expected, **dom):
        for strict in (False, True):
            with self.subTest(strict=strict, dom=dom):
                page = NodePage(**dom)
                value = await read_snapshot(page, CollectorConfig(strict_client_mode=strict))
                self.assertEqual(value['client_mode'], expected)
                self.assertEqual(value['answer'], '')
                self.assertFalse(value['question_verified'])
                self.assertEqual(len(page.calls), 2 if strict else 1)

    async def test_missing_chip_is_unknown_in_legacy_and_strict_paths(self):
        await self.assert_modes(None)

    async def test_work_title_without_chip_is_still_unknown(self):
        await self.assert_modes(None, title='豆包工作 - 工作新习惯')

    async def test_empty_chip_with_work_title_is_unknown(self):
        for label in ('', ' \n\t '):
            await self.assert_modes(None, chips=[{'label': label}], title='豆包工作')

    async def test_hidden_work_chip_is_unknown(self):
        for change in ({'rect': False}, {'display': 'none'}, {'visibility': 'hidden'}):
            await self.assert_modes(None, chips=[{'label': '豆包工作', **change}])

    async def test_hidden_consumer_chip_is_unknown(self):
        await self.assert_modes(None, chips=[{'label': '豆包', 'rect': False}])

    async def test_unrecognized_chip_never_defaults_to_consumer_or_work(self):
        for title in ('豆包', '豆包工作'):
            await self.assert_modes(None, chips=[{'label': '加载中'}], title=title)

    async def test_existing_work_label_semantics_are_preserved(self):
        for label in ('豆包工作', '豆包 \n 工作'):
            await self.assert_modes('work', chips=[{'label': label}], title='豆包')

    async def test_existing_consumer_label_semantics_do_not_use_conflicting_title(self):
        # Enabled legacy chips remain compatible; the new disabled brand is
        # covered separately and cannot establish the composer mode.
        for label in ('豆包', '豆 包', '豆包对话'):
            await self.assert_modes('consumer', chips=[{'label': label}], title='豆包工作')

    async def test_visible_chip_is_not_overridden_by_a_hidden_other_mode(self):
        await self.assert_modes('consumer', chips=[{'label': '豆包工作', 'rect': False}, {'label': '豆包'}])
        await self.assert_modes('work', chips=[{'label': '豆包', 'display': 'none'}, {'label': '豆包工作'}])

    async def test_strict_origin_guard_remains_strict(self):
        for url in ('https://doubao.com.evil.example/chat/', 'http://www.doubao.com/chat/',
                    'https://www.doubao.com:8443/chat/', 'https://www.doubao.com/chatbot'):
            page = NodePage(chips=[{'label': '豆包工作'}], url=url)
            value = await read_snapshot(page, CollectorConfig(strict_client_mode=True))
            self.assertIsNone(value['client_mode'])

    async def test_login_reader_does_not_claim_authentication_from_unknown_mode(self):
        page = NodePage(title='豆包工作')
        self.assertEqual(await _read_login_observation(page, CollectorConfig()), {
            'client_mode': None, 'login_required': None, 'challenge': None,
        })

    async def test_new_ui_disabled_brand_and_work_shell_without_composer_is_unknown(self):
        # Observed anonymous work tab: generic title and disabled plain brand,
        # with both sidebar entries present. Neither establishes current mode.
        await self.assert_modes(None, chips=[{'label': '豆包', 'aria_disabled': 'true'}],
                                title='豆包 - 字节跳动旗下 AI 智能助手')

    async def test_new_ui_disabled_brand_and_explicit_dialogue_composer_is_consumer(self):
        await self.assert_modes('consumer', chips=[{'label': '豆包', 'aria_disabled': 'true'}],
                                composer=[{'label': '对话'}])

    async def test_disabled_brand_and_explicit_work_composer_is_work(self):
        await self.assert_modes('work', chips=[{'label': '豆包', 'aria_disabled': 'true'}],
                                composer=[{'label': '工 作'}])

    async def test_native_disabled_plain_brand_also_cannot_identify_consumer(self):
        await self.assert_modes(None, chips=[{'label': '豆包', 'disabled': True}])

    async def test_unknown_or_empty_visible_composer_never_falls_back_to_legacy_chip(self):
        for chip in ('豆包对话', '豆包工作'):
            for mode in ('', '加载中', '其他模式'):
                await self.assert_modes(None, chips=[{'label': chip}], composer=[{'label': mode}])

    async def test_hidden_composer_cannot_identify_mode_with_disabled_brand(self):
        for change in ({'rect': False}, {'display': 'none'}, {'visibility': 'hidden'}):
            await self.assert_modes(None, chips=[{'label': '豆包', 'aria_disabled': 'true'}],
                                    composer=[{'label': '对话', **change}])

    async def test_explicit_composer_conflict_with_known_chip_is_unknown(self):
        await self.assert_modes(None, chips=[{'label': '豆包工作'}], composer=[{'label': '对话'}])
        await self.assert_modes(None, chips=[{'label': '豆包对话'}], composer=[{'label': '工作'}])

    async def test_matching_explicit_chip_and_composer_keep_the_mode(self):
        await self.assert_modes('consumer', chips=[{'label': '豆包对话'}], composer=[{'label': '对话'}])
        await self.assert_modes('work', chips=[{'label': '豆包工作'}], composer=[{'label': '工作'}])

    async def test_multiple_visible_composer_modes_or_workspace_modes_are_unknown(self):
        await self.assert_modes(None, composer=[{'label': '对话'}, {'label': '工作'}])
        await self.assert_modes(None, composer=[{'label': '对话'}, {'label': '加载中'}])
        await self.assert_modes(None, chips=[{'label': '豆包对话'}, {'label': '豆包工作'}])

    async def test_hidden_conflicting_chip_does_not_override_visible_composer(self):
        await self.assert_modes('consumer', chips=[{'label': '豆包工作', 'rect': False}],
                                composer=[{'label': '对话'}])

    async def test_observed_cloud_computer_tool_preserves_explicit_work_chip(self):
        await self.assert_modes('work', chips=[{'label': '豆包\n工作', 'aria_disabled': 'true'}],
                                composer=[{'label': '云电脑'}])

    async def test_cloud_computer_alone_brand_or_consumer_chip_cannot_prove_work(self):
        for chips in ([], [{'label': '豆包', 'aria_disabled': 'true'}],
                      [{'label': '豆包'}], [{'label': '豆包对话'}]):
            await self.assert_modes(None, chips=chips, composer=[{'label': '云电脑'}])

    async def test_only_all_visible_exact_cloud_computer_labels_can_use_work_compatibility(self):
        await self.assert_modes('work', chips=[{'label': '豆包工作'}],
                                composer=[{'label': '云电脑'}, {'label': '云电脑'}])
        for other in ('对话', '工作', '加载中', '云电脑工具', ''):
            await self.assert_modes(None, chips=[{'label': '豆包工作'}],
                                    composer=[{'label': '云电脑'}, {'label': other}])

    async def test_hidden_work_chip_does_not_authorize_cloud_computer_compatibility(self):
        await self.assert_modes(None, chips=[{'label': '豆包工作', 'visibility': 'hidden'}],
                                composer=[{'label': '云电脑'}])

    def test_both_reader_scripts_embed_the_same_existing_label_classifier(self):
        from services.doubao_source_collector import SNAPSHOT_JS
        self.assertIn(_VISIBLE_CLIENT_MODE_JS, SNAPSHOT_JS)
        self.assertIn(_VISIBLE_CLIENT_MODE_JS, _LOGIN_OBSERVATION_JS)
        self.assertNotIn('__VISIBLE_CLIENT_MODE__', SNAPSHOT_JS + _LOGIN_OBSERVATION_JS)


if __name__ == '__main__':
    unittest.main()
