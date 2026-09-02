<template>
  <main class="distribution-center">
    <header class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">DISTRIBUTION ROUTING / 内容分发</p>
        <h1>发布中心</h1>
        <p>从就绪稿件创建独立渠道任务。每个平台单独记录进度，失败不会拖住其他渠道。</p>
      </div>
      <div class="live-indicator" :class="{ active: activeJobCount > 0 }">
        <span class="pulse" aria-hidden="true" />
        <div>
          <strong>{{ activeJobCount ? `${activeJobCount} 个任务处理中` : '任务轨迹已同步' }}</strong>
          <small>{{ lastSyncedLabel }}</small>
        </div>
      </div>
    </header>

    <section class="dispatch-console" aria-labelledby="dispatch-title">
      <div class="dispatch-form">
        <div class="section-heading">
          <span>CONTROL / 01</span>
          <div>
            <h2 id="dispatch-title">建立分发任务</h2>
            <p>一次选择多个平台，系统会为每个平台创建独立任务。</p>
          </div>
        </div>

        <div class="field-block">
          <label for="article-select">就绪稿件</label>
          <el-select
            id="article-select"
            v-model="selectedArticleId"
            filterable
            clearable
            placeholder="选择一篇已标记就绪的文章"
            :loading="articlesLoading"
            :disabled="articlesLoading || articlesLoadError"
          >
            <el-option
              v-for="article in articles"
              :key="article.id"
              :label="article.title"
              :value="article.id"
            >
              <div class="article-option">
                <span>{{ article.title }}</span>
                <small>{{ article.project_name }} · GEO {{ article.geo_score }}</small>
              </div>
            </el-option>
          </el-select>
          <p v-if="articlesLoadError" class="field-error">就绪稿件暂未载入，请刷新后重试。</p>
          <div v-else-if="!articlesLoading && articles.length === 0" class="field-guidance">
            还没有就绪稿件。请先在内容库中将文章标记为“就绪”。
            <button type="button" @click="router.push('/content-library')">前往内容库</button>
          </div>
        </div>

        <article v-if="selectedArticle" class="article-proof">
          <div class="proof-id">A-{{ String(selectedArticle.id).padStart(4, '0') }}</div>
          <div class="proof-copy">
            <span>{{ selectedArticle.project_name }}</span>
            <strong>{{ selectedArticle.title }}</strong>
            <div class="proof-tags">
              <i v-for="tag in selectedArticle.tags.slice(0, 3)" :key="tag"># {{ tag }}</i>
              <i v-if="selectedArticle.tags.length === 0">尚未设置标签</i>
            </div>
          </div>
          <div :class="['proof-score', scoreTone(selectedArticle.geo_score)]">
            <b>{{ selectedArticle.geo_score }}</b>
            <small>GEO</small>
          </div>
        </article>

        <fieldset class="platform-fieldset">
          <legend>分发渠道</legend>
          <p>第一阶段优先验证文章平台；每个选项都会生成一条可重试任务。</p>
          <div class="platform-grid">
            <button
              v-for="platform in platforms"
              :key="platform.key"
              type="button"
              :class="['platform-option', { selected: selectedPlatforms.includes(platform.key) }]"
              :aria-pressed="selectedPlatforms.includes(platform.key)"
              @click="togglePlatform(platform.key)"
            >
              <span class="platform-code">{{ platform.code }}</span>
              <span class="platform-name">{{ platform.name }}</span>
              <small>{{ platform.note }}</small>
              <span class="selection-mark" aria-hidden="true">
                {{ selectedPlatforms.includes(platform.key) ? '✓' : '+' }}
              </span>
            </button>
          </div>
        </fieldset>

        <section
          :class="['asset-panel', { required: imageRequired, missing: imageRequired && !selectedImage }]"
          aria-labelledby="asset-title"
        >
          <div class="asset-heading">
            <span class="asset-code">ASSET / IMG</span>
            <div>
              <strong id="asset-title">共享图片资产</strong>
              <small>百家号取首图作为封面，小红书用作笔记图片；其他渠道可按各自规则使用。</small>
            </div>
            <i>{{ imageRequired ? '所选渠道必需' : '可选' }}</i>
          </div>

          <article v-if="selectedImage" class="asset-record">
            <img :src="selectedImage.previewUrl" :alt="`${selectedImage.name} 预览`" />
            <div class="asset-copy">
              <span>READY / 已入素材库</span>
              <strong>{{ selectedImage.name }}</strong>
              <small>{{ selectedImage.filename }}</small>
              <div v-if="imagePlatforms.length" class="asset-routes">
                <b v-for="platform in imagePlatforms" :key="platform.key">→ {{ platform.name }}</b>
              </div>
            </div>
            <el-button text @click="clearSelectedImage">移除</el-button>
          </article>

          <div v-else class="asset-empty">
            <el-icon><Picture /></el-icon>
            <div>
              <strong>{{ imageRequired ? '这条路由还缺一张图片' : '需要图片时在这里上传' }}</strong>
              <span>JPG / JPEG / PNG，最大 5MB；任务只保存素材文件名，不接收任意本地路径。</span>
            </div>
            <el-button
              plain
              :icon="Upload"
              :loading="imageUploading"
              @click="openImagePicker"
            >
              {{ imageUploading ? `${imageUploadProgress}%` : '选择图片' }}
            </el-button>
          </div>
          <input
            ref="imageInput"
            class="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            @change="handleImageSelected"
          />
        </section>

        <div class="schedule-panel">
          <div>
            <strong>定时发布</strong>
            <span>先创建计划任务；是否到点自动执行由下方单独授权。</span>
          </div>
          <el-switch
            v-model="scheduleEnabled"
            aria-label="启用定时发布"
            @change="handleScheduleToggle"
          />
          <el-date-picker
            v-if="scheduleEnabled"
            v-model="publishAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择发布时间"
            :disabled-date="disablePastDate"
          />
          <div v-if="scheduleEnabled" class="schedule-auto-execute">
            <el-checkbox v-model="autoExecuteAtDue">到点自动执行</el-checkbox>
            <p>
              默认关闭。开启后，创建任务前还会要求一次明确确认；真实执行仍受服务端总开关、账号状态和平台风控保护。
            </p>
          </div>
        </div>

        <div class="dispatch-actions">
          <p>
            执行模式由服务端控制。Demo 任务会走完整状态链，但不会访问真实平台。
          </p>
          <el-button
            type="primary"
            size="large"
            :icon="Promotion"
            :loading="dispatching"
            :disabled="!canDispatch"
            @click="dispatchArticle"
          >
            {{ dispatchButtonLabel }}
          </el-button>
        </div>
      </div>

      <aside class="route-manifest" aria-label="本次分发清单">
        <div class="manifest-heading">
          <span>ROUTE / 02</span>
          <h2>渠道路由</h2>
          <p>一篇稿件，多条独立轨迹。</p>
        </div>

        <div v-if="manifestPlatforms.length" class="route-line">
          <div
            v-for="(platform, index) in manifestPlatforms"
            :key="platform.key"
            class="route-node"
          >
            <div class="node-index">{{ String(index + 1).padStart(2, '0') }}</div>
            <div class="node-copy">
              <strong>{{ platform.name }}</strong>
              <span>{{ latestPlatformState(platform.key) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="route-empty">
          <div class="empty-orbit" aria-hidden="true"><span /></div>
          <strong>选择渠道后生成路由</strong>
          <p>平台之间互不阻塞，任何失败都可以单独重试。</p>
        </div>

        <div class="manifest-summary">
          <div>
            <span>ARTICLE</span>
            <strong>{{ selectedArticle ? `A-${String(selectedArticle.id).padStart(4, '0')}` : '—' }}</strong>
          </div>
          <div>
            <span>CHANNELS</span>
            <strong>{{ selectedPlatforms.length }}</strong>
          </div>
          <div>
            <span>MODE</span>
            <strong>{{ scheduleEnabled ? (autoExecuteAtDue ? 'AUTO@TIME' : 'SCHEDULED') : 'NOW' }}</strong>
          </div>
          <div>
            <span>ASSET</span>
            <strong>{{ selectedImage ? '01 IMG' : 'NONE' }}</strong>
          </div>
        </div>

        <div class="safety-note">
          <el-icon><Lock /></el-icon>
          <p><strong>安全边界</strong>验证码、扫码、风控和页面变化只会进入“待人工确认”，系统不会绕过平台验证。</p>
        </div>
      </aside>
    </section>

    <section class="job-board" aria-labelledby="jobs-title">
      <div class="board-toolbar">
        <div class="section-heading compact">
          <span>LEDGER / 03</span>
          <div>
            <h2 id="jobs-title">发布任务</h2>
            <p>{{ jobsRangeLabel }}</p>
          </div>
        </div>
        <div class="job-filters">
          <el-select
            v-model="jobFilters.platform"
            clearable
            placeholder="全部平台"
            aria-label="按平台筛选"
            @change="applyJobFilters"
          >
            <el-option v-for="platform in platforms" :key="platform.key" :label="platform.name" :value="platform.key" />
          </el-select>
          <el-select
            v-model="jobFilters.status"
            clearable
            placeholder="全部状态"
            aria-label="按状态筛选"
            @change="applyJobFilters"
          >
            <el-option v-for="status in statusOptions" :key="status.value" :label="status.label" :value="status.value" />
          </el-select>
          <el-button :icon="Refresh" :loading="jobsRefreshing" @click="fetchJobs()">刷新</el-button>
        </div>
      </div>

      <div v-if="jobsLoading" class="jobs-loading" aria-live="polite">
        <div v-for="index in 4" :key="index" class="job-skeleton"><el-skeleton :rows="2" animated /></div>
      </div>

      <div v-else-if="jobsLoadError" class="board-state is-error" role="alert">
        <div class="state-symbol">!</div>
        <div>
          <h3>任务轨迹没有载入</h3>
          <p>检查后端服务后重新加载。已经创建的任务不会丢失。</p>
        </div>
        <el-button type="primary" @click="fetchJobs()">重新加载</el-button>
      </div>

      <div v-else-if="jobs.length === 0" class="board-state">
        <div class="state-symbol">↗</div>
        <div>
          <h3>{{ hasJobFilters ? '当前筛选下没有任务' : '第一条分发轨迹等待建立' }}</h3>
          <p>{{ hasJobFilters ? '清除筛选查看其他状态。' : '选择稿件与渠道后，任务会按平台逐条出现在这里。' }}</p>
        </div>
        <el-button v-if="hasJobFilters" @click="clearJobFilters">清除筛选</el-button>
      </div>

      <div v-else class="job-list">
        <article v-for="job in jobs" :key="job.job_id" class="job-row">
          <div :class="['status-signal', job.status]">
            <span />
            <small>{{ statusLabel(job.status) }}</small>
          </div>

          <div class="job-identity">
            <span>JOB-{{ String(job.job_id).padStart(5, '0') }}</span>
            <strong>{{ platformName(job.platform) }}</strong>
            <small v-if="job.demo" class="demo-mark">DEMO / 演示任务</small>
            <small v-if="job.images?.length" class="asset-mark">{{ job.images.length }} IMG / 已附素材</small>
            <small v-if="job.auto_execute" class="schedule-mark">AUTO@TIME / 已授权</small>
          </div>

          <div class="job-article">
            <span>{{ job.article_title || `文章 #${job.article_id}` }}</span>
            <small>{{ job.publish_at ? `计划 ${formatDate(job.publish_at, false)}` : `创建 ${formatDate(job.created_at)}` }}</small>
          </div>

          <div class="job-progress">
            <div class="progress-copy">
              <span>{{ job.message || defaultStatusMessage(job.status) }}</span>
              <b>{{ statusProgress(job.status) }}%</b>
            </div>
            <div class="progress-track" :class="job.status">
              <span :style="{ width: `${statusProgress(job.status)}%` }" />
            </div>
          </div>

          <div class="job-time">
            <span>{{ job.finished_at ? '完成时间' : job.started_at ? '开始时间' : '创建时间' }}</span>
            <time>{{ formatDate(job.finished_at || job.started_at || job.created_at) }}</time>
          </div>

          <div class="job-actions">
            <el-button
              v-if="['failed', 'need_action'].includes(job.status)"
              type="primary"
              plain
              :loading="busyJobIds.has(job.job_id)"
              @click="retryJob(job)"
            >
              {{ job.status === 'need_action' ? '确认后继续' : '重新发布' }}
            </el-button>
            <el-button
              v-else-if="job.demo && job.status === 'queued'"
              type="primary"
              :loading="busyJobIds.has(job.job_id)"
              @click="executeDemoJob(job)"
            >
              执行演示
            </el-button>
            <el-button
              v-else-if="job.can_execute_real"
              type="danger"
              plain
              :loading="busyJobIds.has(job.job_id)"
              @click="executeRealJob(job)"
            >
              确认真实发布
            </el-button>
            <el-button v-if="job.url" text :icon="Link" @click="openResult(job.url)">查看结果</el-button>
            <span v-if="!job.url && !['failed', 'need_action', 'queued'].includes(job.status)" class="no-action">无需操作</span>
          </div>
        </article>
      </div>

      <footer v-if="jobPagination.total_pages > 1" class="board-pagination">
        <span>PAGE {{ String(jobPagination.page).padStart(2, '0') }} / {{ String(jobPagination.total_pages).padStart(2, '0') }}</span>
        <el-pagination
          v-model:current-page="jobPagination.page"
          background
          layout="prev, pager, next"
          :page-size="jobPagination.page_size"
          :total="jobPagination.total"
          @current-change="fetchJobs"
        />
      </footer>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Lock, Picture, Promotion, Refresh, Upload } from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { materialApi } from '@/api/material'
import { publishApi } from '@/api/publish'

const route = useRoute()
const router = useRouter()

const platforms = [
  { key: 'zhihu', code: 'ZH', name: '知乎', note: 'P0 · 文章' },
  { key: 'toutiao', code: 'TT', name: '今日头条', note: 'P0 · 图文' },
  { key: 'baijiahao', code: 'BJ', name: '百家号', note: 'P1 · 图文' },
  { key: 'sohu', code: 'SH', name: '搜狐号', note: 'P1 · 图文' },
  { key: 'xiaohongshu', code: 'XH', name: '小红书', note: 'P1 · 笔记' }
]

const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'processing', label: '发布中' },
  { value: 'success', label: '发布成功' },
  { value: 'failed', label: '发布失败' },
  { value: 'need_action', label: '待人工确认' },
  { value: 'scheduled', label: '计划中' }
]

