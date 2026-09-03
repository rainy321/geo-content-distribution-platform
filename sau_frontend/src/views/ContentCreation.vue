<template>
  <main class="content-creation">
    <header class="workbench-header">
      <div>
        <p class="eyebrow">GEO WRITING DESK</p>
        <h1>AI 内容创作</h1>
        <p>从品牌事实出发生成内容，在同一张写作台上完成编辑、规则评分与保存。</p>
        <el-button class="batch-entry" plain :icon="Files" @click="batchDialogVisible = true">
          批量生成草稿
        </el-button>
      </div>

      <ol class="flow-track" aria-label="内容生产进度">
        <li :class="{ active: briefReady, complete: hasDraft }">
          <span>01</span>
          <strong>设定 Brief</strong>
        </li>
        <li :class="{ active: hasDraft, complete: Boolean(scoreResult) }">
          <span>02</span>
          <strong>生成草稿</strong>
        </li>
        <li :class="{ active: Boolean(scoreResult), complete: Boolean(savedArticleId) }">
          <span>03</span>
          <strong>检查 GEO</strong>
        </li>
        <li :class="{ active: Boolean(savedArticleId) }">
          <span>04</span>
          <strong>保存内容</strong>
        </li>
      </ol>
    </header>

    <div v-if="loadingExisting" class="loading-existing" aria-live="polite">
      <span class="loading-existing-mark" />
      正在从内容库载入稿件…
    </div>

    <div class="writing-layout">
      <aside class="brief-panel">
        <div class="panel-heading">
          <div>
            <span class="panel-index">01 / CONTENT BRIEF</span>
            <h2>生成要求</h2>
          </div>
          <el-button :icon="Refresh" text :loading="projectLoading" @click="fetchProjects">刷新项目</el-button>
        </div>

        <el-alert
          v-if="projectsError"
          title="品牌项目加载失败"
          description="检查后端服务后重新加载。"
          type="warning"
          show-icon
          :closable="false"
          class="brief-alert"
        />

        <div v-else-if="!projectLoading && projects.length === 0" class="no-projects">
          <div class="no-projects-mark">01</div>
          <h3>先建立品牌底稿</h3>
          <p>内容生成需要品牌、产品和关键词作为事实来源。</p>
          <el-button type="primary" @click="router.push('/projects')">前往品牌项目</el-button>
        </div>

        <el-form
          v-else
          ref="briefFormRef"
          :model="brief"
          :rules="briefRules"
          label-position="top"
          class="brief-form"
          @submit.prevent="generateDraft"
        >
          <el-form-item label="品牌项目" prop="project_id">
            <el-select
              v-model="brief.project_id"
              filterable
              placeholder="选择品牌底稿"
              :loading="projectLoading"
              @change="handleProjectChange"
            >
              <el-option
                v-for="project in projects"
                :key="project.id"
                :label="project.name"
                :value="project.id"
              >
                <div class="project-option">
                  <strong>{{ project.name }}</strong>
                  <span>{{ project.product || project.industry || '品牌资料待完善' }}</span>
                </div>
              </el-option>
            </el-select>
          </el-form-item>

          <div v-if="selectedProject" class="project-context">
            <div class="context-monogram">{{ selectedProject.name.slice(0, 2) }}</div>
            <div>
              <strong>{{ selectedProject.product || selectedProject.name }}</strong>
              <span>{{ selectedProject.industry || '行业待补充' }}</span>
            </div>
            <button type="button" aria-label="编辑品牌底稿" @click="router.push('/projects')">
              <el-icon><EditPen /></el-icon>
            </button>
          </div>

          <el-form-item label="内容模板">
            <div class="template-picker">
              <el-select
                v-model="brief.template_id"
                clearable
                placeholder="不使用模板"
                @change="applySelectedTemplate"
              >
                <el-option
                  v-for="item in templates"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                >
                  <span>{{ item.name }}</span>
                  <small>{{ item.is_builtin ? '内置' : '自定义' }}</small>
                </el-option>
              </el-select>
              <el-button :icon="Collection" aria-label="管理内容模板" @click="templateDialogVisible = true" />
            </div>
            <p v-if="selectedTemplate" class="template-note">{{ selectedTemplate.description || selectedTemplate.instruction }}</p>
          </el-form-item>

          <el-form-item label="文章主题" prop="topic">
            <el-input
              v-model="brief.topic"
              type="textarea"
              :rows="3"
              resize="none"
              maxlength="160"
              show-word-limit
              placeholder="例如：企业如何选择适合自己的 AI Agent"
            />
          </el-form-item>

          <el-form-item label="目标关键词" prop="keywords">
            <el-select
              v-model="brief.keywords"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="选择或输入关键词"
            >
              <el-option
                v-for="keyword in availableKeywords"
                :key="keyword"
                :label="keyword"
                :value="keyword"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="文章长度" prop="length">
            <el-radio-group v-model="brief.length" class="length-options">
              <el-radio-button :value="600">600 字</el-radio-button>
              <el-radio-button :value="1000">1000 字</el-radio-button>
              <el-radio-button :value="1500">1500 字</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <div class="form-pair">
            <el-form-item label="内容类型" prop="content_type">
              <el-select v-model="brief.content_type">
                <el-option v-for="item in contentTypes" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标平台" prop="target_platform">
              <el-select v-model="brief.target_platform">
                <el-option v-for="item in platforms" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </div>

          <el-button
            class="generate-button"
            type="primary"
            size="large"
            :icon="MagicStick"
            :loading="generating"
            :disabled="projectLoading || !projects.length"
            native-type="submit"
          >
            {{ generating ? '正在生成内容' : 'AI 生成 GEO 内容' }}
          </el-button>

          <p class="generation-note">生成内容不会自动保存或发布，你可以先编辑和检查。</p>
        </el-form>

        <div v-if="generationError" class="generation-error" role="alert">
          <el-icon><WarningFilled /></el-icon>
          <div>
            <strong>{{ generationError.title }}</strong>
            <p>{{ generationError.message }}</p>
          </div>
        </div>
      </aside>

      <section class="canvas-panel">
        <div v-if="generating" class="drafting-state" aria-live="polite">
          <div class="drafting-mark"><MagicStick /></div>
          <span class="panel-index">02 / DRAFTING</span>
          <h2>正在组织品牌事实与内容结构</h2>
          <p>模型将根据你选择的项目、关键词和目标平台生成草稿。</p>
          <el-skeleton :rows="9" animated />
        </div>

        <div v-else-if="!hasDraft" class="blank-canvas">
          <div class="blank-sheet">
            <span>UNTITLED DRAFT</span>
            <div class="blank-line is-title" />
            <div class="blank-line" />
            <div class="blank-line is-short" />
            <div class="blank-heading">H2</div>
            <div class="blank-line" />
            <div class="blank-line is-medium" />
          </div>
          <div class="blank-copy">
            <span class="panel-index">02 / EDITORIAL CANVAS</span>
            <h2>草稿会在这里展开</h2>
            <p>完成左侧 Brief 后开始生成。标题、摘要和正文都可以继续编辑。</p>
          </div>
        </div>

        <div v-else class="editor-workspace">
          <div class="editor-toolbar">
            <div class="document-state">
              <span class="state-dot" :class="{ saved: savedArticleId && !dirty }" />
              <div>
                <strong>{{ documentState }}</strong>
                <small v-if="savedArticleId">ARTICLE {{ String(savedArticleId).padStart(4, '0') }}</small>
                <small v-else>尚未写入内容库</small>
              </div>
            </div>
            <div class="toolbar-buttons">
              <el-button :icon="Refresh" :loading="scoring" @click="scoreDraft">重新评分</el-button>
              <el-button type="primary" :icon="FolderChecked" :loading="saving" @click="saveDraft">
                {{ savedArticleId ? '保存修改' : '保存到内容库' }}
              </el-button>
            </div>
          </div>

          <div class="editor-grid">
            <article class="article-sheet">
              <div class="sheet-metadata">
                <span>{{ selectedProject?.name }}</span>
                <span>{{ brief.content_type }}</span>
                <span>{{ brief.target_platform || '通用内容' }}</span>
              </div>

              <el-input
                v-model="draft.title"
                class="title-editor"
                maxlength="100"
                placeholder="文章标题"
                @input="markEdited(true)"
              />

              <label class="editor-label" for="summary-editor">摘要</label>
              <el-input
                id="summary-editor"
                v-model="draft.summary"
                class="summary-editor"
                type="textarea"
                :rows="3"
                resize="none"
                placeholder="文章摘要"
                @input="markEdited(false)"
              />

              <div class="body-heading">
                <label class="editor-label" for="content-editor">正文 · Markdown</label>
                <span>{{ contentLength }} 字</span>
              </div>
              <el-input
                id="content-editor"
                v-model="draft.content"
                class="content-editor"
                type="textarea"
                :autosize="{ minRows: 20, maxRows: 38 }"
                resize="none"
                placeholder="生成后的正文"
                @input="markEdited(true)"
              />

              <div class="tag-editor">
                <label class="editor-label">内容标签</label>
                <el-select
                  v-model="draft.tags"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="输入后按回车添加"
                  @change="markEdited(false)"
                >
                  <el-option v-for="tag in draft.tags" :key="tag" :label="tag" :value="tag" />
                </el-select>
              </div>
            </article>

            <aside class="evidence-rail">
              <div class="score-heading">
                <span class="panel-index">03 / GEO EVIDENCE</span>
                <el-tag v-if="scoreStale" type="warning" effect="plain" size="small">待更新</el-tag>
              </div>

              <div v-if="scoreResult" class="score-card">
                <div class="score-ring" :style="scoreRingStyle">
                  <div>
                    <strong>{{ scoreResult.score }}</strong>
                    <span>/ 100</span>
                  </div>
                </div>
                <p>{{ scoreSummary }}</p>
              </div>

              <div v-else class="score-pending">
                <el-icon><DataAnalysis /></el-icon>
                <strong>等待评分</strong>
                <p>草稿生成后自动执行本地规则检查。</p>
              </div>

              <div v-if="scoreResult" class="dimension-list">
                <div v-for="dimension in scoreDimensions" :key="dimension.key" class="dimension-row">
                  <div>
                    <span>{{ dimension.label }}</span>
                    <strong>{{ dimension.value }}</strong>
                  </div>
                  <div class="dimension-track">
                    <span :style="{ width: `${dimension.value}%` }" />
                  </div>
                </div>
              </div>

              <div v-if="scoreResult" class="suggestion-list">
                <span class="editor-label">改进建议</span>
                <ul v-if="scoreResult.suggestions.length">
                  <li v-for="suggestion in scoreResult.suggestions" :key="suggestion">
                    {{ suggestion }}
                  </li>
                </ul>
                <div v-else class="all-clear">
                  <el-icon><CircleCheckFilled /></el-icon>
                  当前规则项已全部覆盖
                </div>
              </div>

              <p class="score-note">评分来自本地规则，用于内容检查，不代表真实搜索排名。</p>
            </aside>
          </div>
        </div>
      </section>
    </div>

    <el-dialog v-model="batchDialogVisible" title="批量生成文章" width="620px" class="editorial-dialog">
      <div class="batch-intro">
        <span>BATCH / 最多 5 篇</span>
        <p>沿用当前品牌、关键词、长度、内容类型和模板。每行填写一个主题，生成后自动保存为草稿。</p>
      </div>
      <el-input
        v-model="batchTopics"
        type="textarea"
        :rows="7"
        resize="none"
        placeholder="企业如何选择 AI Agent&#10;AI Agent 项目落地的常见误区"
      />
      <div v-if="batchResult" class="batch-result">
        <strong>已保存 {{ batchResult.created_count }} 篇</strong>
        <span v-if="batchResult.failed_count">{{ batchResult.failed_count }} 篇未完成</span>
      </div>
      <template #footer>
        <el-button v-if="batchResult?.created_count" @click="router.push('/content-library')">查看内容库</el-button>
        <el-button @click="batchDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="batchGenerating" :disabled="!canBatchGenerate" @click="generateBatch">
          生成并保存草稿
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="templateDialogVisible" title="内容模板" width="760px" class="editorial-dialog">
      <div class="template-registry">
        <article v-for="item in templates" :key="item.id" :class="{ active: brief.template_id === item.id }">
          <div>
            <span>{{ item.is_builtin ? 'BUILT-IN' : 'CUSTOM' }} / {{ item.content_type }}</span>
            <strong>{{ item.name }}</strong>
            <p>{{ item.description || item.instruction }}</p>
          </div>
          <div class="template-actions">
            <el-button text @click="selectTemplate(item)">使用</el-button>
            <el-button v-if="!item.is_builtin" text @click="editTemplate(item)">编辑</el-button>
            <el-button v-if="item.is_builtin" text @click="copyTemplate(item)">复制</el-button>
            <el-button v-if="!item.is_builtin" text type="danger" @click="removeTemplate(item)">删除</el-button>
          </div>
        </article>
      </div>

      <div class="template-editor">
        <span>{{ templateForm.id ? 'EDIT CUSTOM TEMPLATE' : 'NEW CUSTOM TEMPLATE' }}</span>
        <div class="template-form-row">
          <el-input v-model="templateForm.name" maxlength="80" placeholder="模板名称" />
          <el-select v-model="templateForm.content_type">
            <el-option v-for="item in contentTypes" :key="item" :label="item" :value="item" />
          </el-select>
        </div>
        <el-input v-model="templateForm.description" maxlength="300" placeholder="用途说明（可选）" />
        <el-input v-model="templateForm.instruction" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="告诉模型应采用什么结构、语气和事实边界" />
        <div class="template-editor-actions">
          <el-button v-if="templateForm.id" text @click="resetTemplateForm">取消编辑</el-button>
          <el-button type="primary" :loading="templateSaving" @click="saveTemplate">{{ templateForm.id ? '保存修改' : '新增模板' }}</el-button>
        </div>
      </div>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  CircleCheckFilled,
  Collection,
  DataAnalysis,
  EditPen,
  FolderChecked,
  Files,
  MagicStick,
  Refresh,
  WarningFilled
} from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { projectApi } from '@/api/project'

