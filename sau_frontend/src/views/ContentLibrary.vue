<template>
  <main class="content-library">
    <header class="library-header">
      <div class="header-copy">
        <p class="eyebrow">EDITORIAL ARCHIVE / 内容资产</p>
        <h1>内容库</h1>
        <p>按品牌与生产状态整理稿件，确认内容是否已经可以进入分发环节。</p>
      </div>

      <div class="header-actions">
        <div class="registry-stamp" aria-label="当前筛选稿件数">
          <span>当前筛选</span>
          <strong>{{ pagination.total }}</strong>
          <small>ARTICLES</small>
        </div>
        <el-button plain :icon="Upload" @click="importDialogVisible = true">Excel 导入</el-button>
        <el-button type="primary" :icon="DocumentAdd" @click="router.push('/content-create')">
          新建内容
        </el-button>
      </div>
    </header>

    <div class="library-shell">
      <aside class="index-panel">
        <div class="index-heading">
          <span>INDEX / 01</span>
          <h2>稿件索引</h2>
        </div>

        <label class="filter-label" for="project-filter">品牌项目</label>
        <el-select
          id="project-filter"
          v-model="filters.project_id"
          clearable
          filterable
          placeholder="全部品牌"
          :loading="projectsLoading"
          :disabled="projectsError"
          @change="applyFilters"
        >
          <el-option
            v-for="project in projects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
        <p v-if="projectsError" class="filter-error">项目筛选暂不可用，稿件仍可正常浏览。</p>

        <div class="status-index" aria-label="按稿件状态筛选">
          <button
            v-for="item in statusOptions"
            :key="item.value || 'all'"
            type="button"
            :class="{ active: filters.status === item.value }"
            @click="selectStatus(item.value)"
          >
            <span>{{ item.code }}</span>
            <div>
              <strong>{{ item.label }}</strong>
              <small>{{ item.description }}</small>
            </div>
          </button>
        </div>

        <button v-if="hasFilters" type="button" class="clear-filters" @click="clearFilters">
          清除全部筛选
        </button>

        <div class="state-legend">
          <span>WORKFLOW NOTE</span>
          <p><i class="dot draft" />草稿仍在编辑中；就绪稿件可以进入人工发布确认。</p>
          <p><i class="dot locked" />发布中与已发布由后续发布流程维护。</p>
        </div>
      </aside>

      <section class="archive-panel" aria-labelledby="archive-title">
        <div class="archive-toolbar">
          <div>
            <span class="archive-code">CATALOG / {{ currentStatus.code }}</span>
            <h2 id="archive-title">{{ archiveTitle }}</h2>
            <p>{{ rangeLabel }}</p>
          </div>
          <el-button :icon="Refresh" :loading="loading" @click="fetchArticles">刷新</el-button>
        </div>

        <div v-if="loading" class="archive-loading" aria-live="polite">
          <div v-for="index in 4" :key="index" class="skeleton-record">
            <el-skeleton :rows="3" animated />
          </div>
        </div>

        <div v-else-if="loadError" class="archive-state is-error" role="alert">
          <div class="state-mark">!</div>
          <h3>稿件目录没有载入</h3>
          <p>检查后端服务后重新获取，现有内容不会受到影响。</p>
          <el-button type="primary" @click="fetchArticles">重新加载</el-button>
        </div>

        <div v-else-if="articles.length === 0" class="archive-state">
          <div class="empty-sheet" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <h3>{{ hasFilters ? '这个索引下还没有稿件' : '内容库等待第一篇稿件' }}</h3>
          <p>{{ hasFilters ? '清除筛选查看其他内容，或新建一篇文章。' : '从品牌 Brief 生成内容并保存后，稿件会出现在这里。' }}</p>
          <div class="empty-actions">
            <el-button v-if="hasFilters" @click="clearFilters">清除筛选</el-button>
            <el-button type="primary" @click="router.push('/content-create')">开始创作</el-button>
          </div>
        </div>

        <div v-else class="record-list">
          <article v-for="article in articles" :key="article.id" class="article-record">
            <div class="record-sequence">
              <span>A-{{ String(article.id).padStart(4, '0') }}</span>
              <small>{{ article.project_name }}</small>
            </div>

            <div class="record-body">
              <div class="record-kicker">
                <span :class="['status-pill', article.status]">{{ statusLabel(article.status) }}</span>
                <time :datetime="article.updated_at">更新于 {{ formatDate(article.updated_at) }}</time>
              </div>
              <button type="button" class="record-title" @click="openEditor(article.id)">
                {{ article.title }}
              </button>
              <p>{{ article.summary || '这篇稿件尚未填写摘要。' }}</p>
              <div class="record-tags">
                <span v-for="tag in article.tags.slice(0, 3)" :key="tag"># {{ tag }}</span>
                <span v-if="article.tags.length > 3">+{{ article.tags.length - 3 }}</span>
              </div>
            </div>

            <div :class="['score-proof', scoreTone(article.geo_score)]">
              <strong>{{ displayScore(article.geo_score) }}</strong>
              <span>GEO SCORE</span>
            </div>

            <div class="record-actions">
              <el-button
                v-if="canToggleStatus(article.status)"
                text
                :loading="updatingArticleId === article.id"
                @click="toggleReady(article)"
              >
                {{ article.status === 'draft' ? '标记就绪' : '退回草稿' }}
              </el-button>
              <el-button :icon="Aim" @click="openOptimizer(article.id)">GEO 优化</el-button>
              <el-button
                v-if="article.status === 'ready'"
                type="primary"
                :icon="Promotion"
                @click="openPublisher(article.id)"
              >
                进入发布
              </el-button>
              <el-button type="primary" plain :icon="EditPen" @click="openEditor(article.id)">
                继续编辑
              </el-button>
            </div>
          </article>
        </div>

        <footer v-if="pagination.total_pages > 1" class="archive-pagination">
          <span>PAGE {{ String(pagination.page).padStart(2, '0') }} / {{ String(pagination.total_pages).padStart(2, '0') }}</span>
          <el-pagination
            v-model:current-page="pagination.page"
            background
            layout="prev, pager, next"
            :page-size="pagination.page_size"
            :total="pagination.total"
            @current-change="fetchArticles"
          />
        </footer>
      </section>
    </div>

    <el-dialog v-model="importDialogVisible" title="批量导入文章" width="560px">
      <div class="import-guide">
        <span>IMPORT / XLSX OR CSV</span>
        <h3>先选品牌，再导入稿件</h3>
        <p>必需列为“标题”和“正文”；可选“摘要、标签、状态”。一次最多 200 篇，整批校验通过后才会写入。</p>
      </div>
      <label class="import-label">归属品牌项目</label>
      <el-select v-model="importProjectId" filterable placeholder="选择品牌项目">
        <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
      </el-select>
      <button type="button" class="import-file" @click="importInput?.click()">
        <el-icon><Upload /></el-icon>
        <strong>{{ importFile?.name || '选择 Excel 或 CSV 文件' }}</strong>
        <span>{{ importFile ? formatFileSize(importFile.size) : '最大 2MB · 不会覆盖现有文章' }}</span>
      </button>
      <input ref="importInput" class="visually-hidden" type="file" accept=".xlsx,.csv" @change="handleImportFile" />
      <div class="import-template-link">
        <el-button text :icon="Download" @click="downloadImportTemplate">下载标准 Excel 模板</el-button>
      </div>
      <template #footer>
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" :disabled="!importProjectId || !importFile" @click="importArticles">
          导入到内容库
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Aim, DocumentAdd, Download, EditPen, Promotion, Refresh, Upload } from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { projectApi } from '@/api/project'

