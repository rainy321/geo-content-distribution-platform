<template>
  <main class="project-management">
    <header class="page-intro">
      <div class="intro-copy">
        <p class="eyebrow">BRAND KNOWLEDGE BASE</p>
        <h1>品牌项目</h1>
        <p class="intro-text">
          把品牌事实、产品定位和核心关键词整理成一份可靠底稿，供后续生成、评分与分发共同使用。
        </p>
      </div>

      <div class="intro-actions">
        <div class="ledger" aria-label="项目概览">
          <div class="ledger-item">
            <span>PROJECTS</span>
            <strong>{{ projects.length }}</strong>
          </div>
          <div class="ledger-item">
            <span>ARTICLES</span>
            <strong>{{ totalArticles }}</strong>
          </div>
          <div class="ledger-item">
            <span>SIGNALS</span>
            <strong>{{ totalKeywords }}</strong>
          </div>
        </div>
        <el-button type="primary" size="large" :icon="Plus" @click="openCreateDialog">
          新建品牌项目
        </el-button>
      </div>
    </header>

    <section class="project-toolbar" aria-label="项目筛选">
      <div class="toolbar-heading">
        <span class="section-index">PROJECT FILES</span>
        <p>{{ filteredProjects.length }} 份品牌档案</p>
      </div>
      <div class="toolbar-actions">
        <el-input
          v-model="searchText"
          :prefix-icon="Search"
          clearable
          placeholder="搜索品牌、产品或关键词"
          aria-label="搜索品牌项目"
        />
        <el-button :icon="Refresh" circle :loading="loading" aria-label="刷新项目" @click="fetchProjects" />
      </div>
    </section>

    <section v-if="loadError" class="state-panel error-panel">
      <el-icon><WarningFilled /></el-icon>
      <div>
        <h2>项目列表没有加载成功</h2>
        <p>检查后端服务是否运行，然后重新加载。</p>
      </div>
      <el-button @click="fetchProjects">重新加载</el-button>
    </section>

    <section v-else v-loading="loading" class="project-stage">
      <div v-if="filteredProjects.length" class="project-grid">
        <article
          v-for="project in filteredProjects"
          :key="project.id"
          class="project-card"
        >
          <div class="signal-band" aria-hidden="true">
            <span
              v-for="(keyword, index) in signalKeywords(project)"
              :key="`${project.id}-${keyword}-${index}`"
            />
          </div>

          <div class="card-heading">
            <div class="brand-monogram">{{ brandMonogram(project.name) }}</div>
            <div class="brand-title">
              <div class="brand-meta">
                <span>{{ project.industry || '行业待补充' }}</span>
                <span class="record-id">ID {{ String(project.id).padStart(3, '0') }}</span>
              </div>
              <h2>{{ project.name }}</h2>
            </div>
          </div>

          <div class="product-line">
            <span>PRODUCT</span>
            <strong>{{ project.product || '尚未填写产品名称' }}</strong>
          </div>

          <p class="project-description">
            {{ project.description || '补充品牌介绍后，AI 才能稳定引用准确的品牌和产品事实。' }}
          </p>

          <a
            v-if="safeWebsite(project.website)"
            class="website-link"
            :href="safeWebsite(project.website)"
            target="_blank"
            rel="noopener noreferrer"
          >
            <el-icon><Link /></el-icon>
            {{ websiteHost(project.website) }}
            <el-icon><TopRight /></el-icon>
          </a>
          <div v-else class="website-link is-empty">
            <el-icon><Link /></el-icon>
            官网待补充
          </div>

          <div class="keyword-section">
            <span class="field-label">CORE SIGNALS</span>
            <div v-if="project.keywords.length" class="keyword-list">
              <span v-for="keyword in project.keywords" :key="keyword" class="keyword-chip">
                {{ keyword }}
              </span>
            </div>
            <p v-else class="field-empty">还没有核心关键词</p>
          </div>

          <div class="card-footer">
            <div class="article-count">
              <el-icon><DocumentCopy /></el-icon>
              <span><strong>{{ project.article_count }}</strong> 篇文章</span>
            </div>
            <time :datetime="project.updated_at">更新于 {{ formatDate(project.updated_at) }}</time>
          </div>

          <div class="card-actions">
            <el-button :icon="EditPen" @click="openEditDialog(project)">编辑底稿</el-button>
            <el-tooltip
              :disabled="project.article_count === 0"
              content="项目下已有文章，不能删除"
              placement="top"
            >
              <span>
                <el-button
                  type="danger"
                  plain
                  :icon="Delete"
                  :disabled="project.article_count > 0"
                  @click="confirmDelete(project)"
                >
                  删除
                </el-button>
              </span>
            </el-tooltip>
          </div>
        </article>
      </div>

      <div v-else-if="projects.length" class="state-panel empty-panel">
        <el-icon><Search /></el-icon>
        <div>
          <h2>没有匹配的品牌项目</h2>
          <p>尝试搜索品牌名称、产品、行业或核心关键词。</p>
        </div>
        <el-button @click="searchText = ''">清除搜索</el-button>
      </div>

      <div v-else class="state-panel empty-panel first-project">
        <div class="empty-mark">G</div>
        <div>
          <h2>建立第一份品牌底稿</h2>
          <p>先录入品牌事实和关键词，下一步即可生成第一篇 GEO 内容。</p>
        </div>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建品牌项目</el-button>
      </div>
    </section>

    <el-dialog
      v-model="dialogVisible"
      class="project-dialog"
      width="700px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <template #header>
        <div class="dialog-heading">
          <span>{{ dialogMode === 'create' ? 'NEW PROJECT' : 'EDIT PROJECT' }}</span>
          <h2>{{ dialogMode === 'create' ? '建立品牌底稿' : `编辑 ${projectForm.name}` }}</h2>
          <p>只填写能够确认的事实；这些信息会直接进入内容生成 Prompt。</p>
        </div>
      </template>

      <el-form
        ref="projectFormRef"
        :model="projectForm"
        :rules="formRules"
        label-position="top"
        @submit.prevent="submitProject"
      >
        <div class="form-grid">
          <el-form-item label="品牌名称" prop="name">
            <el-input v-model="projectForm.name" maxlength="100" show-word-limit placeholder="例如：XX科技" />
          </el-form-item>
          <el-form-item label="所属行业" prop="industry">
            <el-input v-model="projectForm.industry" placeholder="例如：人工智能" />
          </el-form-item>
          <el-form-item label="产品名称" prop="product">
            <el-input v-model="projectForm.product" placeholder="例如：企业 AI Agent" />
          </el-form-item>
          <el-form-item label="官方网站" prop="website">
            <el-input v-model="projectForm.website" placeholder="https://example.com" />
          </el-form-item>
        </div>

        <el-form-item label="品牌介绍" prop="description">
          <el-input
            v-model="projectForm.description"
            type="textarea"
            :rows="4"
            resize="none"
            placeholder="说明品牌服务对象、核心能力和已确认的产品事实"
          />
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="核心关键词" prop="keywords">
            <el-select
              v-model="projectForm.keywords"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入后按回车添加"
            >
              <el-option
                v-for="keyword in projectForm.keywords"
                :key="keyword"
                :label="keyword"
                :value="keyword"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="竞争品牌" prop="competitors">
            <el-select
              v-model="projectForm.competitors"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入后按回车添加"
            >
              <el-option
                v-for="competitor in projectForm.competitors"
                :key="competitor"
                :label="competitor"
                :value="competitor"
              />
            </el-select>
          </el-form-item>
        </div>
      </el-form>

      <template #footer>
        <div class="dialog-footer">
          <span>保存后可随时继续补充，不会自动发布内容。</span>
          <div>
            <el-button @click="dialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="saving" @click="submitProject">
              {{ dialogMode === 'create' ? '创建项目' : '保存修改' }}
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  DocumentCopy,
  EditPen,
  Link,
  Plus,
  Refresh,
  Search,
  TopRight,
  WarningFilled
} from '@element-plus/icons-vue'
import { projectApi } from '@/api/project'

