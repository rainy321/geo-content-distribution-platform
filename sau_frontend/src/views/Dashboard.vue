<template>
  <main class="operations-dashboard">
    <header class="dashboard-heading">
      <div class="heading-copy">
        <p class="eyebrow">OPERATIONS BRIEF / {{ todayLabel }}</p>
        <h1>今日运营简报</h1>
        <p>把内容生产、渠道连接和发布结果放在同一张工作台上，优先处理真正阻塞分发的事项。</p>
      </div>
      <div class="heading-actions">
        <span v-if="overview.generated_at" class="sync-time">数据更新 {{ formatTime(overview.generated_at) }}</span>
        <el-button :icon="Refresh" :loading="loading" @click="fetchOverview">刷新数据</el-button>
        <el-button type="primary" :icon="MagicStick" @click="router.push('/content-create')">生成新内容</el-button>
      </div>
    </header>

    <section class="metric-ledger" aria-label="今日核心指标">
      <article v-for="metric in metrics" :key="metric.key" :class="['metric-cell', metric.tone]">
        <div class="metric-index">{{ metric.index }}</div>
        <div class="metric-value">{{ metric.value }}</div>
        <div class="metric-copy">
          <strong>{{ metric.label }}</strong>
          <span>{{ metric.note }}</span>
        </div>
      </article>
    </section>

    <section v-if="loadError" class="dashboard-error" role="alert">
      <span>!</span>
      <div>
        <h2>运营数据没有载入</h2>
        <p>检查后端服务后重新加载，已有文章和发布任务不会受到影响。</p>
      </div>
      <el-button type="primary" @click="fetchOverview">重新加载</el-button>
    </section>

    <div v-else class="dashboard-grid" v-loading="loading && !hasLoaded">
      <section class="trend-panel" aria-labelledby="trend-title">
        <div class="panel-heading">
          <div>
            <span>TREND / 01</span>
            <h2 id="trend-title">最近 7 天发布趋势</h2>
            <p>生成量反映内容供给，任务量反映分发动作，成功量反映最终闭环。</p>
          </div>
          <div class="chart-legend" aria-label="趋势图图例">
            <span><i class="generated" />生成</span>
            <span><i class="published" />任务</span>
            <span><i class="success" />成功</span>
          </div>
        </div>

        <div class="trend-chart">
          <div class="chart-scale" aria-hidden="true">
            <span>{{ trendMax }}</span>
            <span>{{ Math.ceil(trendMax / 2) }}</span>
            <span>0</span>
          </div>
          <div class="chart-days">
            <div v-for="day in overview.trend" :key="day.date" class="day-column">
              <div class="bar-stage" :aria-label="trendAriaLabel(day)">
                <span class="grid-line top" />
                <span class="grid-line middle" />
                <div class="bar generated" :style="{ height: barHeight(day.generated) }"><i>{{ day.generated || '' }}</i></div>
                <div class="bar published" :style="{ height: barHeight(day.published) }"><i>{{ day.published || '' }}</i></div>
                <div class="bar success" :style="{ height: barHeight(day.success) }"><i>{{ day.success || '' }}</i></div>
              </div>
              <time :datetime="day.date">{{ day.label }}</time>
            </div>
          </div>
        </div>
      </section>

      <aside class="platform-panel" aria-labelledby="platform-title">
        <div class="panel-heading platform-heading">
          <div>
            <span>CHANNELS / 02</span>
            <h2 id="platform-title">媒体连接</h2>
            <p>仅统计 Cookie 状态正常的账号。</p>
          </div>
        </div>

        <div class="platform-list">
          <article v-for="platform in overview.platforms" :key="platform.key" class="platform-row">
            <div :class="['connection-dot', { connected: platform.connected }]" />
            <div>
              <strong>{{ platform.name }}</strong>
              <span>{{ platform.connected ? `${platform.account_count} 个可用账号` : '尚未连接' }}</span>
            </div>
            <small>{{ platform.connected ? 'ONLINE' : 'OFFLINE' }}</small>
          </article>
        </div>

        <button type="button" class="manage-link" @click="router.push('/account-management')">
          管理媒体账号 <span>→</span>
        </button>
      </aside>
    </div>

    <section v-if="!loadError" class="recent-panel" aria-labelledby="recent-title">
      <div class="panel-heading recent-heading">
        <div>
          <span>RECENT / 03</span>
          <h2 id="recent-title">最近发布</h2>
          <p>按最后活动时间排列；演示任务不会伪装成真实发布。</p>
        </div>
        <el-button text :icon="Promotion" @click="router.push('/publish-center')">打开发布中心</el-button>
      </div>

      <div v-if="overview.recent_jobs.length" class="recent-list">
        <article v-for="job in overview.recent_jobs" :key="job.job_id" class="recent-row">
          <div :class="['status-mark', job.status]">
            <i />
            <span>{{ statusLabel(job.status) }}</span>
          </div>
          <div class="recent-title">
            <strong>{{ job.article_title }}</strong>
            <small>JOB-{{ String(job.job_id).padStart(5, '0') }}</small>
          </div>
          <div class="recent-platform">
            <span>{{ platformName(job.platform) }}</span>
            <small v-if="job.demo">DEMO / 演示</small>
          </div>
          <p>{{ job.message || defaultStatusMessage(job.status) }}</p>
          <time>{{ formatDate(job.finished_at || job.created_at) }}</time>
        </article>
      </div>

      <div v-else class="recent-empty">
        <div class="empty-route" aria-hidden="true"><span /></div>
        <div>
          <h3>还没有发布轨迹</h3>
          <p>稿件标记就绪后，在发布中心选择渠道建立第一条任务。</p>
        </div>
        <el-button type="primary" @click="router.push('/content-library')">查看内容库</el-button>
      </div>
    </section>

    <nav class="workflow-shortcuts" aria-label="内容工作流快捷入口">
      <button type="button" @click="router.push('/projects')">
        <span>01</span><strong>品牌底稿</strong><small>维护事实和关键词</small>
      </button>
      <i>→</i>
      <button type="button" @click="router.push('/content-create')">
        <span>02</span><strong>内容生产</strong><small>生成、评分与保存</small>
      </button>
      <i>→</i>
      <button type="button" @click="router.push('/publish-center')">
        <span>03</span><strong>渠道分发</strong><small>追踪状态和重试</small>
      </button>
    </nav>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick, Promotion, Refresh } from '@element-plus/icons-vue'
