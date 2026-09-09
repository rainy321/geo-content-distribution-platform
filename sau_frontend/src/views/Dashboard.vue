<template>
  <main class="geo-home">
    <section v-if="loadError" class="load-state error-state" role="alert">
      <el-icon><Warning /></el-icon>
      <div>
        <p class="eyebrow">WORKSPACE OFFLINE</p>
        <h1>运营数据没有载入</h1>
        <p>检查本机服务后重新加载；已有项目、文章和发布任务不会受到影响。</p>
      </div>
      <el-button type="primary" :icon="Refresh" @click="fetchOverview">重新加载</el-button>
    </section>

    <section v-else-if="showOnboarding" class="first-run" aria-labelledby="first-run-title">
      <div class="first-run-copy">
        <p class="eyebrow">GENERATIVE ENGINE OPTIMIZATION</p>
        <h1 id="first-run-title">让品牌，<br>出现在 AI 的<br>答案里。</h1>
        <p class="hero-summary">从一份真实品牌底稿开始，建立可复测的可见度基线，再把缺口变成内容行动。</p>
        <div class="hero-actions">
          <el-button type="primary" size="large" :icon="ArrowRight" @click="router.push('/projects')">创建第一个品牌项目</el-button>
          <el-button size="large" @click="router.push('/ai-visibility')">了解诊断口径</el-button>
        </div>
        <small>无需先连接媒体账号，也不会触发真实发布。</small>
      </div>

      <div class="first-run-steps" aria-label="首次使用路径">
        <article>
          <span>01 / DEFINE</span>
          <el-icon><CollectionTag /></el-icon>
          <h2>固定品牌事实</h2>
          <p>记录品牌、产品、官网、关键词与竞品，保证后续分析口径一致。</p>
        </article>
        <article>
          <span>02 / MEASURE</span>
          <el-icon><Search /></el-icon>
          <h2>建立可见度基线</h2>
          <p>按问题与回答样本量化提及率、声量、位次和引用来源。</p>
        </article>
        <article>
          <span>03 / IMPROVE</span>
          <el-icon><DocumentChecked /></el-icon>
          <h2>按优先级优化</h2>
          <p>先补 P0/P1 缺口，生产内容并用相同问题和版本复测。</p>
        </article>
      </div>
    </section>

    <template v-else>
      <section class="hero-grid" aria-labelledby="hero-title">
        <div class="hero-copy">
          <p class="eyebrow">GENERATIVE ENGINE OPTIMIZATION</p>
          <h1 id="hero-title">让品牌，<br>出现在 AI 的<br>答案里。</h1>
          <p class="hero-summary">洞察品牌在 AI 回答中的表现，持续优化内容与引用。</p>
          <div class="hero-actions">
            <el-button type="primary" size="large" :icon="ArrowRight" @click="router.push('/ai-visibility')">开始品牌诊断</el-button>
            <el-button size="large" @click="router.push('/content-create')">创建优化内容</el-button>
          </div>
          <p class="hero-meta">
            <span>{{ activeProject?.name || '品牌项目' }}</span><i />
            <span>AI 可见度</span><i /><span>内容策略</span><i /><span>引用洞察</span>
          </p>
        </div>

        <article class="visibility-card" v-loading="insightsLoading" aria-labelledby="visibility-card-title">
          <header class="card-title-row">
            <div>
              <p class="eyebrow">LIVE PROJECT SIGNAL</p>
              <h2 id="visibility-card-title">品牌可见度概览</h2>
            </div>
            <span class="data-badge">真实数据</span>
          </header>

          <section class="visibility-index">
            <header>
              <div>
                <span>可见度指数 <el-icon title="来自最近一次可见度样本"><InfoFilled /></el-icon></span>
                <div class="score-line">
                  <strong>{{ formatScore(visibilityScore) }}</strong>
                  <em v-if="visibilityChange != null" :class="{ negative: visibilityChange < 0 }">
                    {{ visibilityChange >= 0 ? '↑' : '↓' }} {{ Math.abs(visibilityChange).toFixed(1) }} 较上次
                  </em>
                  <em v-else>等待可比记录</em>
                </div>
              </div>
              <div class="range-control" aria-label="趋势口径"><span>最近</span><strong>{{ visibilitySeries.length || 0 }} 次</strong></div>
            </header>
            <VisibilityTrendCanvas
              :values="visibilitySeries.map(item => item.value)"
              :labels="visibilitySeries.map(item => shortDate(item.label))"
              metric-label="品牌提及率"
            />
          </section>

          <section class="citation-snapshot">
            <header><h3>品牌引用追踪</h3><span>{{ citationEvidence ? '已识别证据' : '等待引用' }}</span></header>
            <div v-if="citationEvidence" class="citation-content">
              <el-icon><ChatDotRound /></el-icon>
              <div>
                <p>{{ citationEvidence.quote }}</p>
                <footer>
                  <span>来源：</span>
                  <a v-if="citationEvidence.url" :href="citationEvidence.url" target="_blank" rel="noopener noreferrer"><el-icon><Link /></el-icon>{{ citationEvidence.domain }}</a>
                  <strong v-else>{{ citationEvidence.domain }}</strong>
                  <small>{{ formatDate(latestVisibility?.analyzedAt) }}</small>
                </footer>
              </div>
            </div>
            <div v-else class="citation-empty">
              <el-icon><Link /></el-icon>
              <p>最近记录还没有可识别引用。进入可见度分析查看缺口和下一步动作。</p>
              <button type="button" @click="router.push('/ai-visibility')">打开分析 →</button>
            </div>
          </section>
          <p v-if="insightError" class="insight-warning" role="status">{{ insightError }}</p>
        </article>
      </section>

      <section class="discovery-section" aria-labelledby="discovery-title">
        <header><p class="eyebrow">BUILT FOR AI DISCOVERY</p><h2 id="discovery-title">从被搜索，到被 AI 看见</h2></header>
        <nav class="discovery-path" aria-label="GEO 核心工作流">
          <button v-for="item in discoverySteps" :key="item.index" type="button" @click="router.push(item.route)">
            <el-icon><component :is="item.icon" /></el-icon><span>{{ item.index }}</span>
            <div><strong>{{ item.title }}</strong><small>{{ item.description }}</small></div>
            <el-icon class="step-arrow"><ArrowRight /></el-icon>
          </button>
        </nav>
      </section>

      <section class="proof-ledger" aria-label="诊断与成本摘要">
        <article>
          <span>READINESS / 01</span><strong>{{ formatScore(latestDiagnostic?.score) }}</strong>
          <div><h3>GEO 准备度</h3><p v-if="diagnosticChange != null" :class="{ negative: diagnosticChange < 0 }">{{ diagnosticChange >= 0 ? '提升' : '下降' }} {{ Math.abs(diagnosticChange).toFixed(1) }} 分，来自相邻两次诊断</p><p v-else>完成两次同口径诊断后显示复测变化</p></div>
        </article>
        <article>
          <span>ACTION / 02</span><strong>{{ actionGapCount }}</strong>
          <div><h3>优先行动</h3><p>P0 {{ latestDiagnostic?.gapCounts?.P0 || 0 }} · P1 {{ latestDiagnostic?.gapCounts?.P1 || 0 }}</p></div>
        </article>
        <article>
          <span>AI COST / 03</span><strong>{{ costDisplay }}</strong>
          <div><h3>模型成本凭证</h3><p>{{ costSummary?.knownTotalTokens || 0 }} 已知 Token · {{ costSummary?.unknownUsageCount || 0 }} 条用量未知</p></div>
        </article>
        <article>
          <span>DISTRIBUTION / 04</span><strong>{{ overview.summary.today_success || 0 }}</strong>
          <div><h3>今日发布成功</h3><p>{{ overview.summary.today_published || 0 }} 条任务 · {{ overview.summary.today_failed || 0 }} 条失败</p></div>
        </article>
      </section>

      <section class="recent-section" aria-labelledby="recent-title">
        <header class="section-heading">
          <div><p class="eyebrow">RECENT DISTRIBUTION</p><h2 id="recent-title">最近发布轨迹</h2></div>
          <button type="button" @click="router.push('/publish-center')">打开发布中心 <el-icon><ArrowRight /></el-icon></button>
        </header>
        <div v-if="overview.recent_jobs.length" class="recent-list">
          <article v-for="job in overview.recent_jobs.slice(0, 5)" :key="job.job_id">
            <span class="job-code">JOB-{{ String(job.job_id).padStart(5, '0') }}</span>
            <div><strong>{{ job.article_title }}</strong><small>{{ platformName(job.platform) }} · {{ job.demo ? '演示任务' : '真实任务' }}</small></div>
            <p>{{ job.message || defaultStatusMessage(job.status) }}</p>
            <span :class="['job-status', job.status]">{{ statusLabel(job.status) }}</span>
            <time>{{ formatDate(job.finished_at || job.created_at) }}</time>
          </article>
        </div>
        <div v-else class="recent-empty">
          <el-icon><Promotion /></el-icon><div><h3>还没有发布轨迹</h3><p>稿件就绪后，在发布中心选择渠道建立第一条任务。</p></div>
          <el-button @click="router.push('/content-library')">查看内容库</el-button>
        </div>
      </section>
    </template>
  </main>