const router = useRouter()
const route = useRoute()
const briefFormRef = ref(null)
const projects = ref([])
const projectLoading = ref(false)
const projectsError = ref(false)
const generating = ref(false)
const scoring = ref(false)
const saving = ref(false)
const generationError = ref(null)
const hasDraft = ref(false)
const scoreResult = ref(null)
const scoreStale = ref(false)
const savedArticleId = ref(null)
const dirty = ref(false)
const loadingExisting = ref(false)
const articleStatus = ref('draft')
const templates = ref([])
const templateDialogVisible = ref(false)
const templateSaving = ref(false)
const batchDialogVisible = ref(false)
const batchGenerating = ref(false)
const batchTopics = ref('')
const batchResult = ref(null)

const contentTypes = ['行业科普', '品牌介绍', '产品介绍', '解决方案', '对比文章', 'FAQ', '新闻稿']
const platforms = [
  { label: '通用内容', value: '' },
  { label: '知乎', value: '知乎' },
  { label: '今日头条', value: '今日头条' },
  { label: '百家号', value: '百家号' },
  { label: '搜狐', value: '搜狐' },
  { label: '小红书', value: '小红书' },
  { label: '抖音', value: '抖音' },
  { label: '快手', value: '快手' },
  { label: 'Bilibili', value: 'Bilibili' },
  { label: '视频号', value: '视频号' },
  { label: 'TikTok', value: 'TikTok' }
]