const projects = ref([])
const loading = ref(false)
const loadError = ref(false)
const searchText = ref('')
const dialogVisible = ref(false)
const dialogMode = ref('create')
const saving = ref(false)
const projectFormRef = ref(null)

const emptyProject = () => ({
  id: null,
  name: '',
  website: '',
  product: '',
  industry: '',
  description: '',
  keywords: [],
  competitors: []
})

const projectForm = reactive(emptyProject())

const validateWebsite = (rule, value, callback) => {
  if (!value) {
    callback()
    return
  }
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('unsupported protocol')
    callback()
  } catch {
    callback(new Error('请输入以 http:// 或 https:// 开头的有效网址'))
  }
}

const formRules = {
  name: [
    { required: true, message: '请输入品牌名称', trigger: 'blur' },
    { max: 100, message: '品牌名称不能超过 100 个字符', trigger: 'blur' }
  ],
  website: [{ validator: validateWebsite, trigger: 'blur' }]
}

const totalArticles = computed(() => (
  projects.value.reduce((sum, project) => sum + Number(project.article_count || 0), 0)
))

const totalKeywords = computed(() => (
  new Set(projects.value.flatMap(project => project.keywords || [])).size
))

const filteredProjects = computed(() => {
  const query = searchText.value.trim().toLocaleLowerCase()
  if (!query) return projects.value
  return projects.value.filter((project) => {
    const searchable = [
      project.name,
      project.product,
      project.industry,
      ...(project.keywords || [])
    ].join(' ').toLocaleLowerCase()
    return searchable.includes(query)
  })
})