const articles = ref([])
const articlesLoading = ref(false)
const articlesLoadError = ref(false)
const selectedArticleId = ref(null)
const selectedPlatforms = ref(['zhihu'])
const scheduleEnabled = ref(false)
const publishAt = ref('')
const autoExecuteAtDue = ref(false)
const dispatching = ref(false)
const imageInput = ref(null)
const selectedImage = ref(null)
const imageUploading = ref(false)
const imageUploadProgress = ref(0)

const jobs = ref([])
const jobsLoading = ref(false)
const jobsRefreshing = ref(false)
const jobsLoadError = ref(false)
const lastSyncedAt = ref(null)
const busyJobIds = ref(new Set())
const jobFilters = reactive({ platform: '', status: '' })
const jobPagination = reactive({ page: 1, page_size: 20, total: 0, total_pages: 0 })
let pollTimer = null
let pollingStopped = false

const selectedArticle = computed(() => (
  articles.value.find(article => article.id === selectedArticleId.value) || null
))

const manifestPlatforms = computed(() => (
  platforms.filter(platform => selectedPlatforms.value.includes(platform.key))
))

const imagePlatforms = computed(() => (
  manifestPlatforms.value.filter(platform => ['baijiahao', 'xiaohongshu'].includes(platform.key))
))

const imageRequired = computed(() => imagePlatforms.value.length > 0)

