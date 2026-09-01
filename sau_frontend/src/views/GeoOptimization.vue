<template>
  <main class="geo-optimization">
    <header class="lab-header">
      <div>
        <p class="eyebrow">GEO REVISION LAB / AI 修订</p>
        <h1>GEO 优化</h1>
        <p>让模型针对规则评分逐项修订，先对照原稿检查，再决定是否保存。</p>
      </div>
      <el-button @click="router.push('/content-library')">返回内容库</el-button>
    </header>

    <section class="article-selector" aria-labelledby="selector-title">
      <div class="selector-index">01</div>
      <div class="selector-copy">
        <span id="selector-title">选择待优化稿件</span>
        <small>最近 {{ articles.length }} 篇内容</small>
      </div>
      <el-select
        v-model="selectedArticleId"
        filterable
        placeholder="按标题选择文章"
        :loading="listLoading"
        :disabled="listError || articles.length === 0"
        @change="loadArticle"
      >
        <el-option
          v-for="article in articles"
          :key="article.id"
          :value="article.id"
          :label="article.title"
        >
          <div class="article-option">
            <strong>{{ article.title }}</strong>
            <span>{{ article.project_name }} · {{ statusLabel(article.status) }} · GEO {{ displayScore(article.geo_score) }}</span>
          </div>
        </el-option>
      </el-select>
      <el-button :icon="Refresh" :loading="listLoading || articleLoading" @click="refreshAll">刷新</el-button>
    </section>

    <section v-if="listError" class="page-state is-error" role="alert">
      <div class="state-symbol">!</div>
      <h2>文章目录没有载入</h2>
      <p>检查后端服务后重新加载，已有文章不会受到影响。</p>
      <el-button type="primary" @click="refreshAll">重新加载</el-button>
    </section>

    <section v-else-if="!listLoading && articles.length === 0" class="page-state">
      <div class="empty-document"><span /><span /><span /></div>
      <h2>还没有可优化的文章</h2>
      <p>先在 AI 内容创作中生成并保存一篇稿件。</p>
      <el-button type="primary" @click="router.push('/content-create')">开始创作</el-button>
    </section>

    <section v-else-if="articleLoading" class="revision-loading" aria-live="polite">
      <el-skeleton :rows="12" animated />
    </section>

    <template v-else-if="currentArticle">
      <section class="revision-heading">
        <div>
          <span>ARTICLE {{ String(currentArticle.id).padStart(4, '0') }}</span>
          <h2>{{ currentArticle.title }}</h2>
          <p>{{ currentArticle.project_name || selectedListArticle?.project_name }} · {{ statusLabel(currentArticle.status) }}</p>
        </div>
        <div class="revision-contract">
          <strong>优化约束</strong>
          <span>保持原意</span>
          <span>不编造数据</span>
          <span>不堆砌关键词</span>
        </div>
      </section>

      <section class="revision-stage">
        <article class="document-panel original-document">
          <div class="document-heading">
            <div>
              <span>ORIGINAL / 原稿</span>
              <strong>当前保存版本</strong>
            </div>
            <span :class="['status-tag', currentArticle.status]">{{ statusLabel(currentArticle.status) }}</span>
          </div>
          <div class="document-scroll">
            <h3>{{ currentArticle.title }}</h3>
            <p v-if="currentArticle.summary" class="document-summary">{{ currentArticle.summary }}</p>
            <pre>{{ currentArticle.content }}</pre>
          </div>
          <div class="document-tags">
            <span v-for="tag in currentArticle.tags" :key="tag"># {{ tag }}</span>
          </div>
        </article>

        <aside class="score-bridge" aria-label="优化前后评分对比">
          <div class="bridge-heading">
            <span>02 / SCORE DELTA</span>
            <strong>规则变化</strong>
          </div>

          <div class="score-transition">
            <div class="score-node before">
              <span>BEFORE</span>
              <strong>{{ displayScore(beforeScore?.score) }}</strong>
            </div>
            <div class="transition-line">
              <span v-if="afterScore" :class="scoreTrendClass">{{ scoreDeltaText }}</span>
            </div>
            <div :class="['score-node', 'after', { pending: !afterScore }]">
              <span>AFTER</span>
              <strong>{{ afterScore ? displayScore(afterScore.score) : '—' }}</strong>
            </div>
          </div>

          <div class="dimension-compare">
            <div v-for="item in dimensionComparison" :key="item.key" class="dimension-item">
              <div>
                <span>{{ item.label }}</span>
                <strong>{{ item.before }}<i>→</i>{{ afterScore ? item.after : '—' }}</strong>
              </div>
              <div class="compare-track">
                <span class="before-bar" :style="{ width: `${item.before}%` }" />
                <span v-if="afterScore" class="after-bar" :style="{ width: `${item.after}%` }" />
              </div>
            </div>
          </div>

          <div class="suggestion-brief">
            <span>MODEL BRIEF</span>
            <ul v-if="beforeScore?.suggestions?.length">
              <li v-for="suggestion in beforeScore.suggestions" :key="suggestion">{{ suggestion }}</li>
            </ul>
            <p v-else>当前规则项已覆盖，模型只需改善表达与可读性。</p>
          </div>
        </aside>

        <article :class="['document-panel', 'optimized-document', { waiting: !optimizedArticle }]">
          <template v-if="optimizing">
            <div class="optimizing-state" aria-live="polite">
              <div class="revision-pulse"><MagicStick /></div>
              <span>03 / REVISING</span>
              <h3>模型正在逐项修订</h3>
              <p>原稿、分数和建议已一起发送，结果不会自动保存。</p>
              <el-skeleton :rows="9" animated />
            </div>
          </template>

          <template v-else-if="optimizedArticle">
            <div class="document-heading">
              <div>
                <span>REVISION / 优化稿</span>
                <strong>{{ optimizationSaved ? '已保存版本' : '待确认预览' }}</strong>
              </div>
              <span v-if="optimizationSaved" class="saved-mark">已保存</span>
            </div>
            <div class="document-scroll">
              <h3>{{ optimizedArticle.title }}</h3>
              <p v-if="optimizedArticle.summary" class="document-summary">{{ optimizedArticle.summary }}</p>
              <pre>{{ optimizedArticle.content }}</pre>
            </div>
            <div class="document-tags">
              <span v-for="tag in optimizedArticle.tags" :key="tag"># {{ tag }}</span>
            </div>
          </template>

          <template v-else>
            <div class="waiting-copy">
              <div class="waiting-mark">Δ</div>
              <span>REVISION / 待生成</span>
              <h3>这里将出现优化后的完整稿件</h3>
              <p>模型只会基于原文事实修订；生成后请先比较分数与正文，再决定是否覆盖保存。</p>
            </div>
          </template>
        </article>
      </section>

      <div v-if="optimizationError" class="optimization-error" role="alert">
        <strong>{{ optimizationError.title }}</strong>
        <p>{{ optimizationError.message }}</p>
      </div>

      <section class="action-dock">
        <div>
          <span>人工确认门</span>
          <p v-if="isWritableStatus">AI 优化只生成预览；点击保存后才会覆盖内容库中的原稿。</p>
          <p v-else>发布中或已发布稿件可以查看优化预览，但不能在这里覆盖保存。</p>
        </div>
        <div class="dock-buttons">
          <el-button
            :icon="MagicStick"
            :loading="optimizing"
            :disabled="optimizing || saving"
            @click="runOptimization"
          >
            {{ optimizedArticle ? '重新优化' : 'AI GEO 优化' }}
          </el-button>
          <el-button
            type="primary"
            :icon="FolderChecked"
            :loading="saving"
            :disabled="!optimizedArticle || optimizationSaved || !isWritableStatus"
            @click="saveOptimization"
          >
            {{ optimizationSaved ? '优化稿已保存' : '确认覆盖并保存' }}
          </el-button>
          <el-button
            v-if="optimizationSaved"
            :icon="EditPen"
            @click="openEditor"
          >
            进入编辑器审核
          </el-button>
        </div>
      </section>
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditPen, FolderChecked, MagicStick, Refresh } from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'

