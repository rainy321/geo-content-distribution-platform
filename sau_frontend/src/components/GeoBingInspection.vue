<template>
  <section v-if="available || rows.length || frozen" class="bing-window" aria-label="普通浏览器检查页观察" data-testid="bing-window">
    <div class="bing-actions"><el-button v-if="available" :disabled="locked || !validUrl || Boolean(frozen) || readingRecord" :loading="pending" data-testid="bing-window-read" @click="readWindow">读取普通浏览器检查页</el-button><el-button text :disabled="locked" data-testid="bing-window-refresh" @click="refresh">刷新读取记录</el-button></div>
    <template v-if="available">
      <p class="bing-note" data-testid="bing-window-target">本次将读取：<strong>{{ target || '请核对当前官网的完整网址' }}</strong><span v-if="!url.trim()">（当前项目地址）</span></p>
      <p class="bing-note">先在日常 Chrome / Edge 登录 Bing，检查“本次将读取”的同一网址，并保持检查页窗口打开。这里只读取当前已开的官方检查页，不会打开或跳转网页、改输入、点击 Inspect 或发送索引请求。</p>
    </template>
    <p v-if="error" class="bing-error" role="alert">{{ error }}</p>
    <p v-if="frozen || readingRecord" class="bing-error" role="status" data-testid="bing-window-uncertain">原读取结果尚未确认，请先刷新读取记录；不会自动重新读取浏览器。<span v-if="frozen">原请求网址：{{ frozen.url }}</span></p>
    <p v-if="notice" class="bing-note" role="status" aria-live="polite">{{ notice }}</p>
    <article v-if="latest" class="bing-observation" data-testid="bing-window-latest">
      <strong>{{ bingReadLabel(latest) }} · 本机窗口观察</strong>
      <p>{{ latest.url }}</p>
      <p v-if="latest.message">{{ latest.message }}</p>
      <dl><div><dt>采集时刻</dt><dd>{{ date(latest.observed_at) }}</dd></div><div><dt>最近抓取（平台原文）</dt><dd>{{ latest.last_crawl_text || '本次未读到，保持未知' }}</dd></div><div><dt>页面获取（平台原文）</dt><dd>{{ latest.page_fetch_text || '本次未读到，保持未知' }}</dd></div><div><dt>缓存版本 / 新鲜度</dt><dd>未知，未核对已索引正文是否为最新版</dd></div><div><dt>平台时区</dt><dd>未知，不用采集时刻替代抓取时间</dd></div></dl>
    </article>
    <details v-if="rows.length" class="bing-history"><summary>查看 {{ rows.length }} 条本机窗口观察 · 与用户确认分开</summary><article v-for="item in rows" :key="item.id" class="bing-history-row"><strong>{{ bingReadLabel(item) }}</strong><p>{{ item.url }}</p><p>采集时刻：{{ date(item.observed_at) }}</p><p>最近抓取原文：{{ item.last_crawl_text || '未知' }}</p><p v-if="item.message">{{ item.message }}</p><small>来源：本机普通浏览器窗口 · 非用户确认 · 非独立核验</small><details><summary>读取编号与诊断</summary><p>{{ item.request_id }}</p><p v-if="item.reason">{{ item.reason }}</p></details></article></details>
    <p class="bing-note">读取只说明当前窗口显示的内容，不证明刚刚重新 Inspect、新版已进入索引或豆包采用；未知不等于未索引，本观察不计 GEO 效果，也不修改下方人工确认结果。</p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { geoWebmasterApi } from '@/api/geoWebmaster'
import { webmasterOverview, webmasterError } from '@/utils/geoWebmaster'
import { bingOwner, bingTarget, bingSnapshot, bingObservations, bingReceiptMatches, bingSavedReply, bingReadLabel, bingPendingKey, bingRestore, bingAwait } from '@/utils/geoBingInspection'
import { journeyDate } from '@/utils/geoJourney'