const brief = reactive({
  project_id: null,
  topic: '',
  keywords: [],
  length: 1000,
  content_type: '行业科普',
  target_platform: '',
  template_id: null
})

const templateForm = reactive({
  id: null,
  name: '',
  description: '',
  content_type: '行业科普',
  instruction: ''
})

const draft = reactive({
  title: '',
  summary: '',
  content: '',
  tags: []
})

const briefRules = {
  project_id: [{ required: true, message: '请选择品牌项目', trigger: 'change' }],
  topic: [{ required: true, message: '请输入文章主题', trigger: 'blur' }],
  keywords: [{ type: 'array', required: true, min: 1, message: '至少选择一个目标关键词', trigger: 'change' }]
}

const selectedProject = computed(() => (
  projects.value.find(project => project.id === brief.project_id) || null
))

const selectedTemplate = computed(() => (
  templates.value.find(item => item.id === brief.template_id) || null
))

const parsedBatchTopics = computed(() => [...new Set(
  batchTopics.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
)].slice(0, 6))

const canBatchGenerate = computed(() => Boolean(
  brief.project_id
  && brief.keywords.length
  && parsedBatchTopics.value.length > 0
  && parsedBatchTopics.value.length <= 5
  && !batchGenerating.value
))

const availableKeywords = computed(() => {
  const source = [...(selectedProject.value?.keywords || []), ...brief.keywords]
  return [...new Set(source)]
})