const router = useRouter()
const articles = ref([])
const projects = ref([])
const loading = ref(false)
const loadError = ref(false)
const projectsLoading = ref(false)
const projectsError = ref(false)
const updatingArticleId = ref(null)
const importDialogVisible = ref(false)
const importProjectId = ref(null)
const importFile = ref(null)
const importInput = ref(null)
const importing = ref(false)
let requestSequence = 0

const filters = reactive({
  project_id: null,
  status: ''
})

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0,
  total_pages: 0
})

const statusOptions = [
  { value: '', code: 'ALL', label: '全部稿件', description: '查看完整内容目录' },
  { value: 'draft', code: 'D01', label: '草稿', description: '仍在编辑与校对' },
  { value: 'ready', code: 'R02', label: '就绪', description: '可以进入发布确认' },
  { value: 'publishing', code: 'P03', label: '发布中', description: '发布流程正在处理' },
  { value: 'published', code: 'P04', label: '已发布', description: '已经完成分发' }
]

const currentStatus = computed(() => (
  statusOptions.find(item => item.value === filters.status) || statusOptions[0]
))

const selectedProjectName = computed(() => (
  projects.value.find(project => project.id === filters.project_id)?.name || ''
))

const archiveTitle = computed(() => {
  const parts = []
  if (selectedProjectName.value) parts.push(selectedProjectName.value)
  parts.push(currentStatus.value.label)
  return parts.join(' · ')
})

