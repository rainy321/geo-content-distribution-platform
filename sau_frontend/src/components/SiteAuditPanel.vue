<template>
  <section class="site-audit" aria-labelledby="site-audit-title">
    <header class="audit-heading">
      <div>
        <span>PUBLIC SITE / TECHNICAL READINESS</span>
        <h3 id="site-audit-title">先确认官网能被公开读取</h3>
        <p>检查首页、robots、Sitemap、结构化数据与 llms.txt。结果只代表本次公开网页检查，不证明搜索引擎索引或 AI 已经引用。</p>
      </div>
      <div class="audit-stamp" :class="statusMeta.tone" aria-live="polite">
        <span>{{ statusMeta.label }}</span>
        <strong>{{ auditResult?.score == null ? '—' : Math.round(auditResult.score) }}</strong>
        <small>{{ auditResult?.grade || '未检测' }}</small>
      </div>
    </header>

    <div class="audit-controls">
      <label for="site-audit-url">
        <span>本次检查网址</span>
        <el-input
          id="site-audit-url"
          v-model="auditUrl"
          :disabled="!projectId || auditLoading"
          placeholder="https://example.com"
          clearable
          @input="handleAuditUrlInput"
          @keydown.enter.prevent="runAudit"
        />
        <small>{{ auditUrlHelp }}</small>
      </label>
      <el-button
        type="primary"
        native-type="button"
        :loading="auditLoading"
        :disabled="!canRunAudit"
        @click="runAudit"
      >
        {{ auditLoading ? '正在检查公开网页' : '检查官网' }}
      </el-button>
    </div>

    <label class="fetch-confirmation" :class="{ disabled: !normalizedProjectId || !savedWebsiteUrl }">
      <input v-model="auditConfirmed" type="checkbox" :disabled="!normalizedProjectId || !savedWebsiteUrl || auditLoading" />
      <span>
        我确认：将从本机向该公开站点发送 GET；最多读取目标页面、robots.txt、Sitemap 和 llms.txt，
        不发送项目正文、Cookie 或 API Key。
      </span>
    </label>

    <p class="external-boundary">
      <b>外部访问：</b>点击后后端会请求上方公开网址及其站点资源；不会调用 AI 模型，也不会把站点内容计作已收录或已引用。
    </p>

    <div v-if="auditError" class="audit-alert" role="alert">
      <strong>站点审计没有完成</strong>
      <p>{{ auditError }}</p>
      <small v-if="auditResult">上一次成功结果仍保留在下方。</small>
    </div>

    <template v-if="auditResult">
      <div class="audit-meta">
        <div>
          <span>最终地址</span>
          <a
            v-if="auditResult.finalUrl || auditResult.requestedUrl"
            :href="auditResult.finalUrl || auditResult.requestedUrl"
            target="_blank"
            rel="noopener noreferrer"
          >{{ displayHost(auditResult.finalUrl || auditResult.requestedUrl) }} ↗</a>
          <strong v-else>未检测</strong>
        </div>
        <div><span>记录时间</span><strong>{{ formatDate(auditResult.auditedAt) }}</strong></div>
        <div><span>算法版本</span><strong>{{ auditResult.auditVersion || '未检测' }}</strong></div>
        <div><span>本机记录</span><strong>{{ auditResult.id == null ? '未保存' : `#${auditResult.id}` }}</strong></div>
      </div>

      <div class="check-corridor" aria-label="站点审计检查项">
        <article v-for="check in auditResult.checks" :key="check.key" :class="`is-${check.status}`">
          <div class="check-index" aria-hidden="true" />
          <span>{{ check.label }}</span>
          <strong>{{ checkStatusLabel(check.status) }}</strong>
          <small>{{ check.score == null ? '未返回分数' : `${check.score} / ${check.maxScore ?? '—'}` }}</small>
          <details v-if="check.evidence.length">
            <summary>查看证据</summary>
            <p v-for="evidence in check.evidence" :key="evidence">{{ evidence }}</p>
          </details>
        </article>
      </div>

      <div class="audit-evidence-grid">
        <article class="evidence-card crawler-card">
          <header><span>CRAWLER POLICY</span><strong>AI 爬虫访问规则</strong></header>
          <div v-if="auditResult.crawlerAccess.length" class="crawler-list">
            <div v-for="crawler in auditResult.crawlerAccess" :key="crawler.id">
              <i :class="crawler.allowed === true ? 'allowed' : crawler.allowed === false ? 'blocked' : 'unknown'" />
              <p><strong>{{ crawler.label }}</strong><small>{{ crawler.userAgent }}</small></p>
              <b>{{ crawler.allowed === true ? '允许' : crawler.allowed === false ? '阻止' : '未检测' }}</b>
            </div>
          </div>
          <p v-else class="no-reading">未检测到爬虫访问规则。</p>
        </article>

        <article class="evidence-card schema-card">
          <header><span>STRUCTURED DATA</span><strong>结构化数据</strong></header>
          <dl>
            <div><dt>有效块</dt><dd>{{ metricOrUnknown(auditResult.structuredData.validBlocks) }}</dd></div>
            <div><dt>无效块</dt><dd>{{ metricOrUnknown(auditResult.structuredData.invalidBlocks) }}</dd></div>
          </dl>
          <p v-if="auditResult.structuredData.types.length" class="type-list">
            <span v-for="type in auditResult.structuredData.types" :key="type">{{ type }}</span>
          </p>
          <p v-else class="no-reading">未检测到可识别的 Schema 类型。</p>
        </article>

        <article class="evidence-card resource-card">
          <header><span>DISCOVERY FILES</span><strong>站点发现资源</strong></header>
          <div class="resource-list">
            <div v-for="resource in auditResult.resources" :key="resource.key">
              <span>{{ resource.label }}</span>
              <strong :class="`is-${resource.status}`">
                {{ resource.advisoryOnly
                  ? `建议项 · ${checkStatusLabel(resource.status)} · 不计分`
                  : checkStatusLabel(resource.status) }}
              </strong>
              <a v-if="resource.url" :href="resource.url" target="_blank" rel="noopener noreferrer">打开 ↗</a>
              <small v-else-if="resource.items.length">{{ resource.items.join(' · ') }}</small>
              <small v-else>未检测</small>
            </div>
          </div>
        </article>
      </div>

      <section class="recommendation-sheet" aria-labelledby="site-audit-actions">
        <header>
          <span>ACTION ORDER</span>
          <h4 id="site-audit-actions">按阻断优先级整改</h4>
        </header>
        <ol v-if="auditResult.recommendations.length">
          <li v-for="item in auditResult.recommendations" :key="item.id">
            <b :class="`priority-${item.priority.toLowerCase()}`">{{ item.priority }}</b>
            <div><strong>{{ item.title }}</strong><p>{{ item.action || '后端未返回具体处理动作。' }}</p></div>
          </li>
        </ol>
        <p v-else class="no-reading">没有返回整改建议；这不代表页面已经被索引或引用。</p>
      </section>

      <details class="audit-boundaries" :open="auditResult.boundaries.length > 0">
        <summary>能力边界 · {{ auditResult.boundaries.length || '未返回' }}</summary>
        <ul v-if="auditResult.boundaries.length">
          <li v-for="boundary in auditResult.boundaries" :key="boundary">{{ boundary }}</li>
        </ul>
        <p v-else>后端未返回能力边界，本次结果不能外推为索引、检索或引用证明。</p>
      </details>
    </template>

    <section class="audit-history" aria-labelledby="site-audit-history">
      <header>
        <div><span>LOCAL HISTORY</span><h4 id="site-audit-history">最近站点审计</h4></div>
        <button type="button" :disabled="historyLoading || !projectId" @click="loadHistory">刷新</button>
      </header>
      <p v-if="historyError" class="history-error" role="alert">{{ historyError }}</p>
      <p v-else-if="historyLoading" class="no-reading">正在读取本机记录…</p>
      <p v-else-if="!historyRuns.length" class="no-reading">当前项目还没有站点审计记录。</p>
      <div v-else class="history-list">
        <button
          v-for="run in historyRuns"
          :key="run.id ?? `${run.auditedAt}-${run.requestedUrl}`"
          type="button"
          :class="{ active: String(run.id) === String(auditResult?.id) }"
          :disabled="historyRunLoading === run.id"
          @click="restoreRun(run)"
        >
          <span>{{ formatDate(run.auditedAt) }}</span>
          <strong>{{ run.score == null ? '—' : Math.round(run.score) }} · {{ run.grade }}</strong>
          <small>{{ historyRunLoading === run.id ? '正在恢复…' : displayHost(run.finalUrl || run.requestedUrl) }}</small>
        </button>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { diagnosticsApi } from '@/api/diagnostics'