</template>

<script setup>
import { computed, markRaw, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChatDotRound, CollectionTag, DataLine, DocumentChecked, InfoFilled, Link, Promotion, Refresh, Search, Warning } from '@element-plus/icons-vue'
import VisibilityTrendCanvas from '@/components/VisibilityTrendCanvas.vue'
import { dashboardApi } from '@/api/dashboard'
import { diagnosticsApi } from '@/api/diagnostics'
import { projectApi } from '@/api/project'
import { visibilityApi } from '@/api/visibility'
import { normalizeCommercialDiagnostic, normalizeCommercialDiagnosticHistory, normalizeProjectCostSummary } from '@/utils/commercialDiagnostic'
import { normalizeVisibilityHistory, normalizeVisibilityResult } from '@/utils/visibilityResult'
import { buildVisibilitySeries, diagnosticRetestDelta, latestCitationEvidence, visibilityDelta } from '@/utils/dashboardPresentation'

const router = useRouter()
const loading = ref(false)
const loadError = ref(false)
const hasLoaded = ref(false)
const insightsLoading = ref(false)
const insightError = ref('')
const projects = ref([])
const visibilityHistory = ref([])
const diagnosticHistory = ref([])
const latestVisibility = ref(null)
const latestDiagnostic = ref(null)
const costSummary = ref(null)