const hasFilters = computed(() => Boolean(filters.project_id || filters.status))

const rangeLabel = computed(() => {
  if (!pagination.total) return '没有符合条件的稿件'
  const start = (pagination.page - 1) * pagination.page_size + 1
  const end = Math.min(pagination.page * pagination.page_size, pagination.total)
  return `显示第 ${start}–${end} 篇，共 ${pagination.total} 篇`
})

const fetchProjects = async () => {
  projectsLoading.value = true
  projectsError.value = false
  try {
    const response = await projectApi.getProjects()
    projects.value = response.data || []
  } catch (error) {
    projectsError.value = true
    console.error('加载项目筛选失败:', error)
  } finally {
    projectsLoading.value = false
  }
}

const fetchArticles = async () => {
  const sequence = ++requestSequence
  loading.value = true
  loadError.value = false
  const params = {
    page: pagination.page,
    page_size: pagination.page_size
  }
  if (filters.project_id) params.project_id = filters.project_id
  if (filters.status) params.status = filters.status

  try {
    const response = await articleApi.getArticles(params)
    if (sequence !== requestSequence) return
    const result = response.data || {}
    const nextPagination = result.pagination || {}
    if (nextPagination.total_pages > 0 && pagination.page > nextPagination.total_pages) {
      pagination.page = nextPagination.total_pages
      await fetchArticles()
      return
    }
    articles.value = result.items || []
    Object.assign(pagination, nextPagination)
  } catch (error) {
    if (sequence !== requestSequence) return
    loadError.value = true
    console.error('加载内容库失败:', error)
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

const applyFilters = () => {
  pagination.page = 1
  fetchArticles()
}

const selectStatus = (status) => {
  if (filters.status === status) return
  filters.status = status
  applyFilters()
}

const clearFilters = () => {
  filters.project_id = null
  filters.status = ''
  applyFilters()
}

const handleImportFile = (event) => {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!/\.(xlsx|csv)$/i.test(file.name)) {
    ElMessage.error('仅支持 XLSX 或 CSV 文件')
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.error('导入文件不能超过 2MB')
    return
  }
  importFile.value = file
}

const importArticles = async () => {
  if (!importProjectId.value || !importFile.value || importing.value) return
  importing.value = true
  try {
    const formData = new FormData()
    formData.append('project_id', String(importProjectId.value))
    formData.append('file', importFile.value)
    const response = await articleApi.importArticles(formData)
    ElMessage.success(`已导入 ${response.data.created_count} 篇文章`)
    importDialogVisible.value = false
    importFile.value = null
    filters.project_id = importProjectId.value
    pagination.page = 1
    await fetchArticles()
  } catch (error) {
    console.error('批量导入文章失败:', error)
  } finally {
    importing.value = false
  }
}

const downloadImportTemplate = () => {
  window.open(articleApi.getImportTemplateUrl(), '_blank', 'noopener,noreferrer')
}

const formatFileSize = bytes => `${Math.max(0.1, bytes / 1024).toFixed(bytes > 1024 * 1024 ? 0 : 1)} KB`

const openEditor = (articleId) => {
  router.push({ path: '/content-create', query: { articleId } })
}

const openOptimizer = (articleId) => {
  router.push({ path: '/geo-optimize', query: { articleId } })
}

const openPublisher = (articleId) => {
  router.push({ path: '/publish-center', query: { article_id: articleId } })
}

const canToggleStatus = status => status === 'draft' || status === 'ready'

const toggleReady = async (article) => {
  if (updatingArticleId.value || !canToggleStatus(article.status)) return
  const nextStatus = article.status === 'draft' ? 'ready' : 'draft'
  updatingArticleId.value = article.id
  try {
    await articleApi.updateArticle(article.id, { status: nextStatus })
    ElMessage.success(nextStatus === 'ready' ? '稿件已标记为就绪' : '稿件已退回草稿')
    await fetchArticles()
  } catch (error) {
    console.error('更新稿件状态失败:', error)
  } finally {
    updatingArticleId.value = null
  }
}

const statusLabel = status => ({
  draft: '草稿',
  ready: '就绪',
  publishing: '发布中',
  published: '已发布'
}[status] || status)

const displayScore = (score) => Number.isFinite(Number(score)) ? Math.round(Number(score)) : '—'

const scoreTone = (score) => {
  const value = Number(score)
  if (!Number.isFinite(value)) return 'is-empty'
  if (value >= 85) return 'is-strong'
  if (value >= 70) return 'is-good'
  return 'is-review'
}

const formatDate = (value) => {
  if (!value) return '时间未知'
  const normalized = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date)
}