const route = useRoute()
const router = useRouter()
const articles = ref([])
const selectedArticleId = ref(null)
const currentArticle = ref(null)
const beforeScore = ref(null)
const optimizedArticle = ref(null)
const afterScore = ref(null)
const listLoading = ref(false)
const articleLoading = ref(false)
const optimizing = ref(false)
const saving = ref(false)
const listError = ref(false)
const optimizationError = ref(null)
const optimizationSaved = ref(false)
let articleRequestSequence = 0

const dimensionLabels = [
  { key: 'entity', label: '品牌实体' },
  { key: 'keywords', label: '关键词' },
  { key: 'structure', label: '内容结构' },
  { key: 'faq', label: 'FAQ' },
  { key: 'citation', label: '引用友好' }
]

const selectedListArticle = computed(() => (
  articles.value.find(article => article.id === selectedArticleId.value) || null
))

const isWritableStatus = computed(() => (
  currentArticle.value && ['draft', 'ready'].includes(currentArticle.value.status)
))

const dimensionComparison = computed(() => dimensionLabels.map(item => ({
  ...item,
  before: beforeScore.value?.dimensions?.[item.key] || 0,
  after: afterScore.value?.dimensions?.[item.key] || 0
})))

const scoreDelta = computed(() => {
  if (!afterScore.value || !beforeScore.value) return 0
  return Number(afterScore.value.score) - Number(beforeScore.value.score)
})