import { dashboardApi } from '@/api/dashboard'

const router = useRouter()
const loading = ref(false)
const loadError = ref(false)
const hasLoaded = ref(false)

const overview = reactive({
  summary: {
    today_generated: 0,
    today_published: 0,
    today_success: 0,
    today_failed: 0,
    connected_media: 0,
    demo_jobs_today: 0
  },
  trend: [],
  platforms: [],
  recent_jobs: [],
  generated_at: ''
})

const todayLabel = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short'
}).format(new Date())

const metrics = computed(() => [
  { key: 'generated', index: '01', label: '今日生成文章', value: overview.summary.today_generated, note: '进入内容资产库', tone: 'teal' },
  {
    key: 'published', index: '02', label: '今日发布任务', value: overview.summary.today_published,
    note: overview.summary.demo_jobs_today ? `其中 ${overview.summary.demo_jobs_today} 条演示任务` : '跨平台任务总量', tone: 'blue'
  },
  { key: 'success', index: '03', label: '发布成功', value: overview.summary.today_success, note: '按完成时间统计', tone: 'green' },
  { key: 'failed', index: '04', label: '发布失败', value: overview.summary.today_failed, note: '可在发布中心重试', tone: 'red' },
  { key: 'connected', index: '05', label: '已连接媒体', value: overview.summary.connected_media, note: 'Cookie 状态正常', tone: 'amber' }
])

const trendMax = computed(() => {
  const values = overview.trend.flatMap(day => [day.generated, day.published, day.success])
  return Math.max(1, ...values)
})