const fetchProjects = async () => {
  loading.value = true
  loadError.value = false
  try {
    const response = await projectApi.getProjects()
    projects.value = response.data || []
  } catch (error) {
    loadError.value = true
    console.error('加载品牌项目失败:', error)
  } finally {
    loading.value = false
  }
}

const resetForm = async (value = emptyProject()) => {
  Object.assign(projectForm, value)
  await nextTick()
  projectFormRef.value?.clearValidate()
}

const openCreateDialog = async () => {
  dialogMode.value = 'create'
  dialogVisible.value = true
  await resetForm()
}

const openEditDialog = async (project) => {
  dialogMode.value = 'edit'
  dialogVisible.value = true
  await resetForm({
    id: project.id,
    name: project.name,
    website: project.website,
    product: project.product,
    industry: project.industry,
    description: project.description,
    keywords: [...(project.keywords || [])],
    competitors: [...(project.competitors || [])]
  })
}

const submitProject = async () => {
  const valid = await projectFormRef.value?.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  const payload = {
    name: projectForm.name,
    website: projectForm.website,
    product: projectForm.product,
    industry: projectForm.industry,
    description: projectForm.description,
    keywords: projectForm.keywords,
    competitors: projectForm.competitors
  }

  try {
    if (dialogMode.value === 'create') {
      await projectApi.createProject(payload)
      ElMessage.success('项目已创建')
    } else {
      await projectApi.updateProject(projectForm.id, payload)
      ElMessage.success('项目已更新')
    }
    dialogVisible.value = false
    await fetchProjects()
  } catch (error) {
    console.error('保存品牌项目失败:', error)
  } finally {
    saving.value = false
  }
}

const confirmDelete = async (project) => {
  if (project.article_count > 0) {
    ElMessage.warning('项目下已有文章，不能删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除“${project.name}”后无法恢复，确定继续吗？`,
      '删除品牌项目',
      {
        confirmButtonText: '删除项目',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    await projectApi.deleteProject(project.id)
    ElMessage.success('项目已删除')
    await fetchProjects()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('删除品牌项目失败:', error)
    }
  }
}

const safeWebsite = (website) => {
  if (!website) return ''
  try {
    const url = new URL(website)
    return ['http:', 'https:'].includes(url.protocol) ? url.toString() : ''
  } catch {
    return ''
  }
}

const websiteHost = (website) => {
  try {
    return new URL(website).hostname.replace(/^www\./, '')
  } catch {
    return website
  }
}

const brandMonogram = (name) => name.trim().slice(0, 2).toUpperCase()

const signalKeywords = (project) => {
  const keywords = (project.keywords || []).slice(0, 6)
  return keywords.length ? keywords : ['pending']
}

const formatDate = (value) => {
  if (!value) return '刚刚'
  const date = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit'
  }).format(date)
}

onMounted(fetchProjects)
</script>

<style lang="scss" scoped>
.project-management {
  --ink: #17222b;
  --teal: #0d5c63;
  --signal: #39b8b2;
  --mist: #edf3f3;
  --amber: #e8a33a;
  --paper: #ffffff;
  max-width: 1480px;
  margin: 0 auto;
  color: var(--ink);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", sans-serif;
}