const overview = reactive({
  summary: { project_count: 0, today_generated: 0, today_published: 0, today_success: 0, today_failed: 0, connected_media: 0, demo_jobs_today: 0 },
  trend: [], platforms: [], recent_jobs: [], generated_at: ''
})

const activeProject = computed(() => projects.value[0] || null)
const hasProjects = computed(() => projects.value.length > 0 || Number(overview.summary.project_count || 0) > 0)
const showOnboarding = computed(() => hasLoaded.value && !hasProjects.value)
const visibilitySeries = computed(() => buildVisibilitySeries(visibilityHistory.value))
const visibilityScore = computed(() => latestVisibility.value?.summary?.mentionRate ?? visibilitySeries.value.at(-1)?.value ?? null)
const visibilityChange = computed(() => visibilityDelta(visibilitySeries.value))
const diagnosticChange = computed(() => diagnosticRetestDelta(diagnosticHistory.value))
const citationEvidence = computed(() => latestCitationEvidence(latestVisibility.value))
const actionGapCount = computed(() => Number(latestDiagnostic.value?.gapCounts?.P0 || 0) + Number(latestDiagnostic.value?.gapCounts?.P1 || 0))
const costDisplay = computed(() => {
  const costs = costSummary.value?.calculatedCosts || []
  if (!costs.length) return '—'
  return costs.map(item => `${item.currency} ${Number(item.amount).toFixed(4)}`).join(' + ')
})