const briefReady = computed(() => Boolean(
  brief.project_id && brief.topic.trim() && brief.keywords.length
))

const contentLength = computed(() => draft.content.replace(/\s/g, '').length)

const documentState = computed(() => {
  if (!savedArticleId.value) return '未保存草稿'
  return dirty.value ? '有未保存修改' : '已保存到内容库'
})

const scoreDimensions = computed(() => {
  const dimensions = scoreResult.value?.dimensions || {}
  return [
    { key: 'entity', label: '品牌实体明确度', value: dimensions.entity || 0 },
    { key: 'keywords', label: '关键词覆盖', value: dimensions.keywords || 0 },
    { key: 'structure', label: '内容结构', value: dimensions.structure || 0 },
    { key: 'faq', label: 'FAQ 完整性', value: dimensions.faq || 0 },
    { key: 'citation', label: '引用友好度', value: dimensions.citation || 0 }
  ]
})

const scoreRingStyle = computed(() => ({
  '--score-angle': `${(scoreResult.value?.score || 0) * 3.6}deg`
}))

const scoreSummary = computed(() => {
  const score = scoreResult.value?.score || 0
  if (score >= 85) return '内容结构完整，可以进入人工复核。'
  if (score >= 70) return '基础质量良好，仍有几项可以加强。'
  return '建议先根据右侧提示补充内容证据。'
})

const fetchProjects = async () => {
  projectLoading.value = true
  projectsError.value = false
  try {
    const response = await projectApi.getProjects()
    projects.value = response.data || []
    if (!brief.project_id && projects.value.length) {
      brief.project_id = projects.value[0].id
      applyProjectKeywords()
    }
  } catch (error) {
    projectsError.value = true
    console.error('加载品牌项目失败:', error)
  } finally {
    projectLoading.value = false
  }
}

const fetchTemplates = async () => {
  try {
    const response = await articleApi.getTemplates()
    templates.value = response.data || []
  } catch (error) {
    console.error('加载内容模板失败:', error)
  }
}

const applySelectedTemplate = () => {
  if (selectedTemplate.value?.content_type) {
    brief.content_type = selectedTemplate.value.content_type
  }
}

const selectTemplate = (item) => {
  brief.template_id = item.id
  applySelectedTemplate()
  templateDialogVisible.value = false
}

const resetTemplateForm = () => {
  Object.assign(templateForm, {
    id: null,
    name: '',
    description: '',
    content_type: brief.content_type,
    instruction: ''
  })
}

const editTemplate = (item) => {
  Object.assign(templateForm, {
    id: item.id,
    name: item.name,
    description: item.description,
    content_type: item.content_type,
    instruction: item.instruction
  })
}

const copyTemplate = (item) => {
  Object.assign(templateForm, {
    id: null,
    name: `${item.name}副本`,
    description: item.description,
    content_type: item.content_type,
    instruction: item.instruction
  })
}

const saveTemplate = async () => {
  if (!templateForm.name.trim() || !templateForm.instruction.trim()) {
    ElMessage.warning('模板名称和写作要求不能为空')
    return
  }
  templateSaving.value = true
  const isUpdate = Boolean(templateForm.id)
  try {
    const payload = {
      name: templateForm.name.trim(),
      description: templateForm.description.trim(),
      content_type: templateForm.content_type,
      instruction: templateForm.instruction.trim()
    }
    const response = templateForm.id
      ? await articleApi.updateTemplate(templateForm.id, payload)
      : await articleApi.createTemplate(payload)
    brief.template_id = response.data.id
    await fetchTemplates()
    applySelectedTemplate()
    resetTemplateForm()
    ElMessage.success(isUpdate ? '模板已更新' : '模板已创建')
  } catch (error) {
    console.error('保存内容模板失败:', error)
  } finally {
    templateSaving.value = false
  }
}

const removeTemplate = async (item) => {
  try {
    await articleApi.deleteTemplate(item.id)
    if (brief.template_id === item.id) brief.template_id = null
    await fetchTemplates()
    if (templateForm.id === item.id) resetTemplateForm()
    ElMessage.success('模板已删除')
  } catch (error) {
    console.error('删除内容模板失败:', error)
  }
}

const generateBatch = async () => {
  if (!canBatchGenerate.value) {
    if (parsedBatchTopics.value.length > 5) ElMessage.warning('一次最多生成 5 篇')
    return
  }
  batchGenerating.value = true
  batchResult.value = null
  try {
    const response = await articleApi.generateBatch({
      project_id: brief.project_id,
      topics: parsedBatchTopics.value,
      keywords: brief.keywords,
      length: brief.length,
      content_type: brief.content_type,
      target_platform: brief.target_platform,
      template_id: brief.template_id
    })
    batchResult.value = response.data
    ElMessage.success(`已保存 ${response.data.created_count} 篇批量草稿`)
  } catch (error) {
    console.error('批量生成失败:', error)
  } finally {
    batchGenerating.value = false
  }
}

const applyProjectKeywords = () => {
  brief.keywords = [...(selectedProject.value?.keywords || [])]
}

const handleProjectChange = () => {
  applyProjectKeywords()
  if (hasDraft.value) {
    resetDraft()
    ElMessage.info('品牌项目已切换，请重新生成草稿')
  }
}