onMounted(() => {
  fetchProjects()
  fetchArticles()
})
</script>

<style lang="scss" scoped>
.content-library {
  --ink: #17222b;
  --teal: #0d5c63;
  --deep-teal: #102b31;
  --signal: #39b8b2;
  --mist: #edf3f3;
  --amber: #e8a33a;
  --paper: #ffffff;
  max-width: 1480px;
  margin: 0 auto;
  color: var(--ink);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", sans-serif;
}

.library-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  margin-bottom: 24px;
  padding: 14px 0 24px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.14);
}

.eyebrow,
.archive-code,
.index-heading > span,
.filter-label,
.state-legend > span {
  color: var(--teal);
  font-family: "Cascadia Mono", "SFMono-Regular", monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.13em;
}

.header-copy h1 {
  margin: 8px 0 7px;
  color: var(--deep-teal);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  font-size: clamp(30px, 3vw, 48px);
  font-weight: 800;
  letter-spacing: -0.05em;
  line-height: 1;
}

.header-copy > p:last-child {
  max-width: 620px;
  margin: 0;
  color: #647178;
  font-size: 14px;
  line-height: 1.7;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 18px;
}

.registry-stamp {
  display: grid;
  grid-template-columns: auto auto;
  align-items: end;
  min-width: 126px;
  padding: 7px 13px;
  border-left: 3px solid var(--signal);
  background: var(--mist);
}

.registry-stamp span,
.registry-stamp small {
  color: #6c7b80;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.09em;
}