const discoverySteps = [
  { index: '01', title: 'AI 可见度监测', description: '了解品牌在 AI 回答中的呈现', route: '/ai-visibility', icon: markRaw(Search) },
  { index: '02', title: '内容优化', description: '让内容更清晰、更容易被理解', route: '/geo-optimize', icon: markRaw(DocumentChecked) },
  { index: '03', title: '引用来源分析', description: '追踪引用，发现下一轮优化机会', route: '/ai-visibility', icon: markRaw(DataLine) }
]

const loadProjectInsights = async projectId => {
  insightsLoading.value = true
  insightError.value = ''
  visibilityHistory.value = []
  diagnosticHistory.value = []
  latestVisibility.value = null
  latestDiagnostic.value = null
  costSummary.value = null
  const [visibilityRows, diagnosticRows, costs] = await Promise.allSettled([
    visibilityApi.getRuns(projectId, 12, { suppressGlobalError: true }),
    diagnosticsApi.getRuns(projectId, 12, { suppressGlobalError: true }),
    diagnosticsApi.getProjectCostSummary(projectId, 30, { suppressGlobalError: true })
  ])
  const detailRequests = []
  if (visibilityRows.status === 'fulfilled') {
    visibilityHistory.value = normalizeVisibilityHistory(visibilityRows.value)
    const latestRun = visibilityHistory.value[0]
    if (latestRun) detailRequests.push(visibilityApi.getRun(latestRun.id, { suppressGlobalError: true }).then(response => { latestVisibility.value = normalizeVisibilityResult(response) }))
  }
  if (diagnosticRows.status === 'fulfilled') {
    diagnosticHistory.value = normalizeCommercialDiagnosticHistory(diagnosticRows.value)
    const latestRun = diagnosticHistory.value[0]
    if (latestRun) detailRequests.push(diagnosticsApi.getRun(latestRun.id, { suppressGlobalError: true }).then(response => { latestDiagnostic.value = normalizeCommercialDiagnostic(response) }))
  }
  if (costs.status === 'fulfilled') costSummary.value = normalizeProjectCostSummary(costs.value)
  const detailResults = await Promise.allSettled(detailRequests)
  const failedParts = [visibilityRows, diagnosticRows, costs, ...detailResults].filter(item => item.status === 'rejected').length
  if (failedParts) insightError.value = `${failedParts} 项项目凭证暂未载入；页面只展示已确认的数据。`
  insightsLoading.value = false
}

const fetchOverview = async () => {
  loading.value = true
  loadError.value = false
  insightError.value = ''
  const [overviewResult, projectResult] = await Promise.allSettled([dashboardApi.getOverview(), projectApi.getProjects()])
  if (overviewResult.status === 'fulfilled') Object.assign(overview, overviewResult.value.data)
  else loadError.value = true
  if (projectResult.status === 'fulfilled') projects.value = Array.isArray(projectResult.value.data) ? projectResult.value.data : []
  else insightError.value = '品牌项目暂未载入，诊断与引用概览不可用。'
  hasLoaded.value = true
  loading.value = false
  if (activeProject.value) await loadProjectInsights(activeProject.value.id)
}

const parseDate = value => {
  if (!value) return null
  const text = String(value)
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text) ? `${text.replace(' ', 'T')}Z` : text.includes(' ') ? text.replace(' ', 'T') : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}
const formatDate = value => {
  const date = parseDate(value)
  return date ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date) : '时间未知'
}
const shortDate = value => { const date = parseDate(value); return date ? `${date.getMonth() + 1}/${date.getDate()}` : '记录' }
const formatScore = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(1)
const platformLabels = { zhihu: '知乎', toutiao: '今日头条', baijiahao: '百家号', sohu: '搜狐号', xiaohongshu: '小红书', douyin: '抖音', kuaishou: '快手', bilibili: 'Bilibili', channels: '视频号', tiktok: 'TikTok' }
const statusLabels = { queued: '排队中', processing: '发布中', success: '发布成功', failed: '发布失败', need_action: '等待确认', scheduled: '计划中' }
const platformName = key => platformLabels[key] || key
const statusLabel = status => statusLabels[status] || status
const defaultStatusMessage = status => ({ queued: '等待执行器领取', processing: '平台正在处理', success: '发布流程已完成', failed: '发布流程失败', need_action: '需要人工确认后继续', scheduled: '等待计划时间' }[status] || '状态待确认')