const resetDraft = () => {
  Object.assign(draft, { title: '', summary: '', content: '', tags: [] })
  hasDraft.value = false
  scoreResult.value = null
  scoreStale.value = false
  savedArticleId.value = null
  dirty.value = false
  articleStatus.value = 'draft'
  generationError.value = null
}

const generateDraft = async () => {
  if (generating.value) return
  const valid = await briefFormRef.value?.validate().catch(() => false)
  if (!valid) return

  generating.value = true
  generationError.value = null
  try {
    const response = await articleApi.generateArticle({
      project_id: brief.project_id,
      topic: brief.topic.trim(),
      keywords: brief.keywords,
      length: brief.length,
      content_type: brief.content_type,
      target_platform: brief.target_platform,
      template_id: brief.template_id
    })
    const article = response.data
    Object.assign(draft, {
      title: article.title || brief.topic,
      summary: article.summary || '',
      content: mergeFaqIntoContent(article.content || '', article.faq),
      tags: article.tags || brief.keywords
    })
    hasDraft.value = true
    savedArticleId.value = null
    articleStatus.value = 'draft'
    dirty.value = true
    await scoreDraft({ silent: true })
  } catch (error) {
    generationError.value = formatGenerationError(error)
  } finally {
    generating.value = false
  }
}

const scoreDraft = async ({ silent = false } = {}) => {
  if (!hasDraft.value || scoring.value) return
  if (!draft.title.trim() || !draft.content.trim()) {
    if (!silent) ElMessage.warning('标题和正文不能为空')
    return
  }

  scoring.value = true
  try {
    scoreResult.value = await articleApi.scoreArticle({
      title: draft.title,
      content: draft.content,
      brand: selectedProject.value.name,
      keywords: brief.keywords
    })
    scoreStale.value = false
    if (!silent) ElMessage.success('GEO 评分已更新')
  } catch (error) {
    console.error('GEO 评分失败:', error)
    if (!silent) ElMessage.error('评分失败，请稍后重试')
  } finally {
    scoring.value = false
  }
}

const saveDraft = async () => {
  if (!hasDraft.value || saving.value) return
  if (!draft.title.trim() || !draft.content.trim()) {
    ElMessage.warning('标题和正文不能为空')
    return
  }

  saving.value = true
  const isUpdate = Boolean(savedArticleId.value)
  const articleFields = {
    title: draft.title,
    summary: draft.summary,
    content: draft.content,
    tags: draft.tags,
    status: articleStatus.value
  }

  try {
    const response = savedArticleId.value
      ? await articleApi.updateArticle(savedArticleId.value, articleFields)
      : await articleApi.createArticle({ project_id: brief.project_id, ...articleFields })
    const savedArticle = response.data
    savedArticleId.value = savedArticle.id
    dirty.value = false
    hydrateStoredScore(savedArticle)
    ElMessage.success(isUpdate ? '草稿修改已保存' : '文章已保存到内容库')
  } catch (error) {
    console.error('保存文章失败:', error)
  } finally {
    saving.value = false
  }
}

const hydrateStoredScore = (article) => {
  scoreResult.value = {
    score: article.geo_score,
    dimensions: article.geo_analysis?.dimensions || {},
    suggestions: article.geo_analysis?.suggestions || []
  }
  scoreStale.value = false
}

const loadExistingArticle = async (articleId) => {
  loadingExisting.value = true
  generationError.value = null
  try {
    const response = await articleApi.getArticle(articleId)
    const article = response.data
    brief.project_id = article.project_id
    brief.topic = article.title
    applyProjectKeywords()
    Object.assign(draft, {
      title: article.title || '',
      summary: article.summary || '',
      content: article.content || '',
      tags: article.tags || []
    })
    hasDraft.value = true
    savedArticleId.value = article.id
    articleStatus.value = article.status || 'draft'
    dirty.value = false
    hydrateStoredScore(article)
  } catch (error) {
    generationError.value = {
      title: '稿件载入失败',
      message: '这篇稿件可能已不存在，请返回内容库刷新后重试。'
    }
  } finally {
    loadingExisting.value = false
  }
}

const markEdited = (affectsScore) => {
  dirty.value = true
  if (affectsScore) scoreStale.value = true
}

const mergeFaqIntoContent = (content, faq) => {
  if (!Array.isArray(faq) || faq.length === 0 || /(?:FAQ|常见问题|常见问答)/i.test(content)) {
    return content
  }
  const faqText = faq
    .filter(item => item?.question)
    .map(item => `### ${item.question}\n\n${item.answer || ''}`)
    .join('\n\n')
  return faqText ? `${content.trim()}\n\n## FAQ\n\n${faqText}` : content
}

const formatGenerationError = (error) => {
  const status = error.response?.status
  const backendMessage = error.response?.data?.msg
  if (status === 503) {
    return {
      title: 'AI 服务尚未配置',
      message: '请到“系统设置”填写自定义 AI API，或配置服务器默认模型后重试。'
    }
  }
  if (status === 502) {
    return {
      title: 'AI 服务暂时没有返回内容',
      message: backendMessage || '检查模型服务地址和模型名称后重试。'
    }
  }
  return {
    title: '内容生成失败',
    message: backendMessage || '检查网络和后端服务后重试。'
  }
}

onMounted(async () => {
  await Promise.all([fetchProjects(), fetchTemplates()])
  const articleId = Number.parseInt(route.query.articleId, 10)
  if (Number.isInteger(articleId) && articleId > 0) {
    await loadExistingArticle(articleId)
  }
})
</script>

