import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'

test('real Chromium Window timers resolve and time out through the actual Bing helper and component wait function', { timeout: 30000 }, () => {
  const source = fs.readFileSync(new URL('../src/components/GeoBingInspection.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'native-window-timer-contract' })
  const declaration = script.scriptSetupAst.find(node => node.type === 'VariableDeclaration' && node.declarations.some(item => item.id.name === 'waitFor'))
  assert.ok(declaration, 'Actual component waitFor must be compiled, not reimplemented by the test')
  const waitFor = descriptor.scriptSetup.content.slice(declaration.start, declaration.end)
  const workspace = fileURLToPath(new URL('../../', import.meta.url))
  const venvPython = path.join(workspace, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const executable = fs.existsSync(venvPython) ? venvPython : 'python'
  const child = spawnSync(executable, [fileURLToPath(new URL('./browser/bing_timer_browser.py', import.meta.url))], {
    input: JSON.stringify({ utils: fileURLToPath(new URL('../src/utils/', import.meta.url)), waitFor }),
    encoding: 'utf8', timeout: 25000, windowsHide: true
  })
  assert.equal(child.error, undefined, child.error?.message)
  assert.equal(child.status, 0, child.stderr || child.stdout)
  const result = JSON.parse(child.stdout)
  assert.deepEqual(result.cases, ['default-resolve', 'default-timeout', 'component-resolve', 'component-timeout'])
  assert.equal(result.old_receiver_failure_reproduced, true)
  assert.equal(result.page_errors.length, 0)
  assert.equal(result.external_requests, 0)
})