const canDispatch = computed(() => Boolean(
  selectedArticleId.value
  && selectedPlatforms.value.length
  && (!imageRequired.value || selectedImage.value)
  && (!scheduleEnabled.value || publishAt.value)
  && !imageUploading.value
  && !dispatching.value
))

const dispatchButtonLabel = computed(() => {
  if (!selectedArticleId.value) return '先选择就绪稿件'
  if (!selectedPlatforms.value.length) return '至少选择一个渠道'
  if (imageUploading.value) return '图片上传中'
  if (imageRequired.value && !selectedImage.value) return '先补齐渠道图片'
  if (scheduleEnabled.value && !publishAt.value) return '选择计划发布时间'
  if (scheduleEnabled.value && autoExecuteAtDue.value) {
    return `创建 ${selectedPlatforms.value.length} 个自动计划任务`
  }
  return scheduleEnabled.value
    ? `创建 ${selectedPlatforms.value.length} 个计划任务`
    : `分发到 ${selectedPlatforms.value.length} 个渠道`
})

const activeJobCount = computed(() => (
  jobs.value.filter(job => ['queued', 'processing'].includes(job.status)).length
))

const hasJobFilters = computed(() => Boolean(jobFilters.platform || jobFilters.status))

const lastSyncedLabel = computed(() => {
  if (!lastSyncedAt.value) return '等待首次同步'
  return `最近同步 ${lastSyncedAt.value.toLocaleTimeString('zh-CN', { hour12: false })}`
})