<style lang="scss" scoped>
.content-creation {
  --ink: #17222b;
  --teal: #0d5c63;
  --signal: #39b8b2;
  --mist: #edf3f3;
  --amber: #e8a33a;
  --paper: #ffffff;
  max-width: 1540px;
  margin: 0 auto;
  color: var(--ink);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", sans-serif;
}

.loading-existing {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 16px;
  padding: 11px 14px;
  border: 1px solid rgba(13, 92, 99, 0.18);
  background: #f4f8f8;
  color: var(--teal);
  font-size: 13px;
  font-weight: 600;
}

.loading-existing-mark {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--signal);
  box-shadow: 0 0 0 0 rgba(57, 184, 178, 0.35);
  animation: load-pulse 1.4s ease-out infinite;
}

@keyframes load-pulse {
  70% { box-shadow: 0 0 0 8px rgba(57, 184, 178, 0); }
  100% { box-shadow: 0 0 0 0 rgba(57, 184, 178, 0); }
}

@media (prefers-reduced-motion: reduce) {
  .loading-existing-mark { animation: none; }
}

.eyebrow,
.panel-index,
.editor-label {
  color: var(--teal);
  font-family: "Cascadia Mono", "SFMono-Regular", monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.13em;
}

.workbench-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 40px;
  padding: 16px 4px 24px;
  border-bottom: 1px solid #cfdcdd;

  h1 {
    margin: 5px 0 8px;
    font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
    font-size: clamp(34px, 4vw, 52px);
    font-weight: 750;
    letter-spacing: -0.045em;
    line-height: 1.05;
  }

  > div > p:last-child {
    color: #68777e;
    font-size: 14px;
  }
}

.flow-track {
  display: flex;
  min-width: 500px;

  li {
    position: relative;
    min-width: 122px;
    padding: 0 14px 11px;
    border-bottom: 2px solid #d6e0e0;
    color: #98a4a8;

    &::after {
      position: absolute;
      right: -3px;
      bottom: -4px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #c8d5d5;
      content: "";
    }

    &.active {
      border-color: var(--teal);
      color: var(--ink);

      &::after { background: var(--teal); }
    }

    &.complete {
      border-color: var(--signal);

      &::after { background: var(--signal); }
    }

    span {
      display: block;
      margin-bottom: 3px;
      font: 700 9px/1 "Cascadia Mono", monospace;
      letter-spacing: 0.1em;
    }

    strong {
      font-size: 12px;
      font-weight: 650;
    }
  }
}

.writing-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 18px;
  padding-top: 20px;
}

.brief-panel,
.canvas-panel {
  border: 1px solid #d5e1e1;
  background: var(--paper);
}

.brief-panel {
  align-self: start;
  padding: 24px;
  border-radius: 16px 4px 16px 4px;
  box-shadow: 0 12px 30px rgba(23, 34, 43, 0.045);
}

.panel-heading,
.editor-toolbar,
.score-heading,
.body-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.panel-heading {
  margin-bottom: 22px;

  h2 {
    margin-top: 5px;
    font-size: 21px;
  }
}

.brief-form {
  :deep(.el-form-item) {
    margin-bottom: 19px;
  }

  :deep(.el-form-item__label) {
    color: #43545b;
    font-size: 12px;
    font-weight: 650;
  }

  :deep(.el-select) {
    width: 100%;
  }

  :deep(.el-input__wrapper),
  :deep(.el-textarea__inner) {
    border-radius: 9px 3px 9px 3px;
  }
}

.brief-alert {
  margin-bottom: 18px;
}

.project-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;

  span {
    color: #8a979c;
    font-size: 11px;
  }
}

.project-context {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: -4px 0 20px;
  padding: 11px;
  border: 1px solid #d9e7e6;
  border-radius: 10px 3px 10px 3px;
  background: #f3f8f8;

  > div:nth-child(2) {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;

    strong {
      overflow: hidden;
      color: #294248;
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      color: #829095;
      font-size: 10px;
    }
  }

  button {
    color: #6f8388;

    &:focus-visible {
      outline: 2px solid var(--signal);
      outline-offset: 3px;
    }
  }
}

.context-monogram {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 9px 3px 9px 3px;
  background: var(--teal);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}

.length-options {
  display: flex;
  width: 100%;

  :deep(.el-radio-button) {
    flex: 1;
  }

  :deep(.el-radio-button__inner) {
    width: 100%;
    padding-right: 8px;
    padding-left: 8px;
  }
}

.form-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.generate-button {
  --el-button-bg-color: var(--teal);
  --el-button-border-color: var(--teal);
  --el-button-hover-bg-color: #14747b;
  --el-button-hover-border-color: #14747b;
  width: 100%;
  min-height: 46px;
  border-radius: 11px 3px 11px 3px;
}

.generation-note {
  margin-top: 10px;
  color: #8a979c;
  font-size: 10px;
  line-height: 1.5;
  text-align: center;
}

.generation-error {
  display: flex;
  gap: 10px;
  margin-top: 18px;
  padding: 13px;
  border: 1px solid #efd8ad;
  border-radius: 9px 3px 9px 3px;
  background: #fff9ed;
  color: #76531d;

  .el-icon {
    margin-top: 2px;
    color: var(--amber);
  }

  strong { font-size: 12px; }

  p {
    margin-top: 3px;
    font-size: 11px;
    line-height: 1.55;
  }
}

.no-projects {
  padding: 26px 12px 12px;
  text-align: center;

  h3 { margin: 14px 0 6px; }

  p {
    margin-bottom: 18px;
    color: #78868c;
    font-size: 12px;
  }
}

