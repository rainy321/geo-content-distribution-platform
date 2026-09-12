import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(
  new URL('../src/App.vue', import.meta.url),
  'utf8'
)
const accountSource = readFileSync(
  new URL('../src/views/AccountManagement.vue', import.meta.url),
  'utf8'
)

test('account risk alerts use a dedicated read-only account focus route', () => {
  const destinationStart = appSource.indexOf('const openAlertDestination = alert =>')
  const destinationEnd = appSource.indexOf('\n\nconst fetchReviewAlerts', destinationStart)
  const destination = appSource.slice(destinationStart, destinationEnd)
  assert.match(destination, /focus_account_id: String\(alert\.account_id\)/)
  assert.doesNotMatch(destination, /\{ account_id:/)
})

test('account focus refreshes, highlights and scrolls on initial and same-page navigation', () => {
  assert.match(accountSource, /await focusExistingAccount\(route\.query\.focus_account_id, \{ refresh: false \}\)/)
  assert.match(accountSource, /watch\(\s*\(\) => route\.query\.focus_account_id/)
  assert.match(accountSource, /if \(refresh\) await fetchOverview\(\)/)
  assert.match(accountSource, /id: `media-account-\$\{account\.id\}`/)
  assert.match(accountSource, /'is-focused': focused/)
  assert.match(accountSource, /accountElement\.scrollIntoView/)
  assert.match(accountSource, /未找到媒体账号 ID \$\{accountId\}；该账号可能已解绑或不再存在/)
})

test('account focus cannot enter login, recovery, replacement or disconnect flows', () => {
  const handlerStart = accountSource.indexOf('const focusExistingAccount = async')
  const handlerEnd = accountSource.indexOf('\n\nonMounted(', handlerStart)
  const handler = accountSource.slice(handlerStart, handlerEnd)
  assert.match(handler, /focusedAccountId\.value = accountId/)
  assert.match(handler, /await fetchOverview\(\)/)
  assert.doesNotMatch(handler, /openLogin|recoverAccount|replaceAccountBinding|reconnectAccount|disconnectAccount/)
})