const fetchOverview = async () => {
  loading.value = true
  loadError.value = false
  try {
    const response = await dashboardApi.getOverview()
    Object.assign(overview, response.data)
    hasLoaded.value = true
  } catch (error) {
    loadError.value = true
    console.error('加载 Dashboard 失败:', error)
  } finally {
    loading.value = false
  }
}

const barHeight = value => value ? `${Math.max(8, (value / trendMax.value) * 100)}%` : '0%'
const trendAriaLabel = day => `${day.date}：生成 ${day.generated}，发布任务 ${day.published}，成功 ${day.success}`

const platformLabels = {
  zhihu: '知乎', toutiao: '今日头条', baijiahao: '百家号', sohu: '搜狐号', xiaohongshu: '小红书',
  douyin: '抖音', kuaishou: '快手', bilibili: 'Bilibili', channels: '视频号', tiktok: 'TikTok'
}
const statusLabels = {
  queued: '排队中', processing: '发布中', success: '成功', failed: '失败', need_action: '等待确认', scheduled: '计划中'
}

const platformName = key => platformLabels[key] || key
const statusLabel = status => statusLabels[status] || status
const defaultStatusMessage = status => ({
  queued: '等待执行器领取', processing: '平台正在处理', success: '发布流程已完成',
  failed: '发布流程失败', need_action: '需要人工确认后继续', scheduled: '等待计划时间'
}[status] || '状态待确认')