fetchOverview()
</script>

<style lang="scss" scoped>
.geo-home { --ink:#111214; --secondary:#505257; --muted:#85878c; --line:#deded9; --soft:#f4f4f1; --paper:#fff; width:min(100%,1420px); margin:0 auto; color:var(--ink); font-family:"Segoe UI Variable Text","Microsoft YaHei UI","PingFang SC",sans-serif; }
.eyebrow { margin:0; color:#77797e; font:600 11px/1.2 "Cascadia Mono",Consolas,monospace; letter-spacing:.16em; }
.hero-grid { display:grid; grid-template-columns:minmax(420px,.92fr) minmax(560px,1.08fr); gap:clamp(40px,6vw,92px); align-items:center; min-height:590px; padding:42px 40px 46px; border-bottom:1px solid var(--line); }
.hero-copy h1,.first-run-copy h1 { margin:22px 0 0; font-family:"Microsoft YaHei UI","PingFang SC",sans-serif; font-size:clamp(58px,5.25vw,84px); font-weight:900; line-height:1.06; letter-spacing:-.07em; }
.hero-summary { max-width:560px; margin:24px 0 0; color:#74767b; font-size:clamp(18px,1.5vw,25px); line-height:1.55; letter-spacing:.01em; }
.hero-actions { display:flex; gap:12px; margin-top:28px; flex-wrap:wrap; }
.hero-actions :deep(.el-button) { min-width:174px; height:54px; margin:0; border-color:var(--ink); border-radius:8px; color:var(--ink); font-size:15px; font-weight:600; }
.hero-actions :deep(.el-button--primary) { background:var(--ink); color:#fff; }
.hero-meta { display:flex; align-items:center; gap:9px; margin:14px 0 0; color:#8b8d91; font-size:12px; }
.hero-meta i { width:3px; height:3px; border-radius:50%; background:#b3b4b7; }
.visibility-card { min-width:0; padding:22px; border:1px solid var(--line); border-radius:10px; background:rgba(255,255,255,.94); box-shadow:0 22px 58px rgba(17,18,20,.07); }
.card-title-row,.visibility-index>header,.citation-snapshot>header,.section-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; }
.card-title-row h2 { margin:8px 0 0; font-size:19px; letter-spacing:-.03em; }
.data-badge,.citation-snapshot>header span { padding:7px 11px; border:1px solid #e3e3df; border-radius:7px; background:#f5f5f2; color:#6f7175; font-size:11px; white-space:nowrap; }
.visibility-index,.citation-snapshot { margin-top:14px; padding:18px; border:1px solid #e4e4e0; border-radius:8px; background:#fff; }
.visibility-index>header>div:first-child>span { display:flex; align-items:center; gap:5px; color:#5d5f64; font-size:12px; }
.score-line { display:flex; align-items:baseline; gap:14px; margin-top:5px; }
.score-line strong { font:800 42px/1 "Segoe UI Variable Display","Microsoft YaHei UI",sans-serif; letter-spacing:-.05em; }
.score-line em,.proof-ledger article>div p { color:#66686d; font-size:11px; font-style:normal; }
.score-line em.negative,.proof-ledger .negative { color:#9c433c; }
.range-control { display:flex; align-items:center; border:1px solid #e5e5e2; border-radius:7px; overflow:hidden; }
.range-control span,.range-control strong { padding:7px 9px; color:#7a7c81; font-size:10px; }
.range-control strong { background:#f2f2ef; color:var(--ink); }
.citation-snapshot h3 { margin:2px 0 0; font-size:16px; }
.citation-content,.citation-empty { display:grid; grid-template-columns:40px minmax(0,1fr); gap:14px; align-items:start; margin-top:16px; }
.citation-content>.el-icon,.citation-empty>.el-icon { width:40px; height:40px; border-radius:50%; background:#efefec; color:#4e5054; font-size:18px; }
.citation-content p,.citation-empty p { margin:0; color:#424449; font-size:12px; line-height:1.75; }
.citation-content footer { display:flex; align-items:center; gap:7px; margin-top:12px; color:#929499; font-size:10px; }
.citation-content footer a { display:inline-flex; align-items:center; gap:4px; color:#1e63d6; font-weight:600; }
.citation-content footer small { margin-left:auto; }
.citation-empty button { grid-column:2; width:fit-content; color:var(--ink); font-size:11px; font-weight:700; }
.insight-warning { margin:12px 0 0; color:#956725; font-size:10px; line-height:1.5; }
.discovery-section { padding:34px 40px 0; }
.discovery-section>header h2,.section-heading h2 { margin:9px 0 0; font-size:clamp(26px,2.5vw,36px); letter-spacing:-.045em; }
.discovery-path { display:grid; grid-template-columns:repeat(3,1fr); margin-top:24px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.discovery-path button { display:grid; grid-template-columns:48px 28px minmax(0,1fr) 20px; gap:14px; align-items:center; min-height:128px; padding:22px 24px; border-right:1px solid var(--line); color:var(--ink); text-align:left; transition:background 160ms ease,transform 160ms ease; }
.discovery-path button:last-child { border-right:0; }
.discovery-path button:hover { background:#f4f4f1; transform:translateY(-2px); }
.discovery-path button>.el-icon:first-child { font-size:34px; }
.discovery-path button>span { align-self:start; color:#8a8c91; font:500 11px/1 "Cascadia Mono",monospace; }
.discovery-path button div { display:flex; flex-direction:column; gap:7px; }
.discovery-path button strong { font-size:17px; }
.discovery-path button small { color:#7b7d82; font-size:12px; line-height:1.5; }
.discovery-path .step-arrow { justify-self:end; color:#9c9da0; }
.proof-ledger { display:grid; grid-template-columns:repeat(4,1fr); margin:30px 40px 0; border:1px solid var(--line); border-radius:9px; background:#fff; overflow:hidden; }
.proof-ledger article { display:grid; grid-template-columns:1fr; min-height:150px; padding:20px; border-right:1px solid var(--line); }
.proof-ledger article:last-child { border-right:0; }
.proof-ledger article>span { color:#8a8c91; font:600 9px/1 "Cascadia Mono",monospace; letter-spacing:.1em; }
.proof-ledger article>strong { align-self:center; overflow-wrap:anywhere; font-size:clamp(28px,2.4vw,38px); letter-spacing:-.05em; }
.proof-ledger article>div h3 { margin:0; font-size:13px; }
.proof-ledger article>div p { margin:5px 0 0; line-height:1.45; }
.recent-section { margin:30px 40px 54px; padding-top:30px; border-top:1px solid var(--line); }
.section-heading button { display:inline-flex; align-items:center; gap:6px; padding:9px 0; color:var(--ink); font-size:12px; font-weight:700; }
.recent-list { margin-top:22px; border-top:1px solid var(--line); }
.recent-list article { display:grid; grid-template-columns:108px minmax(190px,.85fr) minmax(220px,1fr) 90px 92px; gap:18px; align-items:center; min-height:72px; border-bottom:1px solid var(--line); }
.job-code { color:#77797e; font:600 9px/1 "Cascadia Mono",monospace; }
.recent-list article>div { display:flex; min-width:0; flex-direction:column; gap:4px; }
.recent-list article>div strong { overflow:hidden; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.recent-list article>div small,.recent-list article>p,.recent-list article>time { color:#7a7c81; font-size:10px; }
.recent-list article>p { overflow:hidden; margin:0; text-overflow:ellipsis; white-space:nowrap; }
.job-status { width:fit-content; padding:5px 7px; border:1px solid #cacbc8; border-radius:5px; color:#66686c; font-size:9px; }
.job-status.success { border-color:#9fc0aa; color:#32674d; }.job-status.failed{border-color:#d7aaa7;color:#963e37}.job-status.need_action{border-color:#d7bd8f;color:#8a6424}
.recent-empty { display:flex; align-items:center; gap:17px; min-height:130px; margin-top:22px; padding:22px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.recent-empty>.el-icon { font-size:30px; }.recent-empty div{flex:1}.recent-empty h3{margin:0;font-size:14px}.recent-empty p{margin:5px 0 0;color:#77797e;font-size:11px}
.load-state { display:grid; grid-template-columns:52px minmax(0,1fr) auto; gap:20px; align-items:center; min-height:260px; margin:50px 40px; padding:34px; border:1px solid #d9b5b2; border-radius:10px; background:#fff8f7; }
.load-state>.el-icon{font-size:40px;color:#963e37}.load-state h1{margin:8px 0 0;font-size:28px}.load-state div>p:last-child{margin:8px 0 0;color:#77797e;font-size:12px}
.first-run { min-height:calc(100vh - 105px); padding:54px 40px; }.first-run-copy{max-width:720px}.first-run-copy>small{display:block;margin-top:14px;color:#85878c;font-size:11px}
.first-run-steps{display:grid;grid-template-columns:repeat(3,1fr);margin-top:60px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.first-run-steps article{display:grid;min-height:210px;grid-template-columns:1fr auto;padding:24px;border-right:1px solid var(--line)}.first-run-steps article:last-child{border-right:0}
.first-run-steps span{color:#85878c;font:600 9px/1 "Cascadia Mono",monospace}.first-run-steps .el-icon{font-size:28px}.first-run-steps h2{grid-column:1/-1;align-self:end;margin:0;font-size:18px}.first-run-steps p{grid-column:1/-1;margin:10px 0 0;color:#77797e;font-size:12px;line-height:1.65}
@media (prefers-reduced-motion:reduce){.discovery-path button{transition:none}}
@media (max-width:1600px){.hero-grid{grid-template-columns:1fr}.hero-copy{max-width:760px}.visibility-card{width:100%}}
@media (max-width:1180px){.proof-ledger{grid-template-columns:repeat(2,1fr)}.proof-ledger article:nth-child(2){border-right:0}.proof-ledger article:nth-child(-n+2){border-bottom:1px solid var(--line)}.recent-list article{grid-template-columns:100px minmax(180px,.8fr) minmax(200px,1fr) 90px}.recent-list article>time{display:none}}
@media (max-width:820px){.hero-grid,.discovery-section,.first-run{padding-right:20px;padding-left:20px}.proof-ledger,.recent-section{margin-right:20px;margin-left:20px}.hero-grid{grid-template-columns:minmax(0,1fr);padding-top:30px}.hero-copy h1,.first-run-copy h1{font-size:clamp(48px,12vw,68px)}.discovery-path,.first-run-steps{grid-template-columns:1fr}.discovery-path button,.first-run-steps article{border-right:0;border-bottom:1px solid var(--line)}.discovery-path button:last-child,.first-run-steps article:last-child{border-bottom:0}.recent-list article{grid-template-columns:90px 1fr auto;gap:12px;padding:12px 0}.recent-list article>p{grid-column:2/-1;grid-row:2}}
@media (max-width:560px){.hero-grid{padding-right:14px;padding-left:14px}.discovery-section,.first-run{padding-right:14px;padding-left:14px}.proof-ledger,.recent-section{margin-right:14px;margin-left:14px}.hero-actions{flex-direction:column}.hero-actions :deep(.el-button){width:100%}.hero-meta{align-items:flex-start;flex-direction:column}.hero-meta i{display:none}.visibility-card{padding:13px}.visibility-index,.citation-snapshot{padding:13px}.visibility-index>header{align-items:flex-start;flex-direction:column}.citation-content footer{align-items:flex-start;flex-direction:column}.citation-content footer small{margin-left:0}.proof-ledger{grid-template-columns:1fr}.proof-ledger article,.proof-ledger article:nth-child(2){border-right:0;border-bottom:1px solid var(--line)}.proof-ledger article:last-child{border-bottom:0}.recent-list article{grid-template-columns:1fr auto}.job-code{grid-column:1/-1}.recent-list article>p{grid-column:1/-1}.load-state{grid-template-columns:1fr;margin:20px 14px}}
</style>
