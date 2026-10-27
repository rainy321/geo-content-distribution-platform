<template>
  <section class="webmaster" aria-label="官网收录检查" data-testid="webmaster">
    <header><div><h3>让搜索引擎发现官网</h3><p>Bing 接入、文件部署与索引结果，回到这里继续。</p></div><el-button text :disabled="locked" @click="refresh">刷新进度</el-button></header>
    <p v-if="error" class="webmaster-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="webmaster-hint" role="status">正在读取已保存的进度…</p>
    <p v-if="!view.supported" class="webmaster-hint">{{ view.reason }}</p>
    <template v-else>
      <div class="webmaster-summary" data-testid="webmaster-summary"><strong>{{ view.site_origin }}</strong><span>{{ record.ownership?.status==='confirmed' ? '网站验证完成（用户确认）' : '网站验证待确认' }}</span><span>{{ sitemapLabel }}</span><span>{{ inspections.length ? `已登记 ${inspections.length} 个页面的检查结果` : '索引状态未知' }}</span></div>
      <details class="webmaster-details">
        <summary>继续官网接入与收录检查</summary>
        <div class="webmaster-tabs" role="group" aria-label="站长操作步骤"><button v-for="(label,index) in steps" :key="label" type="button" :class="{selected:step===index}" :aria-pressed="step===index" :disabled="locked" @click="step=index">{{ index+1 }} · {{ label }}</button></div>
        <Transition name="webmaster-reveal" mode="out-in">
          <div v-if="step===0" key="login" class="webmaster-panel">
            <h4>用普通浏览器添加官网</h4><p>打开你平时使用的 Chrome 或 Edge，登录 Bing 后，在“Add your site manually”填入下方官网地址。</p>
            <div class="webmaster-value">{{ view.site_origin }}/</div>
            <div class="webmaster-actions"><a :href="bingWebmasterEntry" target="_blank" rel="noopener noreferrer">打开 Bing 官方后台</a><el-button text :disabled="locked" @click="copyText(bingWebmasterEntry)">复制后台地址</el-button><el-button :disabled="locked" @click="step=1">已添加，准备验证文件</el-button></div>
            <p class="webmaster-hint">登录或扫码验证在官方页面完成。如果出现“浏览器或应用可能不安全”，请切换到日常浏览器手动登录。软件不接收账号密码。</p>
          </div>
          <div v-else-if="step===1" key="file" class="webmaster-panel">
            <h4>下载 XML，再交给网站负责人</h4><p>在 Bing 的“XML File”下点击 <strong>BingSiteAuth.xml</strong> 下载。先上传原文件到这里保存，再部署到官网。</p>
            <label class="webmaster-upload"><span>{{ readingFile ? '正在读取原文件…' : '选择 BingSiteAuth.xml' }}</span><input type="file" accept=".xml,application/xml,text/xml" :disabled="locked" aria-label="选择 BingSiteAuth.xml 原文件" data-testid="webmaster-upload" @change="uploadFile"/></label>
            <p v-if="record.verification_file" class="webmaster-hint">已保存原文件 · {{ record.verification_file.bytes }} 字节；下一步交给网站负责人部署。</p>
            <div v-if="record.verification_file" class="webmaster-actions"><el-button :disabled="locked" @click="saveVerificationFile">下载已保存的原文件</el-button><el-button :disabled="locked" @click="copyText(deploymentText)">复制部署说明</el-button><el-button :disabled="locked" @click="step=2">已交付，检查部署</el-button></div>
            <details v-if="record.verification_file"><summary>查看部署说明</summary><p class="webmaster-deployment">{{ deploymentText }}</p></details>
            <p class="webmaster-hint">请网站负责人将文件发布到 {{ view.verification_url }}，并纳入后续部署。文件上传到软件后，还需要部署到官网。</p>
          </div>
          <div v-else-if="step===2" key="verify" class="webmaster-panel">
            <h4>先核对公网文件，再点 Bing 验证</h4><p class="webmaster-value">{{ view.verification_url }}</p>
            <p data-testid="webmaster-public-result"><strong>软件公网核对：</strong>{{ publicLabel }}<span v-if="record.public_check?.checked_at"> · {{ date(record.public_check.checked_at) }}</span></p>
            <p v-if="record.public_check?.error" class="webmaster-hint">{{ record.public_check.error }}</p>
            <div class="webmaster-actions"><el-button :disabled="locked" :loading="saving==='check_public'" data-testid="webmaster-check-public" @click="perform('check_public')">核对公网文件和站点地图</el-button></div>
            <p class="webmaster-hint">公网文件与原件一致后，回 Bing 点击 Verify / Try Again。只有 Bing 显示验证成功、进入网站控制台，才勾选下方结果。</p>
            <template v-if="record.ownership?.status!=='confirmed'"><label class="webmaster-confirm"><input v-model="ownershipConfirmed" type="checkbox" :disabled="locked"/>我已在 Bing 完成验证并进入网站控制台</label><el-button :disabled="locked || !ownershipConfirmed" :loading="saving==='confirm_ownership'" data-testid="webmaster-confirm-ownership" @click="confirmOwnership">保存 Bing 验证结果</el-button></template>
            <p v-else class="webmaster-hint" data-testid="webmaster-owner-confirmed">网站验证完成（用户确认） · {{ date(record.ownership.observed_at) }}<el-button text :disabled="locked" @click="step=3">继续站点地图与索引检查</el-button></p>
          </div>
          <div v-else key="index" class="webmaster-panel">
            <h4>提交站点地图，记录实际索引状态</h4><p>在 Bing 左侧打开 Sitemaps，点击 Submit sitemap，填入下方地址提交一次。</p>
            <div class="webmaster-value">{{ view.sitemap_url }}<el-button text :disabled="locked" @click="copyText(view.sitemap_url)">复制</el-button></div>
            <p v-if="record.sitemap_check?.status==='ok'" class="webmaster-hint">软件已访问到站点地图，包含 {{ record.sitemap_check.entries }} 条网址。此数量不是已索引页数。</p>
            <label :for="`webmaster-sitemap-${cycle.id}`">Bing 站点地图列表的实际状态</label><select :id="`webmaster-sitemap-${cycle.id}`" v-model="sitemapStatus" :disabled="locked" data-testid="webmaster-sitemap-status"><option value="">请选择当前显示的结果</option><option value="pending">已提交，正在处理 / Processing</option><option value="success">Bing 处理成功 / Success</option><option value="failed">处理失败 / Failed</option></select>
            <details class="webmaster-sitemap-detail"><summary>补充 Bing 显示的数据（可选）</summary><label :for="`webmaster-discovered-${cycle.id}`">Bing 发现的网址数，未知时留空</label><input :id="`webmaster-discovered-${cycle.id}`" v-model.number="discoveredUrls" type="number" min="0" step="1" :disabled="locked" data-testid="webmaster-discovered"/><label :for="`webmaster-sitemap-note-${cycle.id}`">处理提示、最后抓取时间等</label><textarea :id="`webmaster-sitemap-note-${cycle.id}`" v-model="sitemapNote" rows="2" maxlength="500" :disabled="locked" placeholder="照实记录；最后抓取为空时写“未显示”"/></details>
            <el-button :disabled="locked || !sitemapStatus" :loading="saving==='record_sitemap'" data-testid="webmaster-save-sitemap" @click="saveSitemap">保存处理状态</el-button><p class="webmaster-hint">{{ sitemapLabel }}。收到“提交成功”提示后，列表仍显示 Processing 时，请选择“正在处理”。</p>
            <p v-if="Number.isInteger(record.sitemap_submission?.discovered_urls)" class="webmaster-hint">Bing 发现网址数：{{ record.sitemap_submission.discovered_urls }}（用户确认，非已索引页数）</p><p v-if="record.sitemap_submission?.note" class="webmaster-hint">{{ record.sitemap_submission.note }}</p>
            <details class="webmaster-inspection"><summary>检查页面的抓取与索引结果</summary><p>在 Bing 左侧打开 URL Inspection，逐页检查。先在它的网址输入框按 Ctrl+A 全选，再粘贴完整网址，避免重复拼上 http://。</p><el-button text :disabled="locked" data-testid="webmaster-copy-inspection" @click="copyText(inspectionCopyUrl)">{{ validInspection ? '复制待检查网址' : '复制官网首页网址' }}</el-button><p class="webmaster-hint">记录“索引”结果；Live URL 能访问页面只证明本次可抓取。输入错误或接口 Error 时，索引状态仍未知。</p>
              <label :for="`webmaster-url-${cycle.id}`">检查的完整页面网址</label><input :id="`webmaster-url-${cycle.id}`" v-model="inspectionUrl" type="url" :list="`webmaster-urls-${cycle.id}`" :disabled="locked" :placeholder="`${view.site_origin}/`" data-testid="webmaster-inspection-url"/><datalist :id="`webmaster-urls-${cycle.id}`"><option v-for="url in view.target_urls" :key="url" :value="url"/></datalist>
              <label :for="`webmaster-index-${cycle.id}`">索引状态</label><select :id="`webmaster-index-${cycle.id}`" v-model="inspectionStatus" :disabled="locked"><option value="unknown">未知 / 尚未确认</option><option value="indexed">Bing 明确显示已索引</option><option value="not_indexed">Bing 明确显示未索引</option></select>
              <label :for="`webmaster-note-${cycle.id}`">页面提示与抓取时间（可选）</label><textarea :id="`webmaster-note-${cycle.id}`" v-model="inspectionNote" maxlength="500" rows="2" :disabled="locked" placeholder="照实记录 Bing 提示、最后抓取时间等"/>
              <el-button :disabled="locked || !validInspection" :loading="saving==='record_inspection'" data-testid="webmaster-save-inspection" @click="saveInspection">保存页面检查结果</el-button>
              <article v-for="item in inspections" :key="item.url" class="webmaster-inspection-row"><strong>{{ indexLabel(item.status) }}（用户确认）</strong><p>{{ item.url }}</p><p v-if="item.note">{{ item.note }}</p><small>{{ date(item.observed_at) }}</small></article>
            </details>
          </div>
        </Transition>
        <p class="webmaster-footnote">记录自动保存，可关闭软件后继续。网站验证、提交、抓取、索引和豆包采用分别核对；这里的进度不计入 GEO 成效。</p>
      </details>
    </template>
    <p v-if="notice" class="webmaster-hint" role="status" aria-live="polite">{{ notice }}</p>
    <p v-if="writeUncertain" class="webmaster-error">保存结果未确认，请先刷新进度核对；软件不会自动重复写入。</p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { geoWebmasterApi } from '@/api/geoWebmaster'