const props = defineProps({ cycle: { type: Object, required: true }, view: { type: Object, required: true }, url: { type: String, default: '' }, busy: Boolean })
const emit = defineEmits(['busy', 'overview'])
const available = ref(false), pending = ref(false), checking = ref(false), frozen = ref(null), error = ref(''), notice = ref('')
const rows = computed(() => bingObservations(props.cycle, props.view))
const target = computed(() => bingTarget(props.cycle, props.url, props.view))
const validUrl = computed(() => Boolean(target.value && props.view.supported))
const latest = computed(() => rows.value.find(item => item.url === target.value) || null)
const readingRecord = computed(() => rows.value.some(item => item.url === target.value && item.status === 'reading'))
const localBusy = computed(() => pending.value || checking.value), locked = computed(() => props.busy || localBusy.value)
const date = value => value ? journeyDate(value) : '本次未取得时间'
let generation = 0, disposed = false, activeWait = null
const owner = () => bingOwner(props.cycle, props.url)
const active = (epoch, key) => !disposed && epoch === generation && key === owner()
const storage = () => { try { return globalThis.localStorage } catch { return null } }
const bridge = () => {
  const api = typeof window === 'undefined' ? null : window.pywebview?.api
  return typeof api?.read_bing_inspection === 'function' ? api.read_bing_inspection.bind(api) : null
}
const syncBridge = () => { available.value = Boolean(bridge()) }
const clearPending = () => {
  try { const store = storage(), saved = bingRestore(store, props.cycle); if (saved && saved.request_id === frozen.value?.request_id) store.removeItem(bingPendingKey(props.cycle)) } catch { /* The backend observation remains authoritative. */ }
  frozen.value = null
}
const reconcile = source => {
  const view = webmasterOverview(props.cycle, source)
  if (!view.supported) throw new Error('站长资料范围已变化，请刷新本轮后核对原记录。')
  const captured = frozen.value || bingRestore(storage(), props.cycle)
  if (captured) {
    frozen.value = captured
    const saved = bingObservations(props.cycle, view).find(row => bingReceiptMatches(row, captured, props.cycle))
    if (saved && saved.status !== 'reading') { clearPending(); notice.value = bingReadLabel(saved) }
  }
  emit('overview', source)
  return view
}
const waitFor = async (promise, milliseconds) => {
  const waiter = bingAwait(promise, { setTimeout, clearTimeout }, milliseconds)
  activeWait = waiter
  try { return await waiter.promise }
  finally { if (activeWait === waiter) activeWait = null }
}
const refresh = async () => {
  if (disposed || locked.value) return
  const epoch = generation, key = owner(), cycleId = props.cycle.id
  checking.value = true; error.value = ''
  try {
    const reply = await waitFor(geoWebmasterApi.list(cycleId), 15000)
    if (!active(epoch, key)) return
    if (!reply?.data) throw new Error('未取得已保存的读取记录，请稍后再刷新。')
    reconcile(reply.data)
    if (frozen.value) notice.value = '原回执尚未确认，继续保留同次编号；没有再次读取浏览器。'
  } catch (cause) { if (active(epoch, key)) error.value = webmasterError(cause) }
  finally { if (active(epoch, key)) checking.value = false }
}
const readWindow = async () => {
  if (disposed || locked.value || frozen.value || readingRecord.value || !validUrl.value) return
  const method = bridge()
  if (!method) { syncBridge(); error.value = '当前桌面版本尚不支持读取普通浏览器检查页。'; return }
  let captured
  try { captured = bingSnapshot(props.cycle, props.view, target.value, globalThis.crypto?.randomUUID?.()) }
  catch (cause) { error.value = webmasterError(cause); return }
  const epoch = generation, key = owner()
  pending.value = true; error.value = ''; notice.value = ''
  let invoked = false
  try {
    const reply = await waitFor(geoWebmasterApi.list(captured.cycle_id), 15000)
    if (!active(epoch, key)) return
    const fresh = reconcile(reply.data)
    if (fresh.context_sha256 !== captured.context_sha256) throw new Error('站长进度已变化，已刷新；请核对当前网址后再明确读取。')
    if (frozen.value || bingObservations(props.cycle, fresh).some(row => row.url === captured.url && row.status === 'reading')) throw new Error('这个网址的原读取结果尚未确认，请刷新原记录，不要重新读取。')
    const store = storage()
    if (!store) throw new Error('未能保留本次读取编号，尚未读取浏览器。请检查软件本地存储。')
    store.setItem(bingPendingKey(props.cycle), JSON.stringify(captured))
    if (bingRestore(store, props.cycle)?.request_id !== captured.request_id) throw new Error('未能保留本次读取编号，尚未读取浏览器。')
    frozen.value = captured
    invoked = true
    const result = await waitFor(method(captured.cycle_id, captured.url, captured.context_sha256, captured.request_id), 45000)
    if (!active(epoch, key)) return
    bingSavedReply(result, captured, props.cycle)
    reconcile(result.overview)
    if (result.inspection.status === 'reading') notice.value = '原读取尚未完成，请刷新原记录；不会自动重新读取。'
    else { clearPending(); notice.value = bingReadLabel(result.inspection) }
  } catch (cause) {
    if (active(epoch, key)) {
      error.value = webmasterError(cause) + (invoked ? ' 请先刷新读取记录，不要重复读取。' : ' 本次未调用浏览器读取。')
    }
  } finally { if (active(epoch, key)) pending.value = false }
}
watch(localBusy, value => emit('busy', value), { immediate: true, flush: 'sync' })
watch(owner, () => {
  generation++; activeWait?.cancel(); activeWait = null; pending.value = false; checking.value = false
  error.value = ''; notice.value = ''; frozen.value = bingRestore(storage(), props.cycle)
}, { immediate: true, flush: 'sync' })
watch(() => props.view.native_inspections, () => {
  if (!frozen.value) return
  const saved = rows.value.find(row => bingReceiptMatches(row, frozen.value, props.cycle))
  if (saved && saved.status !== 'reading') clearPending()
}, { flush: 'sync' })
onMounted(() => { syncBridge(); if (typeof window !== 'undefined') window.addEventListener('pywebviewready', syncBridge) })
onUnmounted(() => { disposed = true; generation++; activeWait?.cancel(); if (typeof window !== 'undefined') window.removeEventListener('pywebviewready', syncBridge); emit('busy', false) })
</script>