.no-projects-mark {
  display: grid;
  width: 50px;
  height: 50px;
  margin: 0 auto;
  place-items: center;
  border: 1px solid #bdd3d2;
  border-radius: 14px 4px 14px 4px;
  color: var(--teal);
  font: 700 12px/1 "Cascadia Mono", monospace;
}

.canvas-panel {
  min-height: 710px;
  overflow: hidden;
  border-radius: 4px 18px 4px 18px;
  box-shadow: 0 16px 40px rgba(23, 34, 43, 0.055);
}

.blank-canvas,
.drafting-state {
  display: flex;
  min-height: 710px;
  align-items: center;
  justify-content: center;
}

.blank-canvas {
  gap: clamp(35px, 7vw, 90px);
  padding: 55px;
  background:
    linear-gradient(90deg, rgba(57, 184, 178, 0.04), transparent 42%),
    #fbfdfd;
}

.blank-sheet {
  width: min(340px, 42%);
  min-height: 430px;
  padding: 38px 34px;
  border: 1px solid #d8e2e2;
  border-radius: 3px 14px 3px 14px;
  background: #fff;
  box-shadow: 18px 20px 0 #eaf2f2;

  > span {
    color: #9aa7ab;
    font: 700 9px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }
}

.blank-line {
  height: 7px;
  margin-top: 15px;
  border-radius: 99px;
  background: #e6ecec;

  &.is-title {
    width: 82%;
    height: 18px;
    margin: 24px 0 36px;
    background: #cbd9d9;
  }

  &.is-short { width: 65%; }
  &.is-medium { width: 78%; }
}

.blank-heading {
  display: grid;
  width: 34px;
  height: 25px;
  margin-top: 40px;
  place-items: center;
  border-radius: 4px;
  background: #deeeee;
  color: #6f9293;
  font: 700 9px/1 "Cascadia Mono", monospace;
}

.blank-copy {
  max-width: 330px;

  h2 {
    margin: 10px 0;
    font-size: 26px;
  }

  p {
    color: #748188;
    font-size: 13px;
    line-height: 1.7;
  }
}

.drafting-state {
  flex-direction: column;
  padding: 50px 12%;
  text-align: center;

  h2 {
    margin: 12px 0 8px;
    font-size: 24px;
  }

  > p {
    margin-bottom: 32px;
    color: #77858a;
    font-size: 13px;
  }

  :deep(.el-skeleton) {
    width: min(720px, 100%);
    text-align: left;
  }
}

.drafting-mark {
  display: grid;
  width: 54px;
  height: 54px;
  margin-bottom: 18px;
  place-items: center;
  border-radius: 16px 4px 16px 4px;
  background: var(--teal);
  color: #fff;

  svg {
    width: 24px;
  }
}

.editor-workspace {
  min-width: 0;
}

.editor-toolbar {
  min-height: 70px;
  padding: 12px 22px;
  border-bottom: 1px solid #dfe8e8;
  background: #f8fbfb;
}

.document-state {
  display: flex;
  align-items: center;
  gap: 10px;

  > div {
    display: flex;
    flex-direction: column;
  }

  strong { font-size: 12px; }

  small {
    margin-top: 2px;
    color: #879499;
    font: 650 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.08em;
  }
}

.state-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 0 4px rgba(232, 163, 58, 0.12);

  &.saved {
    background: var(--signal);
    box-shadow: 0 0 0 4px rgba(57, 184, 178, 0.12);
  }
}

.toolbar-buttons {
  display: flex;
  gap: 8px;
}

.editor-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 285px;
  min-height: 640px;
}

.article-sheet {
  min-width: 0;
  padding: 40px clamp(28px, 5vw, 72px) 55px;
}

.sheet-metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 22px;

  span {
    padding: 4px 8px;
    border: 1px solid #d5e3e2;
    border-radius: 999px;
    color: #557176;
    font-size: 10px;
  }
}

.title-editor {
  margin-bottom: 26px;

  :deep(.el-input__wrapper) {
    padding: 0;
    box-shadow: none;
  }

  :deep(.el-input__inner) {
    height: auto;
    color: var(--ink);
    font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
    font-size: clamp(28px, 3vw, 40px);
    font-weight: 750;
    letter-spacing: -0.035em;
    line-height: 1.25;
  }
}

.editor-label {
  display: block;
  margin-bottom: 8px;
  color: #71868b;
  font-size: 9px;
}

.summary-editor {
  margin-bottom: 28px;

  :deep(.el-textarea__inner) {
    padding: 14px;
    border-radius: 8px 3px 8px 3px;
    background: #f6f9f9;
    color: #53656c;
    font-size: 13px;
    line-height: 1.7;
    box-shadow: inset 3px 0 var(--signal);
  }
}

.body-heading {
  margin-bottom: 8px;

  .editor-label { margin-bottom: 0; }

  span {
    color: #8a979c;
    font: 650 10px/1 "Cascadia Mono", monospace;
  }
}

.content-editor {
  :deep(.el-textarea__inner) {
    padding: 20px 0;
    border: 0;
    border-top: 1px solid #e1e9e9;
    border-bottom: 1px solid #e1e9e9;
    border-radius: 0;
    color: #26373e;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 14px;
    line-height: 1.9;
    box-shadow: none;
  }
}

.tag-editor {
  margin-top: 26px;

  :deep(.el-select) { width: 100%; }
}

.evidence-rail {
  position: relative;
  padding: 28px 22px;
  border-left: 1px solid #dbe5e5;
  background: #f4f8f8;

  &::before {
    position: absolute;
    top: 0;
    left: -2px;
    width: 3px;
    height: 86px;
    background: linear-gradient(var(--signal), transparent);
    content: "";
  }
}