import {
  normalizeSiteAuditHistory,
  normalizeSiteAuditResult,
  safeSiteAuditUrl,
  siteAuditStatusPresentation
} from '@/utils/siteAuditResult'

const props = defineProps({
  projectId: { type: [Number, String], default: '' },
  website: { type: String, default: '' },
  projectName: { type: String, default: '' }
})

const auditUrl = ref('')
const auditResult = ref(null)
const auditLoading = ref(false)
const auditError = ref('')
const auditConfirmed = ref(false)
const pendingAuditIdentity = ref(null)
const historyRuns = ref([])
const historyLoading = ref(false)
const historyError = ref('')
const historyRunLoading = ref(null)
let auditRequestSequence = 0
let historyRequestSequence = 0
let historyRunRequestSequence = 0

const normalizedProjectId = computed(() => {
  const value = Number(props.projectId)
  return Number.isInteger(value) && value > 0 ? value : null
})
const safeAuditUrl = computed(() => safeSiteAuditUrl(auditUrl.value))
const savedWebsiteUrl = computed(() => safeSiteAuditUrl(props.website))
const sameOriginAsSavedWebsite = computed(() => {
  if (!safeAuditUrl.value || !savedWebsiteUrl.value) return false
  try {
    return new URL(safeAuditUrl.value).origin === new URL(savedWebsiteUrl.value).origin
  } catch {
    return false
  }
})
const canRunAudit = computed(() => Boolean(
  normalizedProjectId.value
  && safeAuditUrl.value
  && sameOriginAsSavedWebsite.value
  && auditConfirmed.value
  && !auditLoading.value
))
const statusMeta = computed(() => siteAuditStatusPresentation(auditResult.value?.status))
const auditUrlHelp = computed(() => {
  if (!normalizedProjectId.value) return '先选择一个品牌项目。'
  if (!savedWebsiteUrl.value) return '当前项目尚未配置公开官网，请先到品牌项目补充。'
  if (auditUrl.value && !safeAuditUrl.value) return '网址必须以 http:// 或 https:// 开头。'
  if (safeAuditUrl.value && !sameOriginAsSavedWebsite.value) return '本次网址必须与项目中保存的官网同源。'
  return `默认读取${props.projectName ? `“${props.projectName}”` : '当前项目'}官网；可以改成同源的具体页面。`
})