const jobsRangeLabel = computed(() => {
  if (!jobPagination.total) return '还没有符合条件的发布任务'
  const start = (jobPagination.page - 1) * jobPagination.page_size + 1
  const end = Math.min(jobPagination.page * jobPagination.page_size, jobPagination.total)
  return `显示第 ${start}–${end} 条，共 ${jobPagination.total} 条任务`
})

const fetchArticles = async () => {
  articlesLoading.value = true
  articlesLoadError.value = false
  try {
    const response = await articleApi.getArticles({ status: 'ready', page: 1, page_size: 100 })
    articles.value = response.data?.items || []
    const queryId = Number(route.query.article_id)
    if (!selectedArticleId.value && Number.isInteger(queryId) && articles.value.some(item => item.id === queryId)) {
      selectedArticleId.value = queryId
    }
  } catch (error) {
    articlesLoadError.value = true
    console.error('加载就绪稿件失败:', error)
  } finally {
    articlesLoading.value = false
  }
}

const fetchJobs = async (pageOrSilent = false) => {
  const silent = pageOrSilent === true
  if (typeof pageOrSilent === 'number') jobPagination.page = pageOrSilent
  if (silent) jobsRefreshing.value = false
  else if (jobs.value.length) jobsRefreshing.value = true
  else jobsLoading.value = true
  jobsLoadError.value = false

  const params = {
    page: jobPagination.page,
    page_size: jobPagination.page_size
  }
  if (jobFilters.platform) params.platform = jobFilters.platform
  if (jobFilters.status) params.status = jobFilters.status

  try {
    const response = await publishApi.getJobs(params)
    jobs.value = response.data?.items || []
    Object.assign(jobPagination, response.data?.pagination || {})
    lastSyncedAt.value = new Date()
  } catch (error) {
    if (!silent) jobsLoadError.value = true
    console.error('加载发布任务失败:', error)
  } finally {
    jobsLoading.value = false
    jobsRefreshing.value = false
  }
}

const togglePlatform = (key) => {
  selectedPlatforms.value = selectedPlatforms.value.includes(key)
    ? selectedPlatforms.value.filter(item => item !== key)
    : [...selectedPlatforms.value, key]
}

const handleScheduleToggle = (enabled) => {
  if (!enabled) {
    publishAt.value = ''
    autoExecuteAtDue.value = false
  }
}

const openImagePicker = () => {
  if (!imageUploading.value) imageInput.value?.click()
}

const handleImageSelected = async (event) => {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const isSupportedImage = ['image/jpeg', 'image/png'].includes(file.type)
    || /\.(jpe?g|png)$/i.test(file.name || '')
  if (!isSupportedImage) {
    ElMessage.error('图片仅支持 JPG、JPEG、PNG 格式')
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.error('图片不能超过 5MB')
    return
  }

  imageUploading.value = true
  imageUploadProgress.value = 0
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await materialApi.uploadMaterial(formData, progressEvent => {
      if (progressEvent.total) {
        imageUploadProgress.value = Math.round(
          (progressEvent.loaded * 100) / progressEvent.total
        )
      }
    })
    const filename = String(response.data?.filepath || '').trim()
    if (!filename) throw new Error('上传接口没有返回素材文件名')
    selectedImage.value = {
      filename,
      name: file.name,
      previewUrl: materialApi.getMaterialPreviewUrl(filename)
    }
    imageUploadProgress.value = 100
    ElMessage.success('图片已进入素材库，将随所选渠道任务保存')
  } catch (error) {
    console.error('上传分发图片失败:', error)
  } finally {
    imageUploading.value = false
  }
}

const clearSelectedImage = () => {
  selectedImage.value = null
  imageUploadProgress.value = 0
}