const scoreDeltaText = computed(() => {
  if (scoreDelta.value > 0) return `+${scoreDelta.value}`
  return String(scoreDelta.value)
})

const scoreTrendClass = computed(() => ({
  positive: scoreDelta.value > 0,
  neutral: scoreDelta.value === 0,
  negative: scoreDelta.value < 0
}))

const fetchArticleList = async () => {
  listLoading.value = true
  listError.value = false
  try {
    const response = await articleApi.getArticles({ page: 1, page_size: 100 })
    articles.value = response.data?.items || []
  } catch (error) {
    listError.value = true
    console.error('加载待优化文章失败:', error)
  } finally {
    listLoading.value = false
  }
}

const loadArticle = async (articleId) => {
  const normalizedId = Number(articleId)
  if (!Number.isInteger(normalizedId) || normalizedId <= 0) return
  const sequence = ++articleRequestSequence
  selectedArticleId.value = normalizedId
  articleLoading.value = true
  optimizationError.value = null
  optimizedArticle.value = null
  afterScore.value = null
  optimizationSaved.value = false
  try {
    const response = await articleApi.getArticle(normalizedId)
    if (sequence !== articleRequestSequence) return
    currentArticle.value = response.data
    beforeScore.value = scoreFromArticle(response.data)
  } catch (error) {
    if (sequence !== articleRequestSequence) return
    currentArticle.value = null
    console.error('加载文章详情失败:', error)
  } finally {
    if (sequence === articleRequestSequence) articleLoading.value = false
  }
}

const refreshAll = async () => {
  await fetchArticleList()
  if (selectedArticleId.value) await loadArticle(selectedArticleId.value)
}

const runOptimization = async () => {
  if (!currentArticle.value || optimizing.value) return
  optimizing.value = true
  optimizationError.value = null
  optimizationSaved.value = false
  try {
    const response = await articleApi.optimizeArticle(currentArticle.value.id)
    optimizedArticle.value = response.data.optimized
    beforeScore.value = response.data.before
    afterScore.value = response.data.after
  } catch (error) {
    optimizationError.value = formatOptimizationError(error)
  } finally {
    optimizing.value = false
  }
}

const saveOptimization = async () => {
  if (!optimizedArticle.value || saving.value || !isWritableStatus.value) return
  try {
    await ElMessageBox.confirm(
      '保存后，内容库中的当前正文会被优化稿覆盖。文章状态保持不变。',
      '确认保存优化稿',
      {
        confirmButtonText: '覆盖并保存',
        cancelButtonText: '继续比较',
        type: 'warning'
      }
    )
  } catch {
    return
  }

  saving.value = true
  try {
    const response = await articleApi.updateArticle(currentArticle.value.id, {
      title: optimizedArticle.value.title,
      summary: optimizedArticle.value.summary,
      content: optimizedArticle.value.content,
      tags: optimizedArticle.value.tags,
      status: currentArticle.value.status
    })
    const saved = response.data
    afterScore.value = scoreFromArticle(saved)
    optimizationSaved.value = true
    const listItem = articles.value.find(item => item.id === saved.id)
    if (listItem) {
      Object.assign(listItem, {
        title: saved.title,
        summary: saved.summary,
        tags: saved.tags,
        geo_score: saved.geo_score,
        updated_at: saved.updated_at
      })
    }
    ElMessage.success('优化稿已保存到内容库')
  } catch (error) {
    console.error('保存优化稿失败:', error)
  } finally {
    saving.value = false
  }
}