const readableError = error => (
  error?.response?.data?.msg
  || error?.response?.data?.message
  || error?.message
  || '检查网址和后端服务后重试。'
)

const createAuditIdentity = () => {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return {
    requestId: `site-audit-ui-${randomPart}`,
    idempotencyKey: `site-audit-${randomPart}`
  }
}

const handleAuditUrlInput = () => {
  auditConfirmed.value = false
  pendingAuditIdentity.value = null
}

const loadHistory = async () => {
  const projectId = normalizedProjectId.value
  const sequence = ++historyRequestSequence
  historyError.value = ''
  if (!projectId) {
    historyRuns.value = []
    historyLoading.value = false
    return
  }
  historyLoading.value = true
  try {
    const response = await diagnosticsApi.getSiteAuditRuns(projectId, 5, { suppressGlobalError: true })
    if (sequence !== historyRequestSequence || projectId !== normalizedProjectId.value) return
    historyRuns.value = normalizeSiteAuditHistory(response)
  } catch (error) {
    if (sequence !== historyRequestSequence || projectId !== normalizedProjectId.value) return
    historyError.value = readableError(error)
  } finally {
    if (sequence === historyRequestSequence) historyLoading.value = false
  }
}

const runAudit = async () => {
  if (auditLoading.value) return
  if (!normalizedProjectId.value) {
    auditError.value = '先选择一个品牌项目。'
    return
  }
  if (!safeAuditUrl.value) {
    auditError.value = '请输入以 http:// 或 https:// 开头的公开网址。'
    return
  }
  if (!savedWebsiteUrl.value) {
    auditError.value = '当前项目尚未配置公开官网，请先到品牌项目补充。'
    return
  }
  if (!sameOriginAsSavedWebsite.value) {
    auditError.value = '本次网址必须与项目中保存的官网同源。'
    return
  }
  if (!auditConfirmed.value) {
    auditError.value = '请先确认本次公开站点 GET 请求的范围。'
    return
  }
  const identity = pendingAuditIdentity.value || createAuditIdentity()
  const projectId = normalizedProjectId.value
  const requestedUrl = safeAuditUrl.value
  const sequence = ++auditRequestSequence
  pendingAuditIdentity.value = identity
  auditLoading.value = true
  auditError.value = ''
  try {
    const response = await diagnosticsApi.runSiteAudit({
      project_id: projectId,
      url: requestedUrl,
      persist: true,
      confirm_external_fetch: true
    }, {
      ...identity,
      suppressGlobalError: true
    })
    if (sequence !== auditRequestSequence || projectId !== normalizedProjectId.value) return
    auditResult.value = normalizeSiteAuditResult(response)
    auditUrl.value = auditResult.value.requestedUrl || requestedUrl
    pendingAuditIdentity.value = null
    auditConfirmed.value = false
    ElMessage.success('站点技术审计已返回并保存到本机。')
    void loadHistory()
  } catch (error) {
    if (sequence !== auditRequestSequence || projectId !== normalizedProjectId.value) return
    // Keep the last successful result visible. A failed external fetch must not
    // erase evidence from either this panel or the parent visibility workflow.
    auditError.value = readableError(error)
    if (error?.response) pendingAuditIdentity.value = null
  } finally {
    if (sequence === auditRequestSequence) auditLoading.value = false
  }
}