.registry-stamp strong {
  grid-row: span 2;
  margin-left: auto;
  color: var(--deep-teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 30px;
  line-height: 1;
}

.library-shell {
  display: grid;
  grid-template-columns: minmax(210px, 250px) minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}

.index-panel,
.archive-panel {
  border: 1px solid rgba(23, 34, 43, 0.12);
  background: var(--paper);
  box-shadow: 0 16px 40px rgba(16, 43, 49, 0.055);
}

.index-panel {
  position: sticky;
  top: 14px;
  padding: 22px;
}

.index-heading {
  margin-bottom: 25px;
}

.index-heading h2,
.archive-toolbar h2 {
  margin: 5px 0 0;
  color: var(--deep-teal);
  font-size: 20px;
}

.filter-label {
  display: block;
  margin-bottom: 8px;
}

.filter-error {
  margin: 8px 0 0;
  color: #a45f15;
  font-size: 11px;
  line-height: 1.5;
}

.status-index {
  display: grid;
  gap: 3px;
  margin-top: 22px;
}

.status-index button {
  display: grid;
  grid-template-columns: 37px 1fr;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 11px 9px;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: #536169;
  text-align: left;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.status-index button:hover,
.status-index button:focus-visible {
  background: #f3f7f7;
  outline: none;
}

.status-index button.active {
  border-left-color: var(--signal);
  background: var(--mist);
  color: var(--deep-teal);
}

.status-index button > span {
  color: var(--teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 10px;
  font-weight: 700;
}

.status-index strong,
.status-index small {
  display: block;
}

.status-index strong {
  font-size: 13px;
}

.status-index small {
  margin-top: 2px;
  color: #849095;
  font-size: 10px;
}

.clear-filters {
  width: 100%;
  margin-top: 14px;
  padding: 9px;
  border: 1px dashed rgba(13, 92, 99, 0.25);
  background: transparent;
  color: var(--teal);
  font-size: 12px;
  cursor: pointer;
}

.state-legend {
  margin-top: 28px;
  padding-top: 19px;
  border-top: 1px solid rgba(23, 34, 43, 0.1);
}

.state-legend p {
  position: relative;
  margin: 10px 0 0;
  padding-left: 16px;
  color: #738086;
  font-size: 11px;
  line-height: 1.6;
}

.dot {
  position: absolute;
  top: 6px;
  left: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--signal);
}

.dot.locked { background: #99a4a8; }

.archive-panel {
  min-height: 580px;
  padding: 24px 28px;
}

.archive-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 19px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.12);
}

.archive-toolbar p {
  margin: 5px 0 0;
  color: #7a878c;
  font-size: 12px;
}

.archive-loading {
  display: grid;
  gap: 0;
}

.skeleton-record {
  padding: 25px 6px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.09);
}

.record-list {
  display: grid;
}

.article-record {
  display: grid;
  grid-template-columns: 112px minmax(260px, 1fr) 92px 150px;
  gap: 24px;
  align-items: center;
  padding: 24px 6px;
  border-bottom: 1px solid rgba(23, 34, 43, 0.1);
}

.article-record:last-child { border-bottom: 0; }

.record-sequence {
  align-self: stretch;
  padding-right: 17px;
  border-right: 1px solid rgba(13, 92, 99, 0.17);
}

.record-sequence span,
.record-sequence small {
  display: block;
}