<style scoped>
.bing-window{margin:12px 0 18px;padding:14px;border:1px solid #d5e4de;border-radius:8px;background:#fff;min-width:0;overflow-wrap:anywhere}.bing-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.bing-actions .el-button{margin-left:0}.bing-note,.bing-history-row small{font-size:12px;line-height:1.8;color:var(--geo-muted,#63736f)}.bing-note{margin:9px 0 0}.bing-error{font-size:12px;line-height:1.8;padding:8px 10px;background:#fff6eb;color:#94602e;border-radius:6px}.bing-error span{display:block}.bing-observation{margin-top:12px;padding:12px;background:#f5faf7;border-radius:7px;font-size:12px}.bing-observation p,.bing-history-row p{margin:5px 0}.bing-observation dl{margin:10px 0 0}.bing-observation dl>div{display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px;margin:5px 0}.bing-observation dt{color:var(--geo-muted,#63736f)}.bing-observation dd{margin:0;white-space:pre-wrap}.bing-history{margin-top:12px}.bing-history>summary{font-size:12px}.bing-history-row{padding:12px 0;border-top:1px solid #dce6e2;font-size:12px}.bing-history-row details{margin-top:6px}.bing-window details[open]>:not(summary){animation:bing-reveal 180ms ease-out}@keyframes bing-reveal{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}@media(max-width:650px){.bing-window{padding:12px}.bing-actions{flex-direction:column;align-items:stretch}.bing-actions .el-button{max-width:100%;white-space:normal;height:auto;min-height:32px}.bing-observation dl>div{grid-template-columns:1fr;gap:2px}}@media(prefers-reduced-motion:reduce){.bing-window details[open]>:not(summary){animation:none}}
</style>