const restoreRun = async run => {
  if (run.id == null || historyRunLoading.value != null) return
  const projectId = normalizedProjectId.value
  const sequence = ++historyRunRequestSequence
  historyRunLoading.value = run.id
  historyError.value = ''
  try {
    const response = await diagnosticsApi.getSiteAuditRun(run.id, { suppressGlobalError: true })
    if (sequence !== historyRunRequestSequence || projectId !== normalizedProjectId.value) return
    auditResult.value = normalizeSiteAuditResult(response)
    auditUrl.value = auditResult.value.requestedUrl || auditUrl.value
  } catch (error) {
    if (sequence !== historyRunRequestSequence || projectId !== normalizedProjectId.value) return
    historyError.value = `无法恢复站点审计 #${run.id}：${readableError(error)}`
  } finally {
    if (sequence === historyRunRequestSequence) historyRunLoading.value = null
  }
}

const displayHost = value => {
  const safe = safeSiteAuditUrl(value)
  if (!safe) return '未检测'
  try {
    return new URL(safe).hostname.replace(/^www\./, '')
  } catch {
    return '未检测'
  }
}

const formatDate = value => {
  if (!value) return '未检测'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(date)
}

const metricOrUnknown = value => value == null ? '未检测' : String(value)
const checkStatusLabel = value => ({
  passed: '通过',
  warning: '需关注',
  failed: '未通过',
  pending: '检查中',
  not_checked: '未检测'
}[String(value)] || '未检测')

watch(
  [normalizedProjectId, () => props.website],
  ([projectId, website]) => {
    auditRequestSequence += 1
    historyRunRequestSequence += 1
    auditUrl.value = String(website || '').trim()
    auditResult.value = null
    auditLoading.value = false
    auditError.value = ''
    auditConfirmed.value = false
    pendingAuditIdentity.value = null
    historyRuns.value = []
    historyError.value = ''
    historyRunLoading.value = null
    if (projectId) void loadHistory()
  },
  { immediate: true }
)
</script>

<style lang="scss" scoped>
.site-audit {
  --audit-ink: #18191b;
  --audit-muted: #6f7277;
  --audit-rule: #d9d9d3;
  --audit-paper: #fbfbf8;
  --audit-pass: #246a5a;
  --audit-warn: #a66b24;
  --audit-fail: #a54242;
  grid-column: 1 / -1;
  padding: 24px;
  border: 1px solid var(--audit-rule);
  background: var(--audit-paper);
  color: var(--audit-ink);
}

.audit-heading,
.audit-controls,
.audit-meta,
.audit-history > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 22px;
}