.record-sequence span {
  color: var(--teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
}

.record-sequence small {
  margin-top: 9px;
  color: #778489;
  font-size: 11px;
  line-height: 1.45;
}

.record-kicker {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-bottom: 8px;
}

.record-kicker time {
  color: #8a9599;
  font-size: 10px;
}

.status-pill {
  padding: 3px 7px;
  border: 1px solid rgba(23, 34, 43, 0.15);
  color: #56636a;
  font-size: 10px;
  font-weight: 700;
}

.status-pill.ready {
  border-color: rgba(13, 92, 99, 0.28);
  background: rgba(57, 184, 178, 0.09);
  color: var(--teal);
}

.status-pill.publishing {
  border-color: rgba(232, 163, 58, 0.35);
  background: rgba(232, 163, 58, 0.1);
  color: #9a5d09;
}

.status-pill.published {
  border-color: rgba(40, 125, 83, 0.28);
  background: rgba(40, 125, 83, 0.08);
  color: #287d53;
}

.record-title {
  display: block;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: 17px;
  font-weight: 750;
  line-height: 1.45;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.record-title:hover,
.record-title:focus-visible {
  color: var(--teal);
  outline: none;
  text-decoration: underline;
  text-underline-offset: 4px;
}

.record-body > p {
  display: -webkit-box;
  margin: 6px 0 10px;
  overflow: hidden;
  color: #6b787e;
  font-size: 12px;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.record-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

.record-tags span {
  color: var(--teal);
  font-size: 10px;
  font-weight: 600;
}

.score-proof {
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  border: 1px solid rgba(23, 34, 43, 0.14);
  background: #f8faf9;
  text-align: center;
}

.score-proof strong,
.score-proof span { display: block; }

.score-proof strong {
  color: var(--ink);
  font-family: "Cascadia Mono", monospace;
  font-size: 27px;
  line-height: 1;
}

.score-proof span {
  margin-top: 4px;
  color: #849095;
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.score-proof.is-strong {
  border-color: var(--teal);
  box-shadow: inset 0 -4px 0 var(--signal);
}

.score-proof.is-good { box-shadow: inset 0 -4px 0 #8cc9b0; }
.score-proof.is-review { box-shadow: inset 0 -4px 0 var(--amber); }

.record-actions {
  display: grid;
  justify-items: stretch;
  gap: 6px;
}

.archive-state {
  display: grid;
  place-items: center;
  min-height: 440px;
  padding: 40px;
  text-align: center;
}

.archive-state h3 {
  margin: 18px 0 6px;
  color: var(--deep-teal);
  font-size: 20px;
}

.archive-state p {
  max-width: 430px;
  margin: 0 0 20px;
  color: #738086;
  font-size: 13px;
  line-height: 1.7;
}

.empty-sheet {
  width: 92px;
  padding: 24px 16px;
  border: 1px solid rgba(13, 92, 99, 0.22);
  border-top: 5px solid var(--signal);
  background: #f7fafa;
}

.empty-sheet span {
  display: block;
  height: 3px;
  margin: 8px 0;
  background: #d7e3e3;
}

.empty-sheet span:nth-child(2) { width: 74%; }

.empty-actions {
  display: flex;
  gap: 10px;
}

.state-mark {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  border: 1px solid rgba(232, 163, 58, 0.5);
  color: #a45f15;
  font-family: "Cascadia Mono", monospace;
  font-size: 28px;
}

.archive-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 5px 2px;
  border-top: 1px solid rgba(23, 34, 43, 0.1);
}

.archive-pagination > span {
  color: var(--teal);
  font-family: "Cascadia Mono", monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

:deep(.el-button--primary) {
  --el-button-bg-color: var(--teal);
  --el-button-border-color: var(--teal);
  --el-button-hover-bg-color: #14757a;
  --el-button-hover-border-color: #14757a;
}

:deep(.el-select) { width: 100%; }

.import-guide {
  margin-bottom: 20px;
  padding: 16px 18px;
  border-left: 3px solid var(--signal);
  background: var(--mist);

  span { color: var(--teal); font: 700 10px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h3 { margin: 8px 0 5px; color: var(--deep-teal); font-size: 17px; }
  p { margin: 0; color: #68787d; font-size: 12px; line-height: 1.65; }
}

.import-label {
  display: block;
  margin: 0 0 8px;
  color: #44585e;
  font-size: 12px;
  font-weight: 700;
}

.import-file {
  display: grid;
  width: 100%;
  margin-top: 14px;
  padding: 24px;
  place-items: center;
  border: 1px dashed rgba(13, 92, 99, 0.38);
  background: #f8fbfa;
  color: var(--teal);
  cursor: pointer;

  .el-icon { margin-bottom: 8px; font-size: 24px; }
  strong { color: var(--deep-teal); font-size: 13px; }
  span { margin-top: 5px; color: #7b898d; font-size: 10px; }
}

.import-template-link {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

@media (max-width: 1100px) {
  .article-record {
    grid-template-columns: 88px minmax(220px, 1fr) 78px;
  }

  .record-actions {
    grid-column: 2 / -1;
    display: flex;
    justify-content: flex-end;
  }
}

@media (max-width: 820px) {
  .library-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .library-shell { grid-template-columns: 1fr; }

  .index-panel {
    position: static;
  }

  .status-index {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .state-legend { display: none; }
}

@media (max-width: 620px) {
  .header-actions {
    align-items: stretch;
    flex-direction: column;
    width: 100%;
  }

  .archive-panel { padding: 18px; }

  .article-record {
    grid-template-columns: 1fr 68px;
    gap: 14px;
  }

  .record-sequence {
    grid-column: 1 / -1;
    display: flex;
    justify-content: space-between;
    padding: 0 0 8px;
    border-right: 0;
    border-bottom: 1px solid rgba(13, 92, 99, 0.15);
  }

  .record-sequence small { margin: 0; }
  .score-proof { width: 64px; height: 64px; }
  .record-actions { grid-column: 1 / -1; }
  .status-index { grid-template-columns: 1fr; }

  .archive-pagination {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-index button { transition: none; }
}
</style>