const parseDate = value => {
  if (!value) return null
  const text = String(value)
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(' ', 'T')}Z`
    : text.includes(' ')
      ? text.replace(' ', 'T')
      : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}
const formatDate = value => {
  const date = parseDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(date)
}
const formatTime = value => {
  const date = parseDate(value)
  return date ? date.toLocaleTimeString('zh-CN', { hour12: false }) : '—'
}

onMounted(fetchOverview)
</script>

<style lang="scss" scoped>
.operations-dashboard {
  --harbor: #102b31;
  --signal: #39b8b2;
  --ink: #173038;
  --muted: #6d7d82;
  --rule: #d5dfdc;
  --amber: #c9852d;
  --red: #bf5a58;
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

.dashboard-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 30px;
  padding: 8px 4px 20px;
  border-bottom: 1px solid #cad6d2;
}

.heading-copy {
  max-width: 700px;
  .eyebrow { margin: 0 0 9px; color: #318f8b; font: 700 11px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.13em; }
  h1 { margin: 0; color: var(--harbor); font: 700 clamp(34px, 4vw, 50px)/0.98 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; letter-spacing: -0.035em; }
  > p:last-child { margin: 13px 0 0; color: var(--muted); font-size: 14px; line-height: 1.7; }
}

.heading-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  .sync-time { width: 100%; color: var(--muted); font: 600 10px/1 "Cascadia Mono", monospace; text-align: right; }
  :deep(.el-button) { border-radius: 3px; }
  :deep(.el-button--primary) { background: var(--harbor); border-color: var(--harbor); }
}

.metric-ledger {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin-top: 22px;
  background: #fff;
  border: 1px solid var(--rule);
  box-shadow: 0 13px 36px rgba(20, 50, 57, 0.055);
}

.metric-cell {
  position: relative;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 14px;
  min-height: 116px;
  padding: 20px;
  border-right: 1px solid var(--rule);
  box-sizing: border-box;
  &:last-child { border-right: 0; }
  &::after { content: ''; position: absolute; right: 20px; bottom: 0; left: 20px; height: 3px; background: #88aaa6; }
  &.blue::after { background: #649bb9; }
  &.green::after { background: var(--signal); }
  &.red::after { background: var(--red); }
  &.amber::after { background: var(--amber); }
}

.metric-index { padding-top: 5px; color: #61908d; font: 700 9px/1 "Cascadia Mono", monospace; }
.metric-value { color: var(--harbor); font: 700 38px/0.9 "Bahnschrift SemiCondensed", sans-serif; text-align: right; }
.metric-copy {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  strong { font-size: 13px; }
  span { color: var(--muted); font-size: 10px; }
}

.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(280px, 0.65fr); gap: 22px; margin-top: 22px; }
.trend-panel, .platform-panel, .recent-panel { background: #fff; border: 1px solid var(--rule); }
.trend-panel { min-height: 390px; padding: 26px; }
.platform-panel { display: flex; flex-direction: column; padding: 26px; background: var(--harbor); color: #edf8f6; }

.panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  > div > span { color: #318f8b; font: 700 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 9px 0 0; color: var(--harbor); font: 700 24px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
}

.chart-legend {
  display: flex;
  gap: 13px;
  color: var(--muted);
  font-size: 10px;
  span { display: flex; align-items: center; gap: 5px; }
  i { width: 8px; height: 8px; background: #87aba8; }
  i.published { background: #649bb9; }
  i.success { background: var(--signal); }
}

.trend-chart { display: grid; grid-template-columns: 26px 1fr; gap: 12px; margin-top: 30px; }
.chart-scale { height: 245px; display: flex; flex-direction: column; justify-content: space-between; color: #80908f; font: 600 9px/1 "Cascadia Mono", monospace; }
.chart-days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; }
.day-column {
  min-width: 0;
  text-align: center;
  time { display: block; margin-top: 10px; color: #647579; font: 600 9px/1 "Cascadia Mono", monospace; }
}
.bar-stage { position: relative; height: 245px; display: flex; align-items: flex-end; justify-content: center; gap: clamp(3px, 0.7vw, 8px); border-bottom: 1px solid #bfcac8; }
.grid-line { position: absolute; right: 0; left: 0; height: 1px; background: #e4eae8; }
.grid-line.top { top: 0; }
.grid-line.middle { top: 50%; }
.bar {
  position: relative;
  z-index: 1;
  width: clamp(7px, 1vw, 14px);
  background: #87aba8;
  transition: height 260ms ease;
  &.published { background: #649bb9; }
  &.success { background: var(--signal); }
  i { position: absolute; top: -15px; left: 50%; color: #5d7075; font: 700 8px/1 "Cascadia Mono", monospace; font-style: normal; transform: translateX(-50%); }
}

.platform-heading {
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);
  > div > span { color: #79d8d1; }
  h2 { color: #edf8f6; }
  p { color: rgba(222, 240, 237, 0.58); }
}
.platform-list { margin-top: 13px; }
.platform-row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 11px;
  align-items: center;
  padding: 13px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  > div:nth-child(2) { display: flex; flex-direction: column; gap: 4px; }
  strong { font-size: 12px; }
  span { color: rgba(222, 240, 237, 0.55); font-size: 9px; }
  small { color: rgba(222, 240, 237, 0.38); font: 700 8px/1 "Cascadia Mono", monospace; }
}
.connection-dot {
  width: 9px;
  height: 9px;
  border: 1px solid #849795;
  border-radius: 50%;
  &.connected { border-color: var(--signal); background: var(--signal); box-shadow: 0 0 0 4px rgba(57, 184, 178, 0.12); }
}
.manage-link { display: flex; justify-content: space-between; margin-top: auto; padding: 15px 0 0; border: 0; background: transparent; color: #8ce3dc; cursor: pointer; font-size: 11px; text-align: left; }

.recent-panel { margin-top: 22px; padding: 0 26px; }
.recent-heading { align-items: center; padding: 23px 0; border-bottom: 1px solid var(--rule); }
.recent-row {
  display: grid;
  grid-template-columns: 92px minmax(180px, 1.25fr) 116px minmax(200px, 1.2fr) 100px;
  gap: 18px;
  align-items: center;
  min-height: 72px;
  border-bottom: 1px solid #e5ebe9;
  &:last-child { border-bottom: 0; }
  p { overflow: hidden; margin: 0; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  > time { color: #536d73; font: 600 9px/1.3 "Cascadia Mono", monospace; text-align: right; }
}
.status-mark {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 10px;
  i { width: 19px; height: 3px; background: #829593; }
  &.success i { background: var(--signal); }
  &.failed i { background: var(--red); }
  &.need_action i, &.scheduled i { background: var(--amber); }
  &.processing i, &.queued i { background: #649bb9; }
}
.recent-title, .recent-platform {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  small { color: #5a8784; font: 700 8px/1 "Cascadia Mono", monospace; }
  > span { font-size: 11px; font-weight: 700; }
}
.recent-empty { min-height: 210px; display: flex; align-items: center; justify-content: center; gap: 20px; }
.recent-empty h3 { margin: 0; font-size: 16px; }
.recent-empty p { margin: 7px 0 0; color: var(--muted); font-size: 11px; }
.empty-route {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border: 1px solid #9db8b4;
  border-radius: 50%;
  span { width: 18px; height: 1px; background: var(--signal); box-shadow: 6px -5px 0 -2px var(--signal); }
}

.dashboard-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 20px;
  min-height: 240px;
  margin-top: 22px;
  background: #fff;
  border: 1px solid #dfc4c3;
  > span { width: 48px; height: 48px; display: grid; place-items: center; border: 1px solid var(--red); color: var(--red); font: 700 22px/1 "Cascadia Mono", monospace; }
  h2 { margin: 0; font-size: 17px; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 11px; }
}

.workflow-shortcuts {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr;
  align-items: stretch;
  gap: 10px;
  margin-top: 22px;
  > i { align-self: center; color: #75a29e; font-style: normal; }
  button { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; padding: 15px 17px; border: 1px solid var(--rule); background: rgba(255, 255, 255, 0.7); color: var(--ink); cursor: pointer; text-align: left; }
  button:hover { border-color: #78b9b5; background: #fff; }
  button:focus-visible { outline: 3px solid rgba(57, 184, 178, 0.28); outline-offset: 2px; }
  button span { grid-row: 1 / 3; color: #4c8d89; font: 700 9px/1 "Cascadia Mono", monospace; }
  button strong { font-size: 12px; }
  button small { color: var(--muted); font-size: 9px; }
}

@media (prefers-reduced-motion: reduce) { .bar { transition: none; } }

@media (max-width: 1050px) {
  .metric-ledger { grid-template-columns: repeat(3, 1fr); }
  .metric-cell:nth-child(3) { border-right: 0; }
  .metric-cell:nth-child(-n + 3) { border-bottom: 1px solid var(--rule); }
  .dashboard-grid { grid-template-columns: 1fr; }
  .platform-panel { min-height: 390px; }
  .recent-row { grid-template-columns: 82px 1fr 100px; padding: 12px 0; }
  .recent-row p { grid-column: 2; }
  .recent-row > time { grid-column: 3; grid-row: 2; }
}

@media (max-width: 760px) {
  .dashboard-heading { align-items: flex-start; flex-direction: column; }
  .heading-actions { width: 100%; justify-content: flex-start; }
  .heading-actions .sync-time { text-align: left; }
  .metric-ledger { grid-template-columns: repeat(2, 1fr); }
  .metric-cell, .metric-cell:nth-child(3) { border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
  .metric-cell:nth-child(even) { border-right: 0; }
  .metric-cell:last-child { grid-column: 1 / -1; border-right: 0; border-bottom: 0; }
  .trend-panel, .platform-panel, .recent-panel { padding-left: 18px; padding-right: 18px; }
  .panel-heading { align-items: flex-start; flex-direction: column; }
  .chart-days { gap: 4px; }
  .bar-stage { gap: 2px; }
  .recent-row { grid-template-columns: 80px 1fr; gap: 9px; }
  .recent-platform { grid-column: 1; }
  .recent-row p, .recent-row > time { grid-column: 2; grid-row: auto; text-align: left; }
  .recent-empty, .dashboard-error { align-items: flex-start; flex-direction: column; padding: 24px; }
  .workflow-shortcuts { grid-template-columns: 1fr; }
  .workflow-shortcuts > i { display: none; }
}
</style>