const dispatchArticle = async () => {
  if (!canDispatch.value) return
  let confirmedAutoExecute = false
  if (scheduleEnabled.value && autoExecuteAtDue.value) {
    try {
      await ElMessageBox.confirm(
        `这将授权系统在 ${publishAt.value} 到达后，自动尝试把《${selectedArticle.value?.title || ''}》发布到 ${selectedPlatforms.value.length} 个所选渠道。任务不会提前执行，失败或状态不明时不会自动重试。是否确认？`,
        '确认定时自动执行',
        {
          confirmButtonText: '确认到点自动执行',
          cancelButtonText: '只创建普通计划',
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
      confirmedAutoExecute = true
    } catch (action) {
      if (action !== 'cancel') return
    }
  }
  dispatching.value = true
  let createdCount = 0
  let failedCount = 0
  let demoCount = 0

  for (const platform of selectedPlatforms.value) {
    try {
      const response = await publishApi.createJob({
        article_id: selectedArticleId.value,
        platform,
        images: selectedImage.value ? [selectedImage.value.filename] : [],
        publish_at: scheduleEnabled.value ? publishAt.value : null,
        auto_execute: confirmedAutoExecute
      })
      let job = response.data
      createdCount += 1
      if (job.demo) demoCount += 1
      if (job.demo && job.status === 'queued') {
        const executed = await publishApi.executeDemoJob(job.job_id)
        job = executed.data
      }
    } catch (error) {
      failedCount += 1
      console.error(`创建 ${platform} 发布任务失败:`, error)
    }
  }

  await fetchJobs(true)
  dispatching.value = false
  if (createdCount && !failedCount) {
    ElMessage.success(
      demoCount
        ? `${createdCount} 个演示任务已完成，未访问真实平台`
        : `${createdCount} 个发布任务已创建`
    )
  } else if (createdCount) {
    ElMessage.warning(`已创建 ${createdCount} 个任务，${failedCount} 个渠道创建失败`)
  }
}

const executeDemoJob = async (job) => {
  markJobBusy(job.job_id, true)
  try {
    await publishApi.executeDemoJob(job.job_id)
    ElMessage.success('演示任务已完成，未访问真实平台')
    await fetchJobs(true)
  } catch (error) {
    console.error('执行演示任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const executeRealJob = async (job) => {
  try {
    await ElMessageBox.confirm(
      `将使用已连接的${platformName(job.platform)}账号真实发布《${job.article_title || `文章 #${job.article_id}`}》。平台可能要求扫码、验证码或人工确认，是否继续？`,
      '确认真实发布',
      {
        confirmButtonText: '确认并执行',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true
      }
    )
  } catch {
    return
  }

  markJobBusy(job.job_id, true)
  try {
    const response = await publishApi.executeRealJob(job.job_id)
    const result = response.data
    if (result.status === 'success') ElMessage.success(`${platformName(job.platform)}真实发布成功`)
    else if (result.status === 'need_action') ElMessage.warning(result.message || '需要人工确认后继续')
    else if (result.status === 'processing') ElMessage.info(result.message || '平台状态仍在确认中')
    else ElMessage.error(result.message || '真实发布未完成')
    await fetchJobs(true)
  } catch (error) {
    console.error('执行真实发布任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const retryJob = async (job) => {
  markJobBusy(job.job_id, true)
  try {
    const response = await publishApi.retryJob(job.job_id)
    const retried = response.data
    if (retried.demo) {
      await publishApi.executeDemoJob(retried.job_id)
      ElMessage.success('演示任务已重新执行')
    } else {
      ElMessage.success('任务已重新排队')
    }
    await fetchJobs(true)
  } catch (error) {
    console.error('重试发布任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const markJobBusy = (jobId, busy) => {
  const next = new Set(busyJobIds.value)
  if (busy) next.add(jobId)
  else next.delete(jobId)
  busyJobIds.value = next
}

const applyJobFilters = () => {
  jobPagination.page = 1
  fetchJobs()
}

const clearJobFilters = () => {
  jobFilters.platform = ''
  jobFilters.status = ''
  applyJobFilters()
}

const platformName = (key) => platforms.find(platform => platform.key === key)?.name || key
const statusLabel = (status) => statusOptions.find(item => item.value === status)?.label || status

const latestPlatformState = (platformKey) => {
  const job = jobs.value.find(item => item.platform === platformKey)
  return job ? statusLabel(job.status) : '等待创建任务'
}

const statusProgress = (status) => ({
  scheduled: 10,
  queued: 18,
  processing: 72,
  need_action: 72,
  success: 100,
  failed: 100
}[status] || 0)

const defaultStatusMessage = (status) => ({
  scheduled: '等待计划时间',
  queued: '等待执行器领取',
  processing: '平台正在处理',
  need_action: '需要人工确认后继续',
  success: '发布流程已完成',
  failed: '发布流程失败'
}[status] || '状态待确认')

const scoreTone = (score) => {
  if (score >= 80) return 'strong'
  if (score >= 60) return 'medium'
  return 'weak'
}

const disablePastDate = (date) => date.getTime() < Date.now() - 24 * 60 * 60 * 1000

const formatDate = (value, assumeUtc = true) => {
  if (!value) return '—'
  const text = String(value)
  const normalized = assumeUtc && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(' ', 'T')}Z`
    : text.includes(' ')
      ? text.replace(' ', 'T')
      : text
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date)
}

const openResult = (url) => {
  if (/^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer')
}

const scheduleJobPoll = () => {
  if (pollingStopped) return
  pollTimer = window.setTimeout(async () => {
    pollTimer = null
    await fetchJobs(true)
    scheduleJobPoll()
  }, 5000)
}

onMounted(async () => {
  pollingStopped = false
  await Promise.allSettled([fetchArticles(), fetchJobs()])
  scheduleJobPoll()
})

onBeforeUnmount(() => {
  pollingStopped = true
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
})
</script>

<style lang="scss" scoped>
.distribution-center {
  --harbor: #102b31;
  --harbor-deep: #0a2227;
  --signal: #39b8b2;
  --signal-soft: #d9f1ee;
  --paper: #f4f7f5;
  --ink: #173038;
  --muted: #6d7d82;
  --rule: #d7e0dd;
  --amber: #c9852d;
  --danger: #c15454;
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

.page-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  margin-bottom: 22px;
  padding: 8px 4px 18px;
  border-bottom: 1px solid #cad6d2;
}

.heading-copy {
  max-width: 720px;

  .eyebrow {
    margin: 0 0 9px;
    color: #318f8b;
    font: 700 11px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.14em;
  }

  h1 {
    margin: 0;
    color: var(--harbor);
    font: 700 clamp(34px, 4vw, 50px)/0.98 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif;
    letter-spacing: -0.035em;
  }

  > p:last-child {
    margin: 13px 0 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.7;
  }
}

.live-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 218px;
  padding: 12px 14px;
  border: 1px solid var(--rule);
  background: rgba(255, 255, 255, 0.7);

  .pulse {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #8fa19f;
    box-shadow: 0 0 0 5px rgba(143, 161, 159, 0.12);
  }

  &.active .pulse {
    background: var(--signal);
    box-shadow: 0 0 0 5px rgba(57, 184, 178, 0.15);
    animation: status-pulse 1.8s ease-in-out infinite;
  }

  div {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  strong { font-size: 13px; }
  small { color: var(--muted); font-size: 11px; }
}

.dispatch-console {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.75fr);
  background: #fff;
  border: 1px solid var(--rule);
  box-shadow: 0 16px 45px rgba(20, 50, 57, 0.07);
}

.dispatch-form { padding: clamp(22px, 3vw, 38px); }

.section-heading {
  display: flex;
  gap: 18px;
  margin-bottom: 28px;

  > span {
    padding-top: 6px;
    color: #3c9692;
    font: 700 10px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
    white-space: nowrap;
  }

  h2 {
    margin: 0;
    color: var(--harbor);
    font: 700 25px/1.1 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif;
  }

  p {
    margin: 7px 0 0;
    color: var(--muted);
    font-size: 13px;
  }

  &.compact { margin-bottom: 0; }
}

.field-block {
  margin-bottom: 18px;

  > label,
  legend {
    display: block;
    margin-bottom: 9px;
    color: #345159;
    font-size: 13px;
    font-weight: 700;
  }

  :deep(.el-select) { width: 100%; }
  :deep(.el-select__wrapper) { min-height: 44px; border-radius: 3px; }
}

.article-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;

  small { color: #819095; }
}

.field-error,
.field-guidance {
  margin: 8px 0 0;
  color: var(--danger);
  font-size: 12px;

  button {
    padding: 0;
    border: 0;
    background: none;
    color: #267f7b;
    cursor: pointer;
    text-decoration: underline;
  }
}

.field-guidance { color: var(--muted); }

.article-proof {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 16px;
  align-items: center;
  margin-bottom: 26px;
  padding: 15px 16px;
  border-left: 3px solid var(--signal);
  background: var(--paper);
}

.proof-id {
  color: #43827f;
  font: 700 11px/1.2 "Cascadia Mono", monospace;
}

.proof-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  > span { color: var(--muted); font-size: 11px; }
  > strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
}

.proof-tags {
  display: flex;
  gap: 8px;
  overflow: hidden;

  i { color: #4b7777; font-size: 11px; font-style: normal; white-space: nowrap; }
}

.proof-score {
  display: grid;
  place-items: center;
  min-width: 54px;
  padding: 8px;
  border: 1px solid #bdcbc8;

  b { font: 700 20px/1 "Cascadia Mono", monospace; }
  small { margin-top: 3px; color: var(--muted); font-size: 8px; letter-spacing: 0.12em; }
  &.strong { color: #257b69; }
  &.medium { color: var(--amber); }
  &.weak { color: var(--danger); }
}

.platform-fieldset {
  margin: 0;
  padding: 0;
  border: 0;

  legend { padding: 0; color: #345159; font-size: 13px; font-weight: 700; }
  > p { margin: -3px 0 12px; color: var(--muted); font-size: 12px; }
}

.platform-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.platform-option {
  position: relative;
  min-height: 102px;
  padding: 13px 11px;
  border: 1px solid var(--rule);
  border-radius: 3px;
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  text-align: left;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;

  &:hover { transform: translateY(-2px); border-color: #81bebb; }
  &:focus-visible { outline: 3px solid rgba(57, 184, 178, 0.3); outline-offset: 2px; }
  &.selected { border-color: var(--signal); background: #eff9f7; box-shadow: inset 0 -3px var(--signal); }

  .platform-code {
    display: block;
    color: #48827f;
    font: 700 9px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  .platform-name { display: block; margin-top: 11px; font-size: 15px; font-weight: 700; }
  small { display: block; margin-top: 5px; color: var(--muted); font-size: 10px; }
  .selection-mark { position: absolute; top: 10px; right: 10px; color: #338e89; font-size: 14px; }
}

.asset-panel {
  margin-top: 18px;
  border: 1px solid var(--rule);
  background: #fbfdfc;

  &.required { border-color: #8bbab7; }
  &.missing { border-left: 3px solid var(--amber); }
}

.asset-heading {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 13px;
  align-items: start;
  padding: 13px 15px;
  border-bottom: 1px solid var(--rule);

  .asset-code {
    padding-top: 3px;
    color: #3b8e8a;
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.1em;
    white-space: nowrap;
  }

  > div { display: flex; flex-direction: column; gap: 4px; }
  strong { color: #29474f; font-size: 13px; }
  small { color: var(--muted); font-size: 10px; line-height: 1.5; }
  i {
    padding: 4px 6px;
    background: var(--signal-soft);
    color: #277b77;
    font: 700 9px/1 "Cascadia Mono", monospace;
    font-style: normal;
    white-space: nowrap;
  }
}

.asset-empty,
.asset-record {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 14px;
  align-items: center;
  min-height: 76px;
  padding: 13px 15px;
}

.asset-empty {
  > .el-icon {
    width: 40px;
    height: 40px;
    border: 1px dashed #8bbab7;
    color: #358d88;
    font-size: 18px;
  }

  > div { display: flex; flex-direction: column; gap: 4px; }
  strong { font-size: 12px; }
  span { color: var(--muted); font-size: 10px; line-height: 1.5; }
  :deep(.el-button) { border-radius: 3px; color: #267f7b; border-color: #7fb7b3; }
}

.asset-record {
  img {
    width: 70px;
    height: 54px;
    object-fit: cover;
    border: 1px solid #bfd0cd;
    background: var(--paper);
  }

  :deep(.el-button) { color: var(--danger); }
}

.asset-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  > span { color: #2e8984; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.07em; }
  > strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  > small { overflow: hidden; color: var(--muted); font: 9px/1.3 "Cascadia Mono", monospace; text-overflow: ellipsis; white-space: nowrap; }
}

.asset-routes {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 2px;

  b { padding: 3px 5px; background: #edf6f4; color: #437674; font-size: 9px; font-weight: 600; }
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.schedule-panel {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 12px;
  margin-top: 24px;
  padding: 14px 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);

  > div:first-child { display: flex; flex-direction: column; gap: 3px; }
  strong { font-size: 13px; }
  span { color: var(--muted); font-size: 11px; }
  :deep(.el-date-editor) { grid-column: 1 / -1; width: 100%; }
}

.schedule-auto-execute {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  align-items: start;
  padding: 11px 12px;
  border-left: 3px solid var(--amber);
  background: #fbf6ee;

  :deep(.el-checkbox) { grid-row: 1 / 3; margin-right: 0; }
  :deep(.el-checkbox__label) { color: #4c4b3f; font-size: 12px; font-weight: 700; }
  p { margin: 0; color: #7d7768; font-size: 10px; line-height: 1.55; }
}

.dispatch-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 20px;

  p { max-width: 440px; margin: 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
  :deep(.el-button) { min-width: 210px; border-radius: 3px; background: var(--harbor); border-color: var(--harbor); }
  :deep(.el-button:hover) { background: #17434a; border-color: #17434a; }
}

.route-manifest {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: clamp(22px, 3vw, 34px);
  background: var(--harbor);
  color: #eef8f6;
}

.manifest-heading {
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);

  > span { color: #78d7d0; font: 700 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 10px 0 0; font: 700 25px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { margin: 7px 0 0; color: rgba(222, 240, 237, 0.65); font-size: 12px; }
}

.route-line {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 24px 0;

  &::before {
    content: '';
    position: absolute;
    top: 16px;
    bottom: 16px;
    left: 15px;
    width: 1px;
    background: linear-gradient(var(--signal), rgba(57, 184, 178, 0.15));
  }
}

.route-node {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 31px 1fr;
  gap: 13px;
  align-items: center;
  padding: 9px 0;
}

.node-index {
  width: 31px;
  height: 31px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(100, 214, 207, 0.75);
  border-radius: 50%;
  background: var(--harbor);
  color: #8ce3dc;
  font: 700 9px/1 "Cascadia Mono", monospace;
}

.node-copy {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;

  strong { font-size: 13px; }
  span { color: rgba(222, 240, 237, 0.58); font-size: 10px; }
}

.route-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  text-align: center;

  strong { margin-top: 17px; font-size: 14px; }
  p { max-width: 220px; margin: 7px 0 0; color: rgba(222, 240, 237, 0.58); font-size: 11px; line-height: 1.6; }
}

.empty-orbit {
  width: 68px;
  height: 68px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(100, 214, 207, 0.38);
  border-radius: 50%;
  transform: rotate(-20deg) scaleY(0.62);

  span { width: 11px; height: 11px; border-radius: 50%; background: var(--signal); transform: scaleY(1.6); }
}

.manifest-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin-top: auto;
  border-top: 1px solid rgba(255, 255, 255, 0.13);
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);

  div { padding: 13px 6px; border-right: 1px solid rgba(255, 255, 255, 0.13); }
  div:last-child { border-right: 0; }
  span { display: block; color: rgba(222, 240, 237, 0.48); font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
  strong { display: block; margin-top: 7px; color: #e8f7f4; font: 700 12px/1 "Cascadia Mono", monospace; }
}

.safety-note {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  color: rgba(222, 240, 237, 0.62);

  .el-icon { flex: 0 0 auto; margin-top: 2px; color: #76d6cf; }
  p { margin: 0; font-size: 10px; line-height: 1.6; }
  strong { color: #dff5f2; }
}

.job-board {
  margin-top: 22px;
  background: #fff;
  border: 1px solid var(--rule);
}

.board-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 26px;
  border-bottom: 1px solid var(--rule);
}

.job-filters {
  display: flex;
  gap: 8px;

  :deep(.el-select) { width: 146px; }
  :deep(.el-select__wrapper),
  :deep(.el-button) { border-radius: 3px; }
}

.jobs-loading { padding: 0 26px; }
.job-skeleton { padding: 22px 0; border-bottom: 1px solid #e5ebe9; }

.job-list { padding: 0 26px; }

.job-row {
  display: grid;
  grid-template-columns: 88px 142px minmax(160px, 1.15fr) minmax(190px, 1.4fr) 118px minmax(110px, auto);
  gap: 16px;
  align-items: center;
  min-height: 98px;
  border-bottom: 1px solid #e5ebe9;
}

.status-signal {
  display: flex;
  flex-direction: column;
  gap: 7px;
  align-items: flex-start;

  > span { width: 28px; height: 3px; background: #92a19f; }
  small { color: var(--muted); font-size: 10px; }
  &.success > span { background: var(--signal); }
  &.processing > span, &.queued > span { background: #4a9ed0; }
  &.need_action > span, &.scheduled > span { background: var(--amber); }
  &.failed > span { background: var(--danger); }
}

.job-identity,
.job-article,
.job-time {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.job-identity {
  > span { color: #47827f; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  > strong { font-size: 14px; }
}

.demo-mark {
  align-self: flex-start;
  padding: 3px 5px;
  background: var(--signal-soft);
  color: #267d78 !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.asset-mark {
  align-self: flex-start;
  color: #3c827e !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.schedule-mark {
  align-self: flex-start;
  color: #a36724 !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.job-article {
  span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; }
  small { color: var(--muted); font-size: 10px; }
}

.progress-copy {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;

  span { overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  b { color: #547077; font: 700 10px/1 "Cascadia Mono", monospace; }
}

.progress-track {
  height: 4px;
  overflow: hidden;
  background: #e6ecea;

  > span { display: block; height: 100%; background: #5f9fbd; transition: width 260ms ease; }
  &.success > span { background: var(--signal); }
  &.failed > span { background: var(--danger); }
  &.need_action > span, &.scheduled > span { background: var(--amber); }
}

.job-time {
  span { color: var(--muted); font-size: 9px; }
  time { font: 600 10px/1.35 "Cascadia Mono", monospace; }
}

.job-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;

  :deep(.el-button) { border-radius: 3px; }
  .no-action { color: #9aa8a6; font-size: 10px; }
}

.board-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  min-height: 220px;
  padding: 30px;

  .state-symbol {
    width: 50px;
    height: 50px;
    display: grid;
    place-items: center;
    border: 1px solid #9db9b5;
    color: #348d88;
    font: 700 22px/1 "Cascadia Mono", monospace;
  }

  h3 { margin: 0; font-size: 16px; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 12px; }
  &.is-error .state-symbol { border-color: #d49a9a; color: var(--danger); }
}

.board-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 26px;

  > span { color: var(--muted); font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
}

@keyframes status-pulse {
  50% { box-shadow: 0 0 0 8px rgba(57, 184, 178, 0.05); }
}

@media (prefers-reduced-motion: reduce) {
  .live-indicator.active .pulse { animation: none; }
  .platform-option, .progress-track > span { transition: none; }
}

@media (max-width: 1180px) {
  .platform-grid { grid-template-columns: repeat(3, 1fr); }
  .job-row { grid-template-columns: 78px 126px minmax(160px, 1fr) minmax(180px, 1fr); padding: 15px 0; }
  .job-time { grid-column: 3; }
  .job-actions { grid-column: 4; grid-row: 2; }
}

@media (max-width: 900px) {
  .dispatch-console { grid-template-columns: 1fr; }
  .route-manifest { min-height: 420px; }
  .page-heading, .board-toolbar { align-items: flex-start; flex-direction: column; }
  .job-filters { width: 100%; flex-wrap: wrap; }
  .job-filters :deep(.el-select) { flex: 1; min-width: 150px; }
}

@media (max-width: 680px) {
  .page-heading { gap: 18px; }
  .live-indicator { width: 100%; box-sizing: border-box; }
  .dispatch-form, .route-manifest { padding: 20px; }
  .platform-grid { grid-template-columns: repeat(2, 1fr); }
  .asset-heading { grid-template-columns: 1fr auto; }
  .asset-heading .asset-code { grid-column: 1 / -1; }
  .asset-empty, .asset-record { grid-template-columns: auto 1fr; }
  .asset-empty :deep(.el-button), .asset-record :deep(.el-button) { grid-column: 1 / -1; width: 100%; }
  .dispatch-actions { align-items: stretch; flex-direction: column; }
  .dispatch-actions :deep(.el-button) { width: 100%; }
  .manifest-summary { grid-template-columns: 1fr; }
  .manifest-summary div { border-right: 0; border-bottom: 1px solid rgba(255, 255, 255, 0.13); }
  .manifest-summary div:last-child { border-bottom: 0; }
  .board-toolbar, .job-list { padding-left: 16px; padding-right: 16px; }
  .job-row { grid-template-columns: 76px 1fr; gap: 12px; padding: 18px 0; }
  .job-article, .job-progress, .job-time, .job-actions { grid-column: 1 / -1; }
  .job-actions { justify-content: flex-start; grid-row: auto; }
  .board-state { align-items: flex-start; flex-direction: column; }
}
</style>