.score-card {
  padding: 24px 0 20px;
  text-align: center;

  > p {
    margin-top: 12px;
    color: #6c7c82;
    font-size: 11px;
    line-height: 1.6;
  }
}

.score-ring {
  display: grid;
  width: 120px;
  height: 120px;
  margin: 0 auto;
  place-items: center;
  border-radius: 50%;
  background: conic-gradient(var(--signal) var(--score-angle), #dce7e7 0deg);

  > div {
    display: grid;
    width: 96px;
    height: 96px;
    place-items: center;
    align-content: center;
    border-radius: 50%;
    background: #f4f8f8;
  }

  strong {
    font: 750 38px/1 "Arial Narrow", sans-serif;
    letter-spacing: -0.04em;
  }

  span {
    margin-top: 3px;
    color: #839196;
    font: 650 9px/1 "Cascadia Mono", monospace;
  }
}

.score-pending {
  padding: 42px 0;
  color: #829095;
  text-align: center;

  .el-icon {
    margin-bottom: 12px;
    font-size: 28px;
  }

  strong {
    display: block;
    color: #5e7177;
    font-size: 13px;
  }

  p {
    margin-top: 5px;
    font-size: 10px;
    line-height: 1.5;
  }
}

.dimension-list {
  padding: 18px 0;
  border-top: 1px solid #d9e4e4;
  border-bottom: 1px solid #d9e4e4;
}

.dimension-row {
  margin-bottom: 14px;

  &:last-child { margin-bottom: 0; }

  > div:first-child {
    display: flex;
    justify-content: space-between;
    margin-bottom: 5px;
    color: #61747a;
    font-size: 10px;

    strong {
      color: var(--ink);
      font: 700 10px/1 "Cascadia Mono", monospace;
    }
  }
}

.dimension-track {
  height: 4px;
  overflow: hidden;
  border-radius: 99px;
  background: #dbe6e6;

  span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--teal);
    transition: width 260ms ease;
  }
}

.suggestion-list {
  padding-top: 20px;

  ul {
    display: grid;
    gap: 9px;
  }

  li {
    position: relative;
    padding-left: 13px;
    color: #596c72;
    font-size: 10px;
    line-height: 1.55;

    &::before {
      position: absolute;
      top: 7px;
      left: 0;
      width: 4px;
      height: 4px;
      border-radius: 50%;
      background: var(--amber);
      content: "";
    }
  }
}

.all-clear {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--teal);
  font-size: 11px;
}

.score-note {
  margin-top: 22px;
  color: #97a2a6;
  font-size: 9px;
  line-height: 1.5;
}

.batch-entry {
  margin-top: 16px;
  border-color: rgba(13, 92, 99, 0.28);
  color: var(--teal);
}

.template-picker {
  display: grid;
  width: 100%;
  grid-template-columns: minmax(0, 1fr) 38px;
  gap: 8px;

  :deep(.el-select) { width: 100%; }
  small { float: right; color: #829095; font-size: 10px; }
}

.template-note {
  width: 100%;
  margin: 6px 0 0;
  color: #74858a;
  font-size: 10px;
  line-height: 1.55;
}

.batch-intro {
  margin-bottom: 16px;
  padding: 14px 16px;
  border-left: 3px solid var(--signal);
  background: var(--mist);

  span {
    color: var(--teal);
    font: 700 10px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  p { margin: 7px 0 0; color: #64767b; font-size: 12px; line-height: 1.65; }
}

.batch-result {
  display: flex;
  gap: 14px;
  margin-top: 14px;
  color: var(--teal);
  font-size: 12px;

  span { color: var(--amber); }
}

.template-registry {
  display: grid;
  max-height: 300px;
  overflow-y: auto;
  border-top: 1px solid #dce5e4;

  article {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    padding: 14px 12px;
    border-bottom: 1px solid #dce5e4;
  }

  article.active { background: #edf7f5; }
  span { color: var(--teal); font: 700 9px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
  strong { display: block; margin-top: 5px; color: var(--ink); font-size: 14px; }
  p { margin: 5px 0 0; color: #728187; font-size: 11px; line-height: 1.55; }
}

.template-actions {
  display: flex;
  align-items: center;
}

.template-editor {
  display: grid;
  gap: 10px;
  margin-top: 22px;
  padding: 18px;
  background: #f4f8f7;

  > span { color: var(--teal); font: 700 10px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
}

.template-form-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(150px, 0.6fr);
  gap: 10px;
}

.template-editor-actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 1180px) {
  .workbench-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .flow-track {
    width: 100%;
    min-width: 0;

    li {
      min-width: 0;
      flex: 1;
    }
  }

  .writing-layout {
    grid-template-columns: 1fr;
  }

  .brief-panel {
    width: 100%;
  }
}

@media (max-width: 820px) {
  .editor-grid {
    grid-template-columns: 1fr;
  }

  .evidence-rail {
    border-top: 1px solid #dbe5e5;
    border-left: 0;
  }

  .blank-canvas {
    align-items: flex-start;
    flex-direction: column-reverse;
  }

  .blank-sheet {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .flow-track {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px 0;
  }

  .form-pair {
    grid-template-columns: 1fr;
  }

  .template-form-row { grid-template-columns: 1fr; }

  .editor-toolbar,
  .toolbar-buttons {
    align-items: stretch;
    flex-direction: column;
  }

  .article-sheet {
    padding: 30px 20px 40px;
  }

  .blank-canvas {
    padding: 32px 22px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dimension-track span {
    transition: none;
  }
}
</style>
