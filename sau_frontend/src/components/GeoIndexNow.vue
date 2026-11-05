<template>
  <section class="indexnow" aria-label="官网更新通知" data-testid="indexnow">
    <header><div><h3>把已更新的页面通知搜索引擎</h3><p>先检查公开页面，再明确提交一次 IndexNow 更新通知。</p></div><el-button text :disabled="locked" data-testid="indexnow-refresh" @click="refresh">检查原记录</el-button></header>
    <p v-if="error" class="indexnow-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="indexnow-note" role="status">正在读取当前项目的通知记录…</p>
    <div v-if="assets.length > 1 || selectedAssetId" class="indexnow-asset">
      <label :for="`indexnow-asset-${cycle.id}`">通知哪个官网地址</label>
      <select :id="`indexnow-asset-${cycle.id}`" :value="selectedAssetId" :disabled="locked || Boolean(frozen)" data-testid="indexnow-asset" @change="selectAsset($event.target.value)">
        <option v-if="!selectedAssetAvailable" :value="selectedAssetId" disabled>原地址已不在当前范围，请切回主官网</option>
        <option v-for="asset in assets" :key="asset.id || 'primary'" :value="asset.primary ? '' : asset.id">{{ asset.primary ? '主官网' : '其他已登记官网' }} · {{ asset.page_scope }}</option>
      </select>
      <p class="indexnow-note">已登记地址不代表控制权已验证。每个所选主机都需单独检查它自己公开的验证文件。</p>
    </div>
    <p v-if="!view.supported" class="indexnow-note">{{ view.reason }}</p>
    <template v-else>
      <p class="indexnow-scope"><strong>{{ projectName }}</strong><span>本轮 {{ cycle.id }}</span><span>{{ view.page_scope }}</span></p>
      <p v-if="frozen" class="indexnow-error" role="status" data-testid="indexnow-uncertain">上次通知回执尚未确认，已保留同次编号。请检查原记录，不能再次提交。<small>编号 {{ frozen.payload.request_id }}</small></p>
      <details class="indexnow-editor">
        <summary>准备页面更新通知 · 每次最多 3 页</summary>
        <div class="indexnow-form">
          <div class="indexnow-actions"><el-button :disabled="locked || Boolean(frozen)" data-testid="indexnow-generate-key" @click="generateKey">生成验证文件</el-button><el-button v-if="generatedKey" :disabled="locked || Boolean(frozen)" data-testid="indexnow-download-key" @click="downloadKey">下载验证文件</el-button><el-button v-if="generatedKey" :disabled="locked || Boolean(frozen)" data-testid="indexnow-copy-key-instructions" @click="copyKeyInstructions">复制部署说明</el-button></div>
          <p v-if="keyDraftWarning" class="indexnow-error" role="alert" data-testid="indexnow-key-draft-warning">{{ keyDraftWarning }}</p>
          <div v-if="generatedKey" class="indexnow-key-file" data-testid="indexnow-generated-key">
            <p><strong>本机验证文件草稿 · 部署与控制权需核对</strong> · <code>{{ generatedKey.filename }}</code></p>
            <textarea :value="generatedKey.instructions" readonly rows="4" aria-label="验证文件部署说明" />
          </div>
          <p class="indexnow-note">没有验证文件时可在本机生成并交给网站负责人上传。这是搜索引擎控制权验证文字，不是模型 API 密钥。生成和下载不会部署文件或发送通知。</p>
          <label :for="`indexnow-key-${cycle.id}`">公开 IndexNow key 文件地址</label>
          <input :id="`indexnow-key-${cycle.id}`" v-model="keyLocation" type="url" :disabled="locked || Boolean(frozen)" :placeholder="`${view.site_origin}/你的公开key.txt`" data-testid="indexnow-key"/>
          <p class="indexnow-note">填已部署到官网的公开 key 文件地址，不填模型 API 密钥。文件需无需登录即可读取，内容与文件名中的 key 一致。</p>
          <label :for="`indexnow-urls-${cycle.id}`">本次真正更新过的页面，每行一个</label>
          <textarea :id="`indexnow-urls-${cycle.id}`" v-model="urlText" rows="3" :disabled="locked || Boolean(frozen)" :placeholder="view.page_scope" data-testid="indexnow-urls"/>
          <div class="indexnow-actions"><el-button :disabled="locked || Boolean(frozen)" :loading="pending==='preview'" data-testid="indexnow-preview" @click="preflight">检查页面和内容变化</el-button><span class="indexnow-note">匿名读取 key 与页面，计算 HTML 哈希；这一步不发送更新通知。</span></div>
        </div>
      </details>
      <Transition name="indexnow-reveal">
        <div v-if="preview" class="indexnow-preview" data-testid="indexnow-preview-result">
          <div class="indexnow-result-heading"><strong>{{ preview.ready ? `${preview.changed_urls.length} 个页面可通知` : '没有可通知的新内容' }}</strong><span>预检有效至 {{ date(preview.expires_at) }}</span></div>
          <article v-for="page in preview.pages" :key="page.url" class="indexnow-page"><p>{{ page.url }}</p><span>{{ preview.changed_urls.includes(page.url) ? '可提交' : '已拦截相同内容' }} · {{ page.bytes }} 字节</span><details><summary>查看内容指纹</summary><code>{{ page.sha256 }}</code></details></article>
          <p v-for="item in preview.blocked_urls || []" :key="item.url" class="indexnow-note">{{ item.url }}：{{ item.reason }}（{{ indexNowStatus(item.status) }}）</p>
          <template v-if="preview.ready"><label class="indexnow-confirm"><input v-model="updatedConfirmed" type="checkbox" :disabled="locked || Boolean(frozen)" data-testid="indexnow-confirm"/>这些页面确实已更新，本次只通知上方可提交的页面</label><p v-if="preview.retry_receipt_id" class="indexnow-note">上次通知被明确拒绝；本次重新预检后，可明确重试该条拒绝记录。</p><el-button :disabled="locked || !updatedConfirmed || Boolean(frozen)" :loading="pending==='submit'" data-testid="indexnow-submit" @click="submit">{{ preview.retry_receipt_id ? '重试明确拒绝的通知' : '提交这次更新通知' }}</el-button></template>
        </div>
      </Transition>
      <p v-if="notice" class="indexnow-note" role="status" aria-live="polite">{{ notice }}</p>
      <details v-if="view.notifications.length" class="indexnow-history" open><summary>本项目本轮的 {{ view.notifications.length }} 条通知记录</summary><article v-for="item in view.notifications" :key="item.id" class="indexnow-receipt"><div><strong>{{ indexNowStatus(item.status) }}</strong><span>{{ date(item.completed_at || item.created_at) }}</span></div><p class="indexnow-note">通知主机：{{ indexNowReceiptOrigin(item) }}</p><p v-if="item.context_sha256!==view.context_sha256" class="indexnow-note">历史资料范围</p><p v-for="url in item.urls || []" :key="url">{{ url }}</p><details><summary>查看通知编号</summary><code>{{ item.request_id }}</code><p v-if="item.http_status">HTTP {{ item.http_status }}</p></details></article></details>
      <p v-else class="indexnow-note">本轮尚无更新通知记录。</p>
      <p class="indexnow-footnote">200 只表示通知已接收；202 表示 key 校验中。通知、抓取、索引与豆包采用分别核对，均不据此认定 GEO 优化成功。结果未知或相同内容已有通知时，不重复发送。</p>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { geoIndexNowApi } from '@/api/geoIndexNow'