const openEditor = () => {
  router.push({ path: '/content-create', query: { articleId: currentArticle.value.id } })
}

const scoreFromArticle = article => ({
  score: article.geo_score || 0,
  dimensions: article.geo_analysis?.dimensions || {},
  suggestions: article.geo_analysis?.suggestions || []
})

const formatOptimizationError = (error) => {
  const status = error.response?.status
  const backendMessage = error.response?.data?.msg
  if (status === 503) {
    return {
      title: 'AI 服务尚未配置',
      message: '请在后端设置 AI_BASE_URL、AI_API_KEY 和 AI_MODEL 后重试。'
    }
  }
  if (status === 502) {
    return {
      title: '模型没有返回可用的优化稿',
      message: backendMessage || '检查模型服务后重试，原稿没有发生变化。'
    }
  }
  return {
    title: '优化请求失败',
    message: backendMessage || '检查网络和后端服务后重试，原稿没有发生变化。'
  }
}

const statusLabel = status => ({
  draft: '草稿',
  ready: '就绪',
  publishing: '发布中',
  published: '已发布'
}[status] || status)

const displayScore = score => Number.isFinite(Number(score)) ? Math.round(Number(score)) : '—'

onMounted(async () => {
  await fetchArticleList()
  if (listError.value || articles.value.length === 0) return
  const queryId = Number.parseInt(route.query.articleId, 10)
  const initialId = Number.isInteger(queryId) && queryId > 0 ? queryId : articles.value[0].id
  await loadArticle(initialId)
})
</script>

<style lang="scss" scoped>
.geo-optimization {
  --ink: #17222b;
  --teal: #0d5c63;
  --deep-teal: #102b31;
  --signal: #39b8b2;
  --mist: #edf3f3;
  --amber: #e8a33a;
  --paper: #fff;
  max-width: 1540px;
  margin: 0 auto;
  color: var(--ink);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", sans-serif;
}

.lab-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 30px;
  padding: 14px 0 22px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.14);
}

.eyebrow,
.document-heading span,
.bridge-heading span,
.suggestion-brief > span,
.revision-heading > div:first-child > span,
.waiting-copy > span,
.optimizing-state > span {
  color: var(--teal);
  font-family: "Cascadia Mono", "SFMono-Regular", monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.13em;
}

.lab-header h1 {
  margin: 8px 0 7px;
  color: var(--deep-teal);
  font-size: clamp(30px, 3vw, 48px);
  font-weight: 800;
  letter-spacing: -0.05em;
  line-height: 1;
}

.lab-header p {
  margin: 0;
  color: #66747a;
  font-size: 14px;
}

.article-selector {
  display: grid;
  grid-template-columns: 46px 170px minmax(260px, 600px) auto;
  gap: 16px;
  align-items: center;
  margin: 20px 0;
  padding: 15px 18px;
  border: 1px solid rgba(23, 34, 43, 0.12);
  background: var(--paper);
  box-shadow: 0 12px 32px rgba(16, 43, 49, 0.045);
}

.selector-index {
  color: var(--signal);
  font-family: "Cascadia Mono", monospace;
  font-size: 24px;
  font-weight: 700;
}