.audit-heading > div:first-child { max-width: 780px; }
.audit-heading span,
.evidence-card header span,
.recommendation-sheet header span,
.audit-history header span {
  font-family: "Cascadia Mono", "SFMono-Regular", monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: #4f555c;
}
.audit-heading h3 { margin: 7px 0 6px; font-size: 23px; letter-spacing: -0.035em; }
.audit-heading p { margin: 0; color: var(--audit-muted); line-height: 1.65; }

.audit-stamp {
  flex: 0 0 104px;
  display: grid;
  justify-items: center;
  padding: 12px 10px;
  border: 1px solid var(--audit-rule);
  background: #fff;
}
.audit-stamp strong { font-size: 34px; line-height: 1; margin: 6px 0 4px; }
.audit-stamp small { color: var(--audit-muted); }
.audit-stamp.complete { border-top: 3px solid var(--audit-pass); }
.audit-stamp.warning, .audit-stamp.pending { border-top: 3px solid var(--audit-warn); }
.audit-stamp.failed { border-top: 3px solid var(--audit-fail); }

.audit-controls { align-items: flex-end; margin-top: 20px; }
.audit-controls label { flex: 1; display: grid; gap: 7px; }
.audit-controls label > span { font-size: 13px; font-weight: 700; }
.audit-controls label > small { color: var(--audit-muted); }
.audit-controls :deep(.el-button) { min-width: 136px; }
.external-boundary { margin: 12px 0 0; padding-left: 12px; border-left: 2px solid #aeb2b6; color: var(--audit-muted); font-size: 12px; line-height: 1.55; }
.fetch-confirmation { display: flex; gap: 10px; align-items: flex-start; margin-top: 14px; padding: 12px 13px; border: 1px solid var(--audit-rule); background: #fff; font-size: 12px; line-height: 1.55; cursor: pointer; }
.fetch-confirmation input { flex: 0 0 auto; margin-top: 2px; accent-color: var(--audit-ink); }
.fetch-confirmation.disabled { opacity: 0.55; cursor: not-allowed; }

.audit-alert,
.history-error {
  margin-top: 16px;
  padding: 12px 14px;
  border-left: 3px solid var(--audit-fail);
  background: #fff3f1;
  color: #733434;
}
.audit-alert p { margin: 4px 0; }

.audit-meta {
  margin-top: 22px;
  padding: 13px 0;
  border-block: 1px solid var(--audit-rule);
}
.audit-meta > div { min-width: 0; display: grid; gap: 4px; }
.audit-meta span { color: var(--audit-muted); font-size: 11px; }
.audit-meta strong, .audit-meta a { color: var(--audit-ink); font-size: 12px; overflow-wrap: anywhere; }

.check-corridor {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 1px;
  margin-top: 20px;
  background: var(--audit-rule);
  border: 1px solid var(--audit-rule);
}
.check-corridor article { position: relative; min-height: 126px; padding: 16px; background: #fff; }
.check-index { position: absolute; inset: 0 0 auto; height: 3px; background: #aeb2b6; }
.is-passed .check-index { background: var(--audit-pass); }
.is-warning .check-index { background: var(--audit-warn); }
.is-failed .check-index { background: var(--audit-fail); }
.check-corridor article > span { display: block; margin-bottom: 12px; color: var(--audit-muted); font-size: 11px; }
.check-corridor article > strong { display: block; font-size: 15px; }
.check-corridor article > small { display: block; margin-top: 5px; color: var(--audit-muted); }
.check-corridor details { margin-top: 12px; font-size: 11px; }
.check-corridor details p { margin: 6px 0 0; overflow-wrap: anywhere; }

.audit-evidence-grid { display: grid; grid-template-columns: 1.2fr 0.8fr 1fr; gap: 14px; margin-top: 14px; }
.evidence-card { padding: 17px; border: 1px solid var(--audit-rule); background: #fff; }
.evidence-card header { display: grid; gap: 5px; padding-bottom: 12px; border-bottom: 1px solid var(--audit-rule); }
.crawler-list > div { display: grid; grid-template-columns: 8px 1fr auto; gap: 9px; align-items: center; padding: 10px 0; border-bottom: 1px solid #ecece7; }
.crawler-list i { width: 7px; height: 7px; border-radius: 50%; background: #9da0a4; }
.crawler-list i.allowed { background: var(--audit-pass); }
.crawler-list i.blocked { background: var(--audit-fail); }
.crawler-list p { display: grid; gap: 2px; margin: 0; }
.crawler-list small { color: var(--audit-muted); overflow-wrap: anywhere; }
.crawler-list b { font-size: 11px; }
.schema-card dl { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 14px 0; }
.schema-card dl div { padding: 11px; background: #f3f3ef; }
.schema-card dt { color: var(--audit-muted); font-size: 11px; }
.schema-card dd { margin: 5px 0 0; font-size: 19px; font-weight: 750; }
.type-list { display: flex; flex-wrap: wrap; gap: 6px; }
.type-list span { padding: 4px 7px; border: 1px solid var(--audit-rule); font: 10px "Cascadia Mono", monospace; }
.resource-list > div { display: grid; grid-template-columns: 1fr auto; gap: 5px 10px; padding: 10px 0; border-bottom: 1px solid #ecece7; }
.resource-list strong { font-size: 11px; }
.resource-list strong.is-passed { color: var(--audit-pass); }
.resource-list strong.is-warning { color: var(--audit-warn); }
.resource-list strong.is-failed { color: var(--audit-fail); }
.resource-list a, .resource-list small { grid-column: 1 / -1; color: var(--audit-muted); font-size: 11px; overflow-wrap: anywhere; }

.recommendation-sheet { display: grid; grid-template-columns: 190px 1fr; gap: 22px; margin-top: 14px; padding: 18px; border: 1px solid var(--audit-rule); background: #fff; }
.recommendation-sheet h4, .audit-history h4 { margin: 5px 0 0; font-size: 16px; }
.recommendation-sheet ol { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.recommendation-sheet li { display: grid; grid-template-columns: 42px 1fr; gap: 11px; }
.recommendation-sheet li > b { align-self: start; padding: 4px 5px; border: 1px solid var(--audit-rule); text-align: center; font: 700 10px "Cascadia Mono", monospace; }
.recommendation-sheet .priority-p0 { border-color: var(--audit-fail); color: var(--audit-fail); }
.recommendation-sheet .priority-p1 { border-color: var(--audit-warn); color: var(--audit-warn); }
.recommendation-sheet li p { margin: 4px 0 0; color: var(--audit-muted); line-height: 1.5; }
.no-reading { margin: 12px 0 0; color: var(--audit-muted); font-size: 12px; line-height: 1.55; }

.audit-boundaries { margin-top: 14px; padding: 13px 15px; border: 1px dashed #bbbdb9; background: #f5f5f1; }
.audit-boundaries summary { cursor: pointer; font-weight: 700; }
.audit-boundaries ul, .audit-boundaries p { margin: 10px 0 0; color: var(--audit-muted); line-height: 1.55; }

.audit-history { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--audit-rule); }
.audit-history header button { border: 0; background: transparent; color: var(--audit-ink); cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.audit-history header button:disabled { opacity: 0.45; cursor: not-allowed; }
.history-list { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.history-list button { min-width: 0; display: grid; gap: 5px; padding: 11px; border: 1px solid var(--audit-rule); background: #fff; color: var(--audit-ink); text-align: left; cursor: pointer; }
.history-list button.active { border-color: var(--audit-ink); box-shadow: inset 0 -3px 0 var(--audit-ink); }
.history-list span, .history-list small { color: var(--audit-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }

button:focus-visible,
a:focus-visible,
summary:focus-visible { outline: 2px solid #2868a9; outline-offset: 3px; }

@media (max-width: 980px) {
  .check-corridor { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .audit-evidence-grid { grid-template-columns: 1fr; }
  .history-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 640px) {
  .site-audit { padding: 17px; }
  .audit-heading, .audit-controls, .audit-meta { display: grid; grid-template-columns: 1fr; }
  .audit-stamp { justify-self: stretch; grid-template-columns: 1fr auto auto; align-items: center; gap: 10px; }
  .audit-stamp strong { margin: 0; font-size: 26px; }
  .audit-controls :deep(.el-button) { width: 100%; }
  .check-corridor, .history-list { grid-template-columns: 1fr; }
  .recommendation-sheet { grid-template-columns: 1fr; }
}
</style>