import { indexNowOwner, indexNowAssets, indexNowOverview, indexNowReadAssetSelection, indexNowSaveAssetSelection, indexNowKeyFile, indexNowKeyDraftStorageKey, readValidatedKeyDraft, saveValidatedKeyDraft, downloadIndexNowKeyFile, indexNowReceiptOrigin, indexNowPreviewPayload, indexNowPreview, indexNowSubmission, indexNowReceiptMatches, indexNowStorageKey, indexNowRestore, indexNowStatus, indexNowError, indexNowUncertain } from '@/utils/geoIndexNow'
import { journeyDate } from '@/utils/geoJourney'

const props = defineProps({ cycle: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['busy'])
const selectedAssetId = ref(''), rawOverview = ref(props.cycle.indexnow), generatedKey = ref(null)
const keyDraftScope = ref(''), keyDraftRestoreTried = ref(''), keyDraftWarning = ref('')
const view = ref(indexNowOverview(props.cycle, props.cycle.indexnow)), keyLocation = ref(''), urlText = ref(''), preview = ref(null)
const pending = ref(''), loading = ref(false), error = ref(''), notice = ref(''), updatedConfirmed = ref(false), frozen = ref(null)
const localBusy = computed(() => Boolean(pending.value) || loading.value), locked = computed(() => props.busy || localBusy.value)
const projectName = computed(() => (props.cycle.profile || props.cycle.baseline?.profile)?.name || '当前项目')
const assets = computed(() => indexNowAssets(props.cycle, rawOverview.value))
const selectedAssetAvailable = computed(() => !selectedAssetId.value || assets.value.some(asset => !asset.primary && asset.id === selectedAssetId.value))
const date = value => journeyDate(value)
let generation = 0, readVersion = 0, formVersion = 0, disposed = false
const owner = () => JSON.stringify([indexNowOwner(props.cycle), selectedAssetId.value])
const active = (epoch, key) => !disposed && epoch === generation && key === owner()
const storage = () => { try { return globalThis.localStorage } catch { return null } }
selectedAssetId.value = indexNowReadAssetSelection(storage(), props.cycle)
const clearPending = () => {
  try { const store = storage(), saved = indexNowRestore(store, view.value); if (saved?.payload.request_id === frozen.value?.payload.request_id) store.removeItem(indexNowStorageKey(view.value)) } catch { /* Keep the server ledger authoritative. */ }
  frozen.value = null
}
const restoreKeyDraft = allow => {
  const scope = indexNowKeyDraftStorageKey(view.value)
  if (generatedKey.value && keyDraftScope.value !== scope) {
    if (keyLocation.value === generatedKey.value.key_location) keyLocation.value = ''
    generatedKey.value = null; keyDraftScope.value = ''
  }
  if (!scope || generatedKey.value || keyDraftRestoreTried.value === scope) return
  keyDraftRestoreTried.value = scope
  if (!allow || keyLocation.value) return
  const saved = readValidatedKeyDraft(storage(), view.value)
  if (saved) {
    generatedKey.value = saved; keyLocation.value = saved.key_location; keyDraftScope.value = scope
    notice.value = '已恢复本机验证文件草稿；部署与控制权需另行核对，恢复不会发送通知。'
  }
}
const apply = (source, allowDraftRestore = true) => {
  rawOverview.value = source
  view.value = indexNowOverview(props.cycle, source, selectedAssetId.value)
  restoreKeyDraft(allowDraftRestore)
  if (!view.value.supported) return
  frozen.value = indexNowRestore(storage(), view.value)
  if (frozen.value && view.value.notifications.some(item => indexNowReceiptMatches(item, frozen.value))) clearPending()
}
const refresh = async () => {
  if (disposed || locked.value) return
  const epoch = generation, key = owner(), ticket = ++readVersion, form = formVersion
  loading.value = true; error.value = ''
  try {
    const reply = await (selectedAssetId.value ? geoIndexNowApi.get(props.cycle.id, selectedAssetId.value) : geoIndexNowApi.get(props.cycle.id))
    if (!active(epoch, key) || ticket !== readVersion) return
    if (reply?.code !== 200) throw new Error('未能读取当前项目的通知记录。')
    apply(reply.data, form === formVersion)
    if (preview.value && preview.value.context_sha256 !== view.value.context_sha256) preview.value = null
  } catch (cause) { if (active(epoch, key) && ticket === readVersion) error.value = indexNowError(cause) }
  finally { if (active(epoch, key) && ticket === readVersion) loading.value = false }
}
const resetEditor = () => {
  keyLocation.value = ''; urlText.value = ''; preview.value = null; generatedKey.value = null
  keyDraftScope.value = ''; keyDraftRestoreTried.value = ''; keyDraftWarning.value = ''
  error.value = ''; notice.value = ''; updatedConfirmed.value = false
}
const selectAsset = async value => {
  if (disposed || locked.value || frozen.value || value === selectedAssetId.value) return
  const asset = assets.value.find(item => (item.primary ? '' : item.id) === value)
  if (!asset) { error.value = '该地址不属于本轮已登记官网，未切换。'; return }
  try { indexNowSaveAssetSelection(storage(), props.cycle, asset) }
  catch (cause) { error.value = indexNowError(cause); return }
  generation++; readVersion++; selectedAssetId.value = value; resetEditor()
  view.value = indexNowOverview(props.cycle, rawOverview.value, value)
  await refresh()
}
const generateKey = () => {
  if (disposed || locked.value || frozen.value) return
  try {
    const candidate = indexNowKeyFile(view.value, globalThis.crypto)
    const saved = saveValidatedKeyDraft(storage(), view.value, candidate)
    generatedKey.value = saved; keyLocation.value = saved.key_location; error.value = ''; keyDraftWarning.value = ''
    keyDraftScope.value = keyDraftRestoreTried.value = indexNowKeyDraftStorageKey(view.value)
    notice.value = '验证文件草稿已保存到本机；未自动部署或发送通知，部署与控制权需核对。'
  } catch (cause) { keyDraftWarning.value = `验证文件草稿保存未确认。${indexNowError(cause)} 可下载当前已有文件；未发送通知。`; notice.value = '' }
}
const downloadKey = () => {
  if (disposed || locked.value || frozen.value || !generatedKey.value) return
  try { downloadIndexNowKeyFile(generatedKey.value, globalThis); error.value = ''; notice.value = '验证文件已交给浏览器下载，可按需交给网站负责人核对。' }
  catch (cause) { error.value = indexNowError(cause) }
}
const copyKeyInstructions = async () => {
  if (disposed || locked.value || frozen.value || !generatedKey.value) return
  const epoch = generation, key = owner(), file = generatedKey.value
  try {
    if (!globalThis.navigator?.clipboard?.writeText) throw new Error('当前窗口不能复制，请直接选中上方部署说明。')
    await globalThis.navigator.clipboard.writeText(file.instructions)
    if (active(epoch, key)) notice.value = '部署说明已复制。'
  } catch (cause) { if (active(epoch, key)) error.value = indexNowError(cause) }
}
const preflight = async () => {
  if (disposed || locked.value || frozen.value) return
  let payload
  try { payload = indexNowPreviewPayload(view.value, keyLocation.value, urlText.value) }
  catch (cause) { error.value = indexNowError(cause); return }
  const epoch = generation, key = owner(), cycleId = props.cycle.id, form = formVersion
  pending.value = 'preview'; preview.value = null; updatedConfirmed.value = false; error.value = ''; notice.value = ''
  try {
    const reply = await (selectedAssetId.value ? geoIndexNowApi.preview(cycleId, payload, selectedAssetId.value) : geoIndexNowApi.preview(cycleId, payload))
    if (!active(epoch, key)) return
    if (form !== formVersion) throw new Error('输入已变化，旧预检不再使用，请重新检查。')
    if (reply?.code !== 200) throw new Error('未取得可核对的预检回执，没有发送通知。')
    apply(reply.data?.overview)
    preview.value = indexNowPreview(reply.data?.preview, view.value, payload)
  } catch (cause) { if (active(epoch, key)) error.value = indexNowError(cause) }
  finally { if (active(epoch, key)) pending.value = '' }
}
const submit = async () => {
  if (disposed || locked.value || frozen.value || !updatedConfirmed.value) return
  let captured
  try {
    const requestId = globalThis.crypto?.randomUUID?.()
    if (!requestId) throw new Error('当前浏览器无法生成本次通知编号，请更新软件后再提交。')
    captured = indexNowSubmission(view.value, preview.value, requestId)
    const store = storage()
    if (!store) throw new Error('未能保留本次通知编号，请检查本地存储后再提交。')
    store.setItem(indexNowStorageKey(view.value), JSON.stringify(captured))
    if (indexNowRestore(store, view.value)?.payload.request_id !== requestId) throw new Error('未能保留本次通知编号，尚未提交。')
  } catch (cause) { error.value = indexNowError(cause); return }
  const epoch = generation, key = owner()
  frozen.value = captured; pending.value = 'submit'; error.value = ''; notice.value = ''
  try {
    const reply = await (captured.asset_id ? geoIndexNowApi.submit(captured.cycle_id, captured.payload, captured.asset_id) : geoIndexNowApi.submit(captured.cycle_id, captured.payload))
    if (!active(epoch, key)) return
    if (reply?.code !== 200 || !indexNowReceiptMatches(reply.data?.receipt, captured)) throw new Error('通知回执不完整，请检查原记录，不能重发。')
    apply(reply.data.overview)
    if (!view.value.supported) throw new Error('本项目范围已变化，请刷新原记录核对。')
    clearPending(); preview.value = null; updatedConfirmed.value = false
    notice.value = indexNowStatus(reply.data.receipt.status)
  } catch (cause) {
    if (active(epoch, key)) {
      if (!indexNowUncertain(cause)) { clearPending(); preview.value = null; updatedConfirmed.value = false }
      error.value = indexNowError(cause)
    }
  } finally { if (active(epoch, key)) pending.value = '' }
}
apply(props.cycle.indexnow)
watch([keyLocation, urlText], () => { formVersion++; preview.value = null; updatedConfirmed.value = false; notice.value = '' }, { flush: 'sync' })
watch(localBusy, value => emit('busy', value), { immediate: true, flush: 'sync' })
watch(() => indexNowOwner(props.cycle), () => {
  generation++; readVersion++; pending.value = ''; loading.value = false; resetEditor()
  selectedAssetId.value = indexNowReadAssetSelection(storage(), props.cycle)
  error.value = ''; notice.value = ''; updatedConfirmed.value = false; frozen.value = null; apply(props.cycle.indexnow)
  void refresh()
}, { flush: 'sync' })
watch(() => props.busy, value => { if (!value && !view.value.supported && !localBusy.value && !disposed) void refresh() })
onMounted(() => { void refresh() })
onUnmounted(() => { disposed = true; generation++; readVersion++; emit('busy', false) })
</script>

<style scoped>
.indexnow-asset{margin:12px 0}.indexnow select{display:block;box-sizing:border-box;max-width:100%;width:100%;margin:6px 0;padding:9px 11px;border:1px solid #cedcd7;border-radius:7px;color:#173e3b;background:#fff;font:inherit}.indexnow select:focus-visible{outline:2px solid #0a756e;outline-offset:2px}.indexnow-key-file{margin:12px 0;overflow-wrap:anywhere}.indexnow-key-file p{margin:0}
.indexnow{margin-top:20px;padding-top:18px;border-top:1px solid #dce6e2;font-size:12px;line-height:1.8}.indexnow header,.indexnow-result-heading,.indexnow-receipt>div{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.indexnow h3{font-size:15px;margin:0}.indexnow header p,.indexnow-note,.indexnow-footnote,.indexnow-result-heading span,.indexnow-receipt>div>span{margin:6px 0;color:var(--geo-muted,#63736f)}.indexnow-scope{display:flex;flex-wrap:wrap;gap:3px 16px;overflow-wrap:anywhere}.indexnow-scope span:last-child{flex-basis:100%}.indexnow summary{cursor:pointer;color:var(--geo-primary,#0a756e);padding:6px 0}.indexnow-editor{margin:10px 0}.indexnow-form,.indexnow-preview{padding:16px;background:#f7faf8;border-radius:9px}.indexnow label{display:block}.indexnow input[type=url],.indexnow textarea{display:block;box-sizing:border-box;width:100%;margin:6px 0 12px;padding:9px 11px;border:1px solid #cedcd7;border-radius:7px;color:#173e3b;background:#fff;font:inherit}.indexnow input:focus-visible,.indexnow textarea:focus-visible,.indexnow summary:focus-visible{outline:2px solid #0a756e;outline-offset:2px}.indexnow-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px}.indexnow-page,.indexnow-receipt{padding:12px 0;border-bottom:1px solid #dce6e2;overflow-wrap:anywhere}.indexnow-page p,.indexnow-receipt p{margin:3px 0}.indexnow code{font-size:11px;overflow-wrap:anywhere;white-space:pre-wrap}.indexnow .indexnow-confirm{display:flex;gap:8px;align-items:flex-start;margin:14px 0}.indexnow-confirm input{margin-top:6px;flex:none}.indexnow-error{padding:9px 12px;background:#fff5ec;border-radius:7px;color:#995c2d;overflow-wrap:anywhere}.indexnow-error small{display:block}.indexnow-history{margin-top:16px}.indexnow-footnote{margin-bottom:0}.indexnow-reveal-enter-active{transition:opacity 180ms ease,transform 240ms cubic-bezier(.2,.8,.2,1)}.indexnow-reveal-leave-active{transition:opacity 100ms ease}.indexnow-reveal-enter-from{opacity:0;transform:translateY(5px)}.indexnow-reveal-leave-to{opacity:0}@media(max-width:650px){.indexnow header{gap:8px;flex-wrap:wrap}.indexnow-form,.indexnow-preview{padding:12px}.indexnow-result-heading,.indexnow-receipt>div{flex-direction:column;gap:2px}.indexnow-actions{align-items:stretch;flex-direction:column}}@media(prefers-reduced-motion:reduce){.indexnow-reveal-enter-active,.indexnow-reveal-leave-active{transition:none}.indexnow-reveal-enter-from{transform:none}}
.indexnow details[open]>:not(summary){animation:indexnow-open 180ms ease-out}@keyframes indexnow-open{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){.indexnow details[open]>:not(summary){animation:none}}
</style>