.page-intro {
  position: relative;
  display: flex;
  justify-content: space-between;
  gap: 48px;
  padding: 34px 38px 32px;
  overflow: hidden;
  border: 1px solid #d5e1e1;
  border-radius: 18px 5px 18px 5px;
  background:
    linear-gradient(108deg, rgba(57, 184, 178, 0.09), transparent 40%),
    var(--paper);

  &::after {
    position: absolute;
    right: -38px;
    bottom: -66px;
    width: 210px;
    height: 150px;
    border: 1px solid rgba(13, 92, 99, 0.14);
    border-radius: 50%;
    content: "";
  }
}

.intro-copy {
  position: relative;
  z-index: 1;
  max-width: 680px;

  h1 {
    margin: 5px 0 12px;
    color: var(--ink);
    font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
    font-size: clamp(34px, 4vw, 54px);
    font-weight: 750;
    letter-spacing: -0.045em;
    line-height: 1.05;
  }
}

.eyebrow,
.section-index,
.field-label,
.product-line > span,
.record-id {
  color: var(--teal);
  font-family: "Cascadia Mono", "SFMono-Regular", monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.13em;
}

.intro-text {
  max-width: 630px;
  color: #607078;
  font-size: 15px;
  line-height: 1.8;
}

.intro-actions {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: space-between;
  min-width: 360px;

  :deep(.el-button--primary) {
    --el-button-bg-color: var(--teal);
    --el-button-border-color: var(--teal);
    --el-button-hover-bg-color: #14747b;
    --el-button-hover-border-color: #14747b;
    min-width: 164px;
    border-radius: 10px 3px 10px 3px;
  }
}

.ledger {
  display: flex;
  overflow: hidden;
  border: 1px solid #d7e2e2;
  border-radius: 10px 3px 10px 3px;
  background: rgba(255, 255, 255, 0.82);
}

.ledger-item {
  min-width: 94px;
  padding: 11px 14px;
  border-right: 1px solid #e0e9e9;

  &:last-child {
    border-right: 0;
  }

  span {
    display: block;
    color: #7d8c92;
    font: 650 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.1em;
  }

  strong {
    display: block;
    margin-top: 5px;
    color: var(--ink);
    font: 700 22px/1 "Arial Narrow", sans-serif;
  }
}

.project-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 4px 16px;
  border-bottom: 1px solid #d4dfdf;
}

.toolbar-heading p {
  margin-top: 4px;
  color: #718087;
  font-size: 13px;
}

.toolbar-actions {
  display: flex;
  gap: 10px;

  :deep(.el-input) {
    width: min(340px, 48vw);
  }

  :deep(.el-input__wrapper) {
    border-radius: 9px 3px 9px 3px;
  }
}

.project-stage {
  min-height: 320px;
  padding-top: 22px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 18px;
}

.project-card {
  position: relative;
  display: flex;
  min-height: 440px;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #d7e2e2;
  border-radius: 14px 4px 14px 4px;
  background: var(--paper);
  box-shadow: 0 10px 28px rgba(23, 34, 43, 0.045);
  transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;

  &:hover {
    transform: translateY(-3px);
    border-color: rgba(13, 92, 99, 0.42);
    box-shadow: 0 18px 38px rgba(23, 34, 43, 0.09);
  }
}