.selector-copy span,
.selector-copy small { display: block; }
.selector-copy span { font-size: 13px; font-weight: 700; }
.selector-copy small { margin-top: 3px; color: #899498; font-size: 10px; }
.article-option strong,
.article-option span { display: block; }
.article-option span { color: #7b888d; font-size: 11px; }

.revision-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  padding: 5px 2px 16px;
}

.revision-heading h2 {
  max-width: 800px;
  margin: 5px 0;
  overflow: hidden;
  color: var(--deep-teal);
  font-size: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.revision-heading p {
  margin: 0;
  color: #7b888d;
  font-size: 11px;
}

.revision-contract {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #647178;
  font-size: 10px;
}

.revision-contract strong { color: var(--teal); }
.revision-contract span { padding: 4px 7px; background: var(--mist); }

.revision-stage {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) 250px minmax(300px, 1fr);
  gap: 16px;
  align-items: stretch;
}

.document-panel,
.score-bridge {
  min-height: 650px;
  border: 1px solid rgba(23, 34, 43, 0.12);
  background: var(--paper);
  box-shadow: 0 16px 40px rgba(16, 43, 49, 0.05);
}

.document-panel {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 22px;
}

.original-document { border-top: 4px solid #97a7aa; }
.optimized-document { border-top: 4px solid var(--signal); }
.optimized-document.waiting { background: linear-gradient(145deg, #f7fafa 0%, #fff 68%); }

.document-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.1);
}

.document-heading strong {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}

.status-tag,
.saved-mark {
  padding: 4px 7px;
  border: 1px solid rgba(13, 92, 99, 0.22);
  color: var(--teal);
  font-size: 9px;
  font-weight: 700;
}

.status-tag.publishing { color: #9a5d09; border-color: rgba(232, 163, 58, 0.4); }
.status-tag.published { color: #287d53; border-color: rgba(40, 125, 83, 0.32); }
.saved-mark { background: rgba(57, 184, 178, 0.09); }

.document-scroll {
  flex: 1;
  max-height: 590px;
  padding: 20px 5px 20px 0;
  overflow: auto;
  scrollbar-color: #b9caca transparent;
  scrollbar-width: thin;
}

.document-scroll h3 {
  margin: 0 0 13px;
  color: var(--ink);
  font-size: 20px;
  line-height: 1.45;
}

.document-summary {
  margin: 0 0 22px;
  padding: 12px 14px;
  border-left: 3px solid var(--signal);
  background: var(--mist);
  color: #56656b;
  font-size: 12px;
  line-height: 1.7;
}

.document-scroll pre {
  margin: 0;
  color: #35434a;
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  font-size: 13px;
  line-height: 1.9;
  white-space: pre-wrap;
  word-break: break-word;
}

.document-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  padding-top: 14px;
  border-top: 1px solid rgba(23, 34, 43, 0.1);
}

.document-tags span { color: var(--teal); font-size: 10px; font-weight: 600; }

.score-bridge {
  position: relative;
  padding: 22px 18px;
  overflow: hidden;
  background: var(--deep-teal);
  color: #fff;
}

.score-bridge::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 1px;
  background: rgba(100, 214, 207, 0.12);
  content: "";
}

.bridge-heading,
.score-transition,
.dimension-compare,
.suggestion-brief { position: relative; z-index: 1; }
.bridge-heading strong { display: block; margin-top: 4px; font-size: 15px; }
.bridge-heading span { color: #64d6cf; }

.score-transition {
  display: grid;
  grid-template-columns: 70px 1fr 70px;
  align-items: center;
  margin: 25px 0;
}

.score-node {
  display: grid;
  place-items: center;
  height: 70px;
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.score-node.after { border-color: var(--signal); }
.score-node.pending { border-style: dashed; opacity: 0.65; }
.score-node span { color: #a9bdc0; font-size: 8px; font-weight: 700; }
.score-node strong { font-family: "Cascadia Mono", monospace; font-size: 25px; }

.transition-line {
  position: relative;
  height: 1px;
  background: rgba(255, 255, 255, 0.26);
}

.transition-line::after {
  position: absolute;
  top: -3px;
  right: -1px;
  border-width: 4px 0 4px 6px;
  border-style: solid;
  border-color: transparent transparent transparent rgba(255, 255, 255, 0.45);
  content: "";
}

.transition-line > span {
  position: absolute;
  top: -25px;
  left: 50%;
  padding: 3px 5px;
  transform: translateX(-50%);
  background: var(--deep-teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 11px;
  font-weight: 700;
}

.transition-line .positive { color: #64d6cf; }
.transition-line .negative { color: #ffc071; }
.transition-line .neutral { color: #c5d2d3; }

.dimension-compare { display: grid; gap: 15px; }
.dimension-item > div:first-child { display: flex; justify-content: space-between; gap: 8px; }
.dimension-item span { color: #b7c7c9; font-size: 10px; }
.dimension-item strong { font-family: "Cascadia Mono", monospace; font-size: 10px; }
.dimension-item i { margin: 0 4px; color: #668186; font-style: normal; }

.compare-track {
  position: relative;
  height: 6px;
  margin-top: 6px;
  background: rgba(255, 255, 255, 0.08);
}

.compare-track span { position: absolute; left: 0; height: 2px; }
.before-bar { top: 0; background: #8ba0a3; }
.after-bar { bottom: 0; background: var(--signal); }

.suggestion-brief {
  margin-top: 28px;
  padding-top: 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.14);
}

.suggestion-brief > span { color: #64d6cf; }
.suggestion-brief ul { display: grid; gap: 9px; margin: 12px 0 0; padding: 0; list-style: none; }
.suggestion-brief li,
.suggestion-brief p { color: #b7c7c9; font-size: 10px; line-height: 1.55; }
.suggestion-brief li::before { margin-right: 6px; color: var(--amber); content: "·"; }

.waiting-copy,
.optimizing-state {
  display: grid;
  place-items: center;
  min-height: 590px;
  text-align: center;
}

.waiting-mark {
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  border: 1px dashed rgba(13, 92, 99, 0.35);
  color: var(--teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 28px;
}

.waiting-copy h3,
.optimizing-state h3 { margin: 14px 0 4px; color: var(--deep-teal); font-size: 18px; }
.waiting-copy p,
.optimizing-state p { max-width: 350px; margin: 0; color: #748187; font-size: 12px; line-height: 1.7; }

.revision-pulse {
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: var(--teal);
  color: white;
  animation: revision-pulse 1.5s ease-out infinite;
}

.optimizing-state :deep(.el-skeleton) { width: 100%; margin-top: 28px; text-align: left; }

.optimization-error {
  margin-top: 14px;
  padding: 13px 16px;
  border: 1px solid rgba(232, 163, 58, 0.42);
  background: rgba(232, 163, 58, 0.08);
  color: #754710;
}

.optimization-error p { margin: 4px 0 0; font-size: 12px; }

.action-dock {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  margin-top: 16px;
  padding: 16px 18px;
  border: 1px solid rgba(23, 34, 43, 0.12);
  background: var(--paper);
  box-shadow: 0 12px 30px rgba(16, 43, 49, 0.045);
}

.action-dock span { color: var(--teal); font-size: 11px; font-weight: 700; }
.action-dock p { margin: 4px 0 0; color: #778489; font-size: 11px; }
.dock-buttons { display: flex; gap: 8px; }

.page-state,
.revision-loading {
  display: grid;
  place-items: center;
  min-height: 520px;
  padding: 40px;
  border: 1px solid rgba(23, 34, 43, 0.12);
  background: var(--paper);
  text-align: center;
}

.revision-loading { display: block; }
.page-state h2 { margin: 18px 0 6px; color: var(--deep-teal); }
.page-state p { margin: 0 0 18px; color: #778489; font-size: 13px; }
.state-symbol { color: #a45f15; font: 32px "Cascadia Mono", monospace; }

.empty-document {
  width: 90px;
  padding: 22px 15px;
  border: 1px solid rgba(13, 92, 99, 0.22);
  border-top: 5px solid var(--signal);
}

.empty-document span { display: block; height: 3px; margin: 8px 0; background: #d7e3e3; }
.empty-document span:nth-child(2) { width: 70%; }

:deep(.el-button--primary) {
  --el-button-bg-color: var(--teal);
  --el-button-border-color: var(--teal);
  --el-button-hover-bg-color: #14757a;
  --el-button-hover-border-color: #14757a;
}

@keyframes revision-pulse {
  70% { box-shadow: 0 0 0 12px rgba(57, 184, 178, 0); }
  100% { box-shadow: 0 0 0 0 rgba(57, 184, 178, 0); }
}

@media (max-width: 1180px) {
  .revision-stage { grid-template-columns: 1fr 210px 1fr; }
  .document-panel { padding: 18px; }
}

@media (max-width: 960px) {
  .article-selector { grid-template-columns: 38px 140px minmax(200px, 1fr) auto; }
  .revision-stage { grid-template-columns: 1fr; }
  .score-bridge { min-height: auto; }
  .score-bridge::before { display: none; }
  .document-panel { min-height: 560px; }
  .score-transition { max-width: 420px; }
  .dimension-compare { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .suggestion-brief ul { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 700px) {
  .lab-header,
  .revision-heading,
  .action-dock { align-items: flex-start; flex-direction: column; }
  .article-selector { grid-template-columns: 36px 1fr auto; }
  .article-selector :deep(.el-select) { grid-column: 1 / -1; }
  .revision-contract { flex-wrap: wrap; }
  .dock-buttons { flex-wrap: wrap; }
}

@media (prefers-reduced-motion: reduce) {
  .revision-pulse { animation: none; }
}
</style>