import { journeyDate } from '@/utils/geoJourney'
import { bingWebmasterEntry, webmasterOverview, webmasterOrigin, webmasterPayload, webmasterReadFile, webmasterDecodeFile,
  webmasterDeploymentText, webmasterStage, webmasterPublicLabel, webmasterSitemapLabel, webmasterSiteUrl, webmasterIndexLabel, webmasterError } from '@/utils/geoWebmaster'

const props = defineProps({ cycle: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['busy'])
const overview = ref(props.cycle.webmaster || null), loading = ref(false), saving = ref(''), readingFile = ref(false)
const error = ref(''), notice = ref(''), writeUncertain = ref(false), ownershipConfirmed = ref(false)
const step = ref(webmasterStage(props.cycle.webmaster?.record)), sitemapStatus = ref(''), discoveredUrls = ref(''), sitemapNote = ref(''), inspectionUrl = ref(''), inspectionStatus = ref('unknown'), inspectionNote = ref('')
const steps = ['添加官网', '准备文件', '完成验证', '站点地图与索引']
let scopeVersion = 0, readVersion = 0, disposed = false
const currentScope = () => `${props.cycle.id}:${webmasterOrigin(props.cycle)}`
const view = computed(() => webmasterOverview(props.cycle, overview.value))
const record = computed(() => view.value.record || {})
const localBusy = computed(() => Boolean(saving.value) || readingFile.value)
const locked = computed(() => props.busy || localBusy.value || loading.value)
const publicLabel = computed(() => webmasterPublicLabel(record.value)), sitemapLabel = computed(() => webmasterSitemapLabel(record.value))
const inspections = computed(() => (Array.isArray(record.value.inspections) ? record.value.inspections : []).filter(item => webmasterSiteUrl(item?.url, view.value.site_origin)))
const deploymentText = computed(() => webmasterDeploymentText(view.value))
const validInspection = computed(() => webmasterSiteUrl(inspectionUrl.value, view.value.site_origin) && Array.from(inspectionNote.value).length <= 500)
const inspectionCopyUrl = computed(() => validInspection.value ? inspectionUrl.value : `${view.value.site_origin}/`)
const date = value => journeyDate(value), indexLabel = status => webmasterIndexLabel(status)
const apply = value => { overview.value = value; ownershipConfirmed.value = false }

const refreshOverview = async () => {
  if (disposed || !webmasterOrigin(props.cycle)) return false
  const scope = currentScope(), version = scopeVersion, read = ++readVersion
  const result = await geoWebmasterApi.list(props.cycle.id)
  if (disposed || version !== scopeVersion || scope !== currentScope() || read !== readVersion) return false
  apply(result.data)
  return true
}
const refresh = async () => {
  if (locked.value || disposed) return
  const version = scopeVersion; loading.value = true; error.value = ''
  try { if (await refreshOverview()) writeUncertain.value = false }
  catch (cause) { if (version === scopeVersion && !disposed) error.value = webmasterError(cause) }
  finally { if (version === scopeVersion && !disposed) loading.value = false }
}
const perform = async (action, fields = {}, options = {}) => {
  if (disposed || props.busy || saving.value || loading.value || writeUncertain.value || readingFile.value && !options.duringUpload || !view.value.supported) return false
  const scope = currentScope(), version = scopeVersion, cycleId = props.cycle.id, context = options.context || view.value.context_sha256
  saving.value = action; error.value = ''; notice.value = ''
  let wrote = false
  try {
    if (!await refreshOverview() || version !== scopeVersion || scope !== currentScope() || props.busy) return false
    if (context !== view.value.context_sha256) throw new Error('官网或站长进度已变化，已刷新。请核对当前结果后再操作。')
    const payload = webmasterPayload(view.value, action, fields)
    wrote = true
    const result = await geoWebmasterApi.update(cycleId, payload)
    if (disposed || version !== scopeVersion || scope !== currentScope()) return false
    readVersion++; apply(result.data); notice.value = '进度已保存。'
    return true
  } catch (cause) {
    if (!disposed && version === scopeVersion && scope === currentScope()) { error.value = webmasterError(cause); writeUncertain.value = wrote }
    return false
  } finally { if (!disposed && version === scopeVersion) saving.value = '' }
}
const uploadFile = async event => {
  const file = event?.target?.files?.[0]
  if (!file || locked.value || writeUncertain.value) return
  const version = scopeVersion, scope = currentScope(), context = view.value.context_sha256
  readingFile.value = true; error.value = ''; notice.value = ''
  try {
    const xml_base64 = await webmasterReadFile(file)
    if (disposed || version !== scopeVersion || scope !== currentScope()) return
    if (await perform('upload_xml', { xml_base64 }, { duringUpload: true, context })) step.value = 2
  } catch (cause) { if (!disposed && version === scopeVersion) error.value = webmasterError(cause) }
  finally { if (!disposed && version === scopeVersion) readingFile.value = false; if (event?.target) event.target.value = '' }
}
const confirmOwnership = async () => { if (ownershipConfirmed.value && await perform('confirm_ownership', { confirmed: true })) step.value = 3 }
const saveSitemap = () => perform('record_sitemap', { status: sitemapStatus.value, discovered_urls: discoveredUrls.value === '' ? null : discoveredUrls.value, note: sitemapNote.value })
const saveInspection = () => validInspection.value && perform('record_inspection', { url: inspectionUrl.value, status: inspectionStatus.value, note: inspectionNote.value })
const copyText = async text => {
  if (locked.value || !text) return
  const version = scopeVersion
  try { await navigator.clipboard.writeText(text); if (!disposed && version === scopeVersion) notice.value = '已复制。' }
  catch { if (!disposed && version === scopeVersion) error.value = '未能复制，请手动选择页面中的网址或部署说明。' }
}
const saveVerificationFile = () => {
  if (locked.value || !record.value.verification_file) return
  try {
    const bytes = webmasterDecodeFile(record.value.verification_file.xml_base64)
    const url = URL.createObjectURL(new Blob([bytes], { type: 'application/xml' })), link = document.createElement('a')
    link.href = url; link.download = 'BingSiteAuth.xml'; document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (cause) { error.value = webmasterError(cause) }
}
watch(localBusy, value => emit('busy', value), { immediate: true, flush: 'sync' })
watch(() => props.cycle.webmaster, value => { if (!localBusy.value) apply(value || null) }, { flush: 'sync' })
watch(currentScope, () => {
  scopeVersion++; readVersion++; overview.value = props.cycle.webmaster || null; loading.value = false; saving.value = ''; readingFile.value = false
  error.value = ''; notice.value = ''; writeUncertain.value = false; ownershipConfirmed.value = false; sitemapStatus.value = ''; discoveredUrls.value = ''; sitemapNote.value = ''; inspectionUrl.value = ''; inspectionStatus.value = 'unknown'; inspectionNote.value = ''
  step.value = webmasterStage(props.cycle.webmaster?.record); void refresh()
}, { flush: 'sync' })
onMounted(() => { void refresh() })
onUnmounted(() => { disposed = true; scopeVersion++; readVersion++; emit('busy', false) })
</script>

<style scoped>
.webmaster{margin:20px 0;padding:20px 24px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:#fff;color:var(--geo-ink,#173e3b);font-size:13px;line-height:1.8}.webmaster header{display:flex;justify-content:space-between;align-items:center;gap:12px}.webmaster h3{margin:0;font-size:16px}.webmaster h4{margin:0 0 10px;font-size:14px}.webmaster header p,.webmaster-hint,.webmaster-footnote{color:var(--geo-muted,#63736f);font-size:12px}.webmaster-summary{display:flex;gap:8px 18px;flex-wrap:wrap;padding-top:12px;font-size:12px}.webmaster-summary strong{width:100%;overflow-wrap:anywhere}.webmaster summary{cursor:pointer;color:var(--geo-primary,#0a756e);width:fit-content}.webmaster-details{margin-top:14px}.webmaster-tabs{display:flex;flex-wrap:wrap;gap:7px;margin:18px 0}.webmaster-tabs button{padding:7px 12px;border:1px solid #dce6e2;border-radius:7px;background:#fff;color:#63736f;cursor:pointer;transition:background-color 180ms ease,color 180ms ease}.webmaster-tabs button.selected{background:#eff7f3;border-color:#afd2c8;color:#0a756e}.webmaster-tabs button:focus-visible,.webmaster summary:focus-visible{outline:2px solid #0a756e;outline-offset:3px}.webmaster-panel{padding:16px;background:#f7faf8;border-radius:8px}.webmaster-value{padding:9px 12px;margin:10px 0;background:#fff;border:1px solid #dce6e2;border-radius:6px;overflow-wrap:anywhere}.webmaster-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.webmaster a{color:#0a756e}.webmaster-error{color:#a6412d;font-size:12px}.webmaster-confirm{display:flex;align-items:center;gap:7px;margin:12px 0}.webmaster-upload{display:inline-flex;position:relative;padding:8px 14px;border:1px solid #93bdb5;border-radius:7px;background:#fff;color:#0a756e;cursor:pointer}.webmaster-upload input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}.webmaster-upload:focus-within{outline:2px solid #0a756e;outline-offset:3px}.webmaster-panel select,.webmaster-panel input[type=url],.webmaster-panel input[type=number],.webmaster-panel textarea{display:block;box-sizing:border-box;width:100%;max-width:100%;margin:6px 0 12px;padding:8px 10px;border:1px solid #cedcd7;border-radius:6px;background:#fff;color:#173e3b;font:inherit}.webmaster-panel label{font-size:12px}.webmaster-inspection{margin-top:20px;padding-top:16px;border-top:1px solid #dce6e2}.webmaster-inspection-row{margin-top:12px;padding:12px;background:#fff;border-radius:6px;overflow-wrap:anywhere}.webmaster-inspection-row p{margin:4px 0}.webmaster-deployment{white-space:pre-wrap;overflow-wrap:anywhere}.webmaster-sitemap-detail{margin:12px 0}.webmaster-footnote{margin:16px 0 0}.webmaster-reveal-enter-active{transition:opacity 180ms ease,transform 220ms cubic-bezier(.2,.8,.2,1)}.webmaster-reveal-leave-active{transition:opacity 120ms ease}.webmaster-reveal-enter-from{opacity:0;transform:translateY(5px)}.webmaster-reveal-leave-to{opacity:0}@media(max-width:650px){.webmaster{padding:18px 16px}.webmaster-tabs button{flex:1;min-width:40%}.webmaster-panel{padding:12px}.webmaster-actions{align-items:stretch;flex-direction:column}.webmaster-actions .el-button{margin-left:0}}@media(prefers-reduced-motion:reduce){.webmaster-tabs button,.webmaster-reveal-enter-active,.webmaster-reveal-leave-active{transition:none}.webmaster-reveal-enter-from{transform:none}}
</style>