.signal-band {
  display: flex;
  height: 7px;
  gap: 2px;
  background: #dfe9e9;

  span {
    flex: 1;
    background: #39b8b2;

    &:nth-child(2) { background: #2b9baa; }
    &:nth-child(3) { background: #397fa1; }
    &:nth-child(4) { background: #536d91; }
    &:nth-child(5) { background: #8d7282; }
    &:nth-child(6) { background: #d09649; }
  }
}

.card-heading {
  display: flex;
  gap: 14px;
  padding: 24px 24px 18px;
}

.brand-monogram {
  display: grid;
  width: 50px;
  height: 50px;
  flex: 0 0 50px;
  place-items: center;
  border: 1px solid rgba(13, 92, 99, 0.25);
  border-radius: 14px 4px 14px 4px;
  background: var(--mist);
  color: var(--teal);
  font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
  font-size: 17px;
  font-weight: 750;
}

.brand-title {
  min-width: 0;
  flex: 1;

  h2 {
    margin-top: 5px;
    overflow: hidden;
    color: var(--ink);
    font-size: 21px;
    font-weight: 720;
    letter-spacing: -0.025em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.brand-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #708087;
  font-size: 12px;
}

.record-id {
  color: #99a5aa;
  font-size: 9px;
}

.product-line,
.project-description,
.website-link,
.keyword-section,
.card-footer,
.card-actions {
  margin-right: 24px;
  margin-left: 24px;
}

.product-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 11px 0;
  border-top: 1px solid #e4ebeb;
  border-bottom: 1px solid #e4ebeb;

  > span {
    color: #809096;
    font-size: 9px;
  }

  strong {
    overflow: hidden;
    color: #33464e;
    font-size: 14px;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.project-description {
  display: -webkit-box;
  min-height: 66px;
  margin-top: 17px;
  overflow: hidden;
  color: #68777e;
  font-size: 13px;
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.website-link {
  display: inline-flex;
  width: fit-content;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  color: var(--teal);
  font-size: 12px;

  &:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  &.is-empty {
    color: #9ba7ab;
  }
}

.keyword-section {
  margin-top: 20px;
}

.field-label {
  display: block;
  margin-bottom: 8px;
  color: #809096;
  font-size: 9px;
}

.keyword-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.keyword-chip {
  padding: 5px 9px;
  border: 1px solid #cce0df;
  border-radius: 999px;
  background: #f2f8f7;
  color: #245b5f;
  font-size: 11px;
}

.field-empty {
  color: #a0aaae;
  font-size: 12px;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding: 20px 0 14px;
  color: #829096;
  font-size: 11px;

  time {
    font-family: "Cascadia Mono", monospace;
    font-size: 10px;
  }
}

.article-count {
  display: inline-flex;
  align-items: center;
  gap: 6px;

  strong {
    color: var(--ink);
    font-size: 13px;
  }
}

.card-actions {
  display: flex;
  justify-content: space-between;
  padding: 14px 0 20px;
  border-top: 1px solid #e4ebeb;
}

.state-panel {
  display: flex;
  min-height: 220px;
  align-items: center;
  justify-content: center;
  gap: 22px;
  padding: 36px;
  border: 1px dashed #bdcdcd;
  border-radius: 16px 4px 16px 4px;
  background: rgba(255, 255, 255, 0.65);

  > .el-icon {
    color: var(--teal);
    font-size: 30px;
  }

  h2 {
    margin-bottom: 5px;
    font-size: 18px;
  }

  p {
    color: #718087;
    font-size: 13px;
  }
}

.error-panel {
  margin-top: 22px;

  > .el-icon {
    color: var(--amber);
  }
}

.empty-mark {
  display: grid;
  width: 62px;
  height: 62px;
  place-items: center;
  border: 1px solid rgba(13, 92, 99, 0.3);
  border-radius: 18px 5px 18px 5px;
  color: var(--teal);
  font: 750 25px/1 "Arial Narrow", sans-serif;
}

.dialog-heading {
  span {
    color: #0d5c63;
    font: 700 10px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.14em;
  }

  h2 {
    margin: 6px 0;
    color: #17222b;
    font-size: 24px;
  }

  p {
    color: #748188;
    font-size: 13px;
  }
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}

:deep(.el-select) {
  width: 100%;
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;

  > span {
    color: #8a969b;
    font-size: 11px;
  }
}

:global(.project-dialog) {
  max-width: calc(100vw - 28px);
  border-radius: 16px 5px 16px 5px;
}

@media (max-width: 900px) {
  .page-intro {
    flex-direction: column;
    gap: 24px;
    padding: 28px;
  }

  .intro-actions {
    min-width: 0;
    flex-direction: row;
    align-items: center;
  }
}

@media (max-width: 640px) {
  .page-intro {
    padding: 24px 20px;
  }

  .intro-actions,
  .project-toolbar,
  .dialog-footer {
    align-items: stretch;
    flex-direction: column;
  }

  .ledger {
    width: 100%;
  }

  .ledger-item {
    min-width: 0;
    flex: 1;
  }

  .toolbar-actions :deep(.el-input) {
    width: 100%;
  }

  .project-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }

  .state-panel {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (prefers-reduced-motion: reduce) {
  .project-card {
    transition: none;
  }

  .project-card:hover {
    transform: none;
  }
}
</style>
