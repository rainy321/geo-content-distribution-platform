<template>
  <main class="geo-automation" :aria-busy="processing">
    <header class="automation-header">
      <div>
        <p class="eyebrow">GEO AUTOPILOT / DURABLE SAGA</p>
        <h1>一键 GEO</h1>
        <p>配置一次、确认一次，系统按顺序完成写稿、优化、预检与已授权发布；单篇异常会暂存，后续文章继续。</p>
      </div>
      <button v-if="currentRun" type="button" class="run-id" :disabled="processing || starting" @click="refreshCurrentRun">
        RUN {{ currentRun.id }} · {{ automationStatusLabel(currentRun.status) }}
      </button>
    </header>

    <section v-if="!currentRun || isAutomationTerminal(currentRun.status)" class="setup-grid">
      <div class="setup-form">
        <div class="section-heading">
          <span>01 / MANIFEST</span>
          <h2>锁定品牌与优化方向</h2>
          <p>启动后会保存独立快照；修改这里不会改变已运行批次。</p>
        </div>

        <el-form label-position="top" @submit.prevent>
          <div class="form-pair">
            <el-form-item label="品牌项目">
              <el-select v-model="form.project_id" filterable placeholder="选择品牌项目" @change="applyProjectDefaults">
                <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="运行名称">
              <el-input v-model="form.name" maxlength="100" placeholder="例如：9 月产品认知覆盖" />
            </el-form-item>
          </div>

          <el-form-item label="本轮 GEO 优化方向">
            <el-input
              v-model="form.optimization_direction"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="例如：围绕企业 AI Agent 的落地成本、适用边界和可核验案例，优先回答采购与技术负责人会直接提问的问题。"
            />
          </el-form-item>

          <el-form-item label="文章主题 · 每行一个">
            <el-input
              v-model="form.topics_text"
              type="textarea"
              :rows="6"
              resize="vertical"
              placeholder="企业如何评估 AI Agent 的落地成本&#10;AI Agent 项目从试点到生产的关键门槛"
            />
            <div :class="['topic-counter', { invalid: topics.length > GEO_AUTOMATION_ITEM_LIMIT }]">
              <span>已识别 {{ topics.length }} 个不重复主题</span>
              <strong>{{ topics.length }} / {{ GEO_AUTOMATION_ITEM_LIMIT }}</strong>
            </div>
          </el-form-item>

          <div class="form-pair three">
            <el-form-item label="内容类型">
              <el-select v-model="form.content_type">
                <el-option v-for="item in contentTypes" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标字数">
              <el-input-number v-model="form.target_length" :min="200" :max="3000" :step="100" controls-position="right" />
            </el-form-item>
            <el-form-item label="写作目标平台">
              <el-select v-model="form.target_platform" clearable placeholder="跟随分发渠道">
                <el-option v-for="platform in selectablePlatforms" :key="platform.key" :label="platform.name" :value="platform.key" />
              </el-select>
            </el-form-item>
          </div>

          <el-form-item label="品牌关键词">
            <el-select v-model="form.keywords" multiple filterable allow-create default-first-option placeholder="至少一个关键词">
              <el-option v-for="keyword in keywordOptions" :key="keyword" :label="keyword" :value="keyword" />
            </el-select>
          </el-form-item>
        </el-form>

        <div class="section-heading second">
          <span>02 / ROUTING</span>
          <h2>选择准备或发布边界</h2>
        </div>

        <div class="mode-switch" role="radiogroup" aria-label="执行模式">
          <button
            v-for="mode in publishModes"
            :key="mode.value"
            type="button"
            :class="{ active: form.publish_mode === mode.value }"
            :disabled="mode.disabled"
            :aria-pressed="form.publish_mode === mode.value"
            @click="setPublishMode(mode.value)"
          >
            <strong>{{ mode.label }}</strong>
            <small>{{ mode.description }}</small>
          </button>
        </div>

        <fieldset class="channel-fieldset">
          <legend>发布渠道</legend>
          <div class="channel-grid">
            <label
              v-for="platform in selectablePlatforms"
              :key="platform.key"
              :class="['channel-option', { selected: form.platforms.includes(platform.key), disabled: !platformSelectable(platform) }]"
            >
              <input
                v-model="form.platforms"
                type="checkbox"
                :value="platform.key"
                :disabled="!platformSelectable(platform)"
              >
              <span>
                <strong>{{ platform.name }}</strong>
                <small>{{ platformChannelNote(platform) }}</small>
              </span>
            </label>
          </div>
        </fieldset>

        <div v-if="form.publish_mode === 'real' && form.platforms.length" class="account-bindings">
          <label v-for="platformKey in form.platforms" :key="platformKey">
            <span>{{ platformName(platformKey) }}发布账号</span>
            <el-select v-model="form.account_ids[platformKey]" placeholder="必须绑定明确账号">
              <el-option
                v-for="account in connectedAccounts(platformKey)"
                :key="account.id"
                :label="account.account_name || `账号 ${account.id}`"
                :value="account.id"
              />
            </el-select>
          </label>
        </div>
      </div>

      <aside class="authorization-panel">
        <div class="section-heading">
          <span>03 / AUTHORIZATION</span>
          <h2>一次确认的执行范围</h2>
        </div>
        <dl>
          <div><dt>品牌</dt><dd>{{ selectedProject?.name || '尚未选择' }}</dd></div>
          <div><dt>文章</dt><dd>{{ topics.length }} 篇，严格串行</dd></div>
          <div><dt>模型调用</dt><dd>至少 {{ topics.length * 2 }} 次</dd></div>
          <div><dt>渠道</dt><dd>{{ form.platforms.length }} 个</dd></div>
          <div><dt>发布任务上限</dt><dd>{{ topics.length * form.platforms.length }} 项</dd></div>
          <div><dt>运行模式</dt><dd>{{ currentPublishMode.label }}</dd></div>
        </dl>

        <div class="safety-contract">
          <strong>固定执行规则</strong>
          <ul>
            <li>一篇文章或一个渠道报错后，保存证据并继续下一项。</li>
            <li>模型或平台结果未知时不自动重试，避免重复计费或重复发布。</li>
            <li>真实任务绑定文章、渠道、账号、状态版本与确认指纹。</li>
            <li>“仅准备”不会创建任务，也不会访问任何媒体平台。</li>
          </ul>
        </div>

        <p v-if="startBlocker" class="start-blocker" role="status">{{ startBlocker }}</p>
        <el-button class="start-button" type="primary" size="large" :loading="starting" :disabled="Boolean(startBlocker)" @click="confirmAndStart">
          核对并启动一键 GEO
        </el-button>
      </aside>
    </section>

    <section v-if="currentRun" class="run-console" aria-live="polite">
      <header class="run-console-header">
        <div>
          <span>RUN {{ String(currentRun.id).padStart(4, '0') }} / {{ String(currentRun.config?.schema_version || '').toUpperCase() }}</span>
          <h2>{{ currentRun.config?.name || '一键 GEO 运行' }}</h2>
          <p>{{ currentRun.message }}</p>
        </div>
        <div class="run-tally">
          <strong>{{ runCounts.completed }} 完成</strong>
          <span>{{ runCounts.issues }} 需处理</span>
          <em>{{ runCounts.pending }} 待执行</em>
        </div>
        <div class="run-actions">
          <el-button v-if="processing" plain @click="requestPause">在检查点暂停</el-button>
          <el-button v-else-if="currentRun.status === 'paused' || hasInterruptedItem" type="primary" @click="resumeRun">继续安全步骤</el-button>
          <el-button v-else-if="!isAutomationTerminal(currentRun.status)" type="primary" @click="resumeRun">继续执行</el-button>
          <el-button plain :disabled="processing || starting" @click="refreshCurrentRun">刷新</el-button>
          <el-button v-if="!processing && isAutomationTerminal(currentRun.status)" plain @click="newRun">新建一轮</el-button>
        </div>
      </header>

      <div class="run-progress" aria-hidden="true">
        <span :style="{ width: `${currentRun.progress || 0}%` }" />
      </div>

      <GeoAutomationRail
        :run="currentRun"
        :locked="!isAutomationTerminal(currentRun.status)"
        @open-article="openArticle"
        @open-publish="openPublishJob"
      />
    </section>

    <section v-if="recentRuns.length" class="recent-runs">
      <header>
        <div>
          <span>HISTORY</span>
          <h2>最近运行</h2>
        </div>
      </header>
      <button v-for="run in recentRuns" :key="run.id" type="button" :disabled="starting || processing" @click="openRun(run.id)">
        <code>RUN {{ run.id }}</code>
        <strong>{{ run.config?.name || run.project_snapshot?.name }}</strong>
        <span>{{ automationStatusLabel(run.status) }} · {{ run.completed_items }}/{{ run.total_items }}</span>
      </button>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import GeoAutomationRail from '@/components/GeoAutomationRail.vue'
import { automationApi } from '@/api/automation'
import { accountApi } from '@/api/account'
import { articleApi } from '@/api/article'
import { projectApi } from '@/api/project'
import { publishApi } from '@/api/publish'
import {
  GEO_AUTOMATION_ITEM_LIMIT,
  TERMINAL_AUTOMATION_STATUSES,
  automationErrorEvidence,
  automationRunCounts,
  automationStatusLabel,
  buildAutomationAuthorization,
  isAutomationTerminal,
  makeAutomationIdentity,
  orderAutomationPublishJobs,
  parseAutomationTopics,
  publishJobEvidence
} from '@/utils/geoAutomation'

defineOptions({ name: 'GeoAutomation' })

const route = useRoute()
const router = useRouter()
const projects = ref([])
const platforms = ref([])
const accountOverview = ref({ accounts: [], platforms: [] })
const recentRuns = ref([])
const currentRun = ref(null)
const processing = ref(false)
const starting = ref(false)
const workspaceReady = ref(false)
let stopRequested = false
let activeRunContext = null
let runSelectionEpoch = 0
const workerId = makeAutomationIdentity('geo-worker')
const pendingCreateStorageKey = 'geoAutomation.pendingCreate.v1'
const leaseHeartbeatMs = 60_000

const contentTypes = ['行业科普', '品牌介绍', '产品介绍', '解决方案', '对比文章', 'FAQ', '新闻稿']

const form = reactive({
  project_id: null,
  name: '',
  optimization_direction: '',
  topics_text: '',
  keywords: [],
  content_type: '行业科普',
  target_length: 1000,
  target_platform: '',
  publish_mode: 'prepare',
  platforms: [],
  account_ids: {}
})

const topics = computed(() => parseAutomationTopics(form.topics_text))
const selectedProject = computed(() => projects.value.find(project => project.id === form.project_id) || null)
const keywordOptions = computed(() => [...new Set([...(selectedProject.value?.keywords || []), ...form.keywords])])
const selectablePlatforms = computed(() => platforms.value.filter(platform => !platform.requires_video))
const hasDemo = computed(() => selectablePlatforms.value.some(platform => platform.demo_available))
const hasReal = computed(() => selectablePlatforms.value.some(platform => platform.runtime_available))
const publishModes = computed(() => [
  {
    value: 'prepare',
    label: '优化并准备',
    description: '运行到平台预检，不创建发布任务',
    disabled: false
  },
  {
    value: 'demo',
    label: '演示全流程',
    description: '创建并执行演示任务，不访问真实平台',
    disabled: !hasDemo.value
  },
  {
    value: 'real',
    label: '真实串行发布',
    description: '按本次快照绑定账号并逐项发送',
    disabled: !hasReal.value
  }
])
const currentPublishMode = computed(() => publishModes.value.find(mode => mode.value === form.publish_mode) || publishModes.value[0])
const selectedChannels = computed(() => form.platforms.map((platform) => {
  const accountId = form.publish_mode === 'real' ? (form.account_ids[platform] || null) : null
  const account = accountId == null
    ? null
    : connectedAccounts(platform).find(item => Number(item.id) === Number(accountId))
  return {
    platform,
    account_id: accountId,
    ...(form.publish_mode === 'real'
      ? { credential_epoch: Number(account?.credential_epoch || 0) || null }
      : {})
  }
}))
const runCounts = computed(() => automationRunCounts(currentRun.value))
const hasInterruptedItem = computed(() => (currentRun.value?.items || []).some(item => !TERMINAL_AUTOMATION_STATUSES.has(item.status) && item.status !== 'queued'))
const startBlocker = computed(() => {
  if (!form.project_id) return '先选择品牌项目'
  if (!form.optimization_direction.trim()) return '填写本轮 GEO 优化方向'
  if (!topics.value.length) return '至少填写一个文章主题'
  if (topics.value.length > GEO_AUTOMATION_ITEM_LIMIT) return `一次最多 ${GEO_AUTOMATION_ITEM_LIMIT} 篇`
  if (!form.keywords.length) return '至少选择一个品牌关键词'
  if (!form.platforms.length) return '至少选择一个发布渠道'
  const unavailable = form.platforms.find((platformKey) => {
    const platform = platforms.value.find(item => item.key === platformKey)
    return !platform || !platformSelectable(platform)
  })
  if (unavailable) return `${platformName(unavailable)}不支持当前执行模式`
  if (form.publish_mode === 'real') {
    const missing = form.platforms.find(platform => !form.account_ids[platform])
    if (missing) return `为${platformName(missing)}绑定明确发布账号`
    const stale = selectedChannels.value.find(channel => !channel.credential_epoch)
    if (stale) return `${platformName(stale.platform)}账号缺少凭证版本，请刷新账号列表后重选`
  }
  return ''
})

const platformName = key => platforms.value.find(item => item.key === key)?.name || key

const pendingCreateIdentity = (payload) => {
  const manifest = JSON.stringify(payload)
  try {
    const stored = JSON.parse(localStorage.getItem(pendingCreateStorageKey) || 'null')
    if (
      stored?.manifest === manifest
      && typeof stored.requestId === 'string'
      && typeof stored.idempotencyKey === 'string'
    ) return stored
  } catch {
    // A disabled or corrupt local store must not prevent a run from starting.
  }
  const identity = {
    manifest,
    requestId: makeAutomationIdentity('geo-auto-run'),
    idempotencyKey: makeAutomationIdentity('geo-auto-key')
  }
  try {
    localStorage.setItem(pendingCreateStorageKey, JSON.stringify(identity))
  } catch {
    // Backend idempotency still protects the active request when storage is unavailable.
  }
  return identity
}

const clearPendingCreateIdentity = () => {
  try {
    localStorage.removeItem(pendingCreateStorageKey)
  } catch {
    // No-op when browser storage is unavailable.
  }
}

const applyFreshRunSnapshot = (candidate, expectedRunId) => {
  if (!candidate || Number(candidate.id) !== Number(expectedRunId)) return false
  const current = currentRun.value
  if (current && Number(current.id) === Number(candidate.id)) {
    if (Number(candidate.state_version || 0) < Number(current.state_version || 0)) return false
    const candidateVersions = new Map(
      (candidate.items || []).map(item => [Number(item.id), Number(item.state_version || 0)])
    )
    const hasOlderItem = (current.items || []).some(item => (
      candidateVersions.has(Number(item.id))
      && candidateVersions.get(Number(item.id)) < Number(item.state_version || 0)
    ))
    if (hasOlderItem) return false
  }
  currentRun.value = candidate
  return true
}

const connectedAccounts = platformKey => (accountOverview.value.accounts || []).filter(account => (
  account.platform_key === platformKey && account.status === 'connected'
))

const platformSelectable = (platform) => {
  if (form.publish_mode === 'prepare') return true
  if (form.publish_mode === 'demo') return Boolean(platform.demo_available)
  return Boolean(platform.runtime_available)
}

const pruneUnavailableChannels = () => {
  const selectable = new Set(
    selectablePlatforms.value
      .filter(platform => platformSelectable(platform))
      .map(platform => platform.key)
  )
  const removed = form.platforms.filter(platformKey => !selectable.has(platformKey))
  if (!removed.length) return
  form.platforms = form.platforms.filter(platformKey => selectable.has(platformKey))
  removed.forEach((platformKey) => {
    delete form.account_ids[platformKey]
  })
}

const setPublishMode = (publishMode) => {
  form.publish_mode = publishMode
  pruneUnavailableChannels()
}

const platformChannelNote = (platform) => {
  if (platform.requires_video) return '视频素材渠道暂不进入文章自动化'
  if (form.publish_mode === 'prepare') return '生成平台副本并执行门禁'
  if (form.publish_mode === 'demo') return platform.demo_available ? '演示可用' : '当前环境不可用'
  return platform.runtime_available ? '真实发布运行时就绪' : (platform.unavailable_reason || '当前不可用')
}

const applyProjectDefaults = () => {
  const project = selectedProject.value
  if (!project) return
  form.keywords = [...(project.keywords || [])]
  if (!form.name.trim()) form.name = `${project.name} GEO 自动化`
}

const loadWorkspace = async () => {
  const [projectResponse, platformResponse, accountResponse, runsResponse] = await Promise.all([
    projectApi.getProjects(),
    publishApi.getPlatforms(),
    accountApi.getMediaAccounts().catch(() => ({ data: { accounts: [], platforms: [] } })),
    automationApi.listRuns({ limit: 20 })
  ])
  projects.value = projectResponse.data || []
  platforms.value = platformResponse.data || []
  accountOverview.value = accountResponse.data || { accounts: [], platforms: [] }
  pruneUnavailableChannels()
  recentRuns.value = runsResponse.data || []
  const requestedProjectId = Number(route.query.project_id)
  if (requestedProjectId && projects.value.some(project => project.id === requestedProjectId)) {
    form.project_id = requestedProjectId
  } else if (!form.project_id && projects.value.length) {
    form.project_id = projects.value[0].id
  }
  applyProjectDefaults()
  if (!form.platforms.length && selectablePlatforms.value.length) {
    form.platforms = selectablePlatforms.value.slice(0, 2).map(platform => platform.key)
  }
  const requestedRunId = Number(route.query.run_id)
  if (requestedRunId) await openRun(requestedRunId)
  workspaceReady.value = true
}

const syncRouteIntent = async () => {
  if (!workspaceReady.value) return
  const requestedRunId = Number(route.query.run_id)
  if (requestedRunId) {
    if (processing.value && Number(currentRun.value?.id) !== requestedRunId) {
      await router.replace({ path: '/geo-automation', query: { run_id: currentRun.value.id } })
      ElMessage.info('当前一键 GEO 仍在执行，不能切换到另一运行')
      return
    }
    if (Number(currentRun.value?.id) !== requestedRunId) await openRun(requestedRunId)
    return
  }
  const requestedProjectId = Number(route.query.project_id)
  if (!requestedProjectId || !projects.value.some(project => project.id === requestedProjectId)) return
  if (processing.value && currentRun.value) {
    await router.replace({ path: '/geo-automation', query: { run_id: currentRun.value.id } })
    ElMessage.info('当前一键 GEO 仍在执行，已保留运行控制台')
    return
  }
  currentRun.value = null
  form.project_id = requestedProjectId
  form.name = ''
  applyProjectDefaults()
}

const confirmAndStart = async () => {
  if (startBlocker.value || starting.value || processing.value) return
  const taskCount = topics.value.length * form.platforms.length
  const confirmation = [
    `品牌：${selectedProject.value.name}`,
    `文章：${topics.value.length} 篇`,
    `渠道：${form.platforms.map(platformName).join('、')}`,
    `最多发布任务：${taskCount} 项`,
    form.publish_mode === 'real'
      ? '确认后会真实访问平台并按账号串行发布。'
      : form.publish_mode === 'demo'
        ? '只执行演示状态链，不访问真实平台。'
        : '只生成、优化和预检，不创建平台任务。',
    '单项异常会暂存并继续；结果未知时不会自动重试。'
  ].join('\n')
  try {
    await ElMessageBox.confirm(confirmation, '确认本次一键 GEO 执行范围', {
      confirmButtonText: form.publish_mode === 'real' ? '确认真实执行' : '确认启动',
      cancelButtonText: '返回检查',
      type: form.publish_mode === 'real' ? 'warning' : 'info'
    })
  } catch {
    return
  }

  const config = {
    name: form.name.trim() || `${selectedProject.value.name} GEO 自动化`,
    topics: topics.value,
    keywords: [...form.keywords],
    optimization_direction: form.optimization_direction.trim(),
    target_length: Number(form.target_length),
    content_type: form.content_type,
    target_platform: form.target_platform,
    channels: selectedChannels.value,
    publish_mode: form.publish_mode,
    optimize: true,
    continue_on_error: true
  }
  const payload = {
    project_id: form.project_id,
    config,
    authorization: buildAutomationAuthorization({
      projectId: form.project_id,
      project: selectedProject.value,
      topics: topics.value,
      publishMode: form.publish_mode,
      channels: selectedChannels.value
    })
  }
  const { requestId, idempotencyKey } = pendingCreateIdentity(payload)
  const selectionEpoch = ++runSelectionEpoch
  starting.value = true
  try {
    const response = await automationApi.createRun(payload, { requestId, idempotencyKey })
    if (selectionEpoch !== runSelectionEpoch) return
    clearPendingCreateIdentity()
    currentRun.value = response.data
    router.replace({ path: '/geo-automation', query: { run_id: response.data.id } })
    await refreshRecentRuns()
    await processRun()
  } catch (error) {
    console.error('创建一键 GEO 运行失败:', error)
  } finally {
    starting.value = false
  }
}

const refreshRecentRuns = async () => {
  const response = await automationApi.listRuns({ limit: 20 })
  recentRuns.value = response.data || []
}

const refreshCurrentRun = async () => {
  if (!currentRun.value?.id) return
  const expectedRunId = Number(currentRun.value.id)
  const selectionEpoch = runSelectionEpoch
  const response = await automationApi.getRun(expectedRunId)
  if (
    selectionEpoch !== runSelectionEpoch
    || Number(currentRun.value?.id) !== expectedRunId
  ) return
  applyFreshRunSnapshot(response.data, expectedRunId)
}

const openRun = async (runId) => {
  if (starting.value) return
  if (processing.value && Number(currentRun.value?.id) !== Number(runId)) {
    await router.replace({ path: '/geo-automation', query: { run_id: currentRun.value.id } })
    ElMessage.info('当前一键 GEO 仍在执行，不能切换到另一运行')
    return
  }
  const selectionEpoch = ++runSelectionEpoch
  const response = await automationApi.getRun(runId)
  if (selectionEpoch !== runSelectionEpoch || starting.value || processing.value) return
  applyFreshRunSnapshot(response.data, runId)
  router.replace({ path: '/geo-automation', query: { run_id: runId } })
}

const newRun = () => {
  if (processing.value || starting.value) return
  runSelectionEpoch += 1
  clearPendingCreateIdentity()
  currentRun.value = null
  router.replace('/geo-automation')
}

const freshItem = itemId => currentRun.value?.items?.find(item => item.id === itemId)
const executionRun = () => activeRunContext || currentRun.value
const workerLeases = new Map()
const lostWorkerLeases = new Map()
const leaseKey = (itemId, stage) => `${Number(itemId)}:${stage}`

const assertWorkerLeaseHeld = (itemId, stage) => {
  const failure = lostWorkerLeases.get(leaseKey(itemId, stage))
  if (!failure) return
  throw Object.assign(new Error('工作租约续期失败，已丢弃晚到结果并停止写入'), {
    code: 'automation_item_busy',
    response: { status: 409, data: { data: { error_code: 'automation_item_busy', state_unknown: true } } },
    cause: failure
  })
}

const checkpoint = async (itemId, payload) => {
  const context = executionRun()
  if (!context?.id || Number(currentRun.value?.id) !== Number(context.id)) {
    throw new Error('运行上下文已变化，已停止写入以避免串批')
  }
  const item = freshItem(itemId)
  if (!item) throw new Error('流水线条目已不存在，请刷新')
  const lease = workerLeases.get(leaseKey(itemId, item.status))
  if (lease) assertWorkerLeaseHeld(itemId, item.status)
  const response = await automationApi.checkpoint(context.id, itemId, {
    expected_state_version: item.state_version,
    worker_id: workerId,
    ...(lease ? {
      lease_token: lease.lease_token,
      lease_epoch: lease.lease_epoch
    } : {}),
    ...payload
  })
  if (Number(response.data?.id) !== Number(context.id)) {
    throw new Error('后端返回了不同运行，已停止执行')
  }
  applyFreshRunSnapshot(response.data, context.id)
  if (lease && payload.status !== item.status) {
    workerLeases.delete(leaseKey(itemId, item.status))
    lostWorkerLeases.delete(leaseKey(itemId, item.status))
  }
  return freshItem(itemId)
}

const acquireItemLease = async (item, stage) => {
  const context = executionRun()
  const response = await automationApi.acquireLease(context.id, item.id, {
    expected_state_version: item.state_version,
    worker_id: workerId,
    stage
  })
  const key = leaseKey(item.id, stage)
  workerLeases.set(key, response.data)
  lostWorkerLeases.delete(key)
  return response.data
}

const releaseItemLease = async (itemId, stage) => {
  const context = executionRun()
  const key = leaseKey(itemId, stage)
  const lease = workerLeases.get(key)
  if (!lease) return
  await automationApi.releaseLease(context.id, itemId, {
    worker_id: workerId,
    stage,
    lease_token: lease.lease_token,
    lease_epoch: lease.lease_epoch
  })
  workerLeases.delete(key)
  lostWorkerLeases.delete(key)
}

const withLeaseHeartbeat = async (itemId, stage, action) => {
  const context = executionRun()
  const key = leaseKey(itemId, stage)
  const timer = globalThis.setInterval(() => {
    const lease = workerLeases.get(key)
    if (!lease || lostWorkerLeases.has(key)) return
    automationApi.renewLease(context.id, itemId, {
      worker_id: workerId,
      stage,
      lease_token: lease.lease_token,
      lease_epoch: lease.lease_epoch
    }).then((response) => {
      workerLeases.set(key, response.data)
    }).catch((error) => {
      lostWorkerLeases.set(key, error)
      console.warn(`一键 GEO 条目 ${itemId} 工作租约续期未确认:`, error?.message || error)
    })
  }, leaseHeartbeatMs)
  try {
    return await action()
  } finally {
    globalThis.clearInterval(timer)
  }
}

const automationErrorCode = error => String(
  error?.response?.data?.data?.error_code || error?.code || ''
)

const resetUnsubmittedAttemptAndRelease = async (item, stage, resultPatch) => {
  try {
    await checkpoint(item.id, {
      status: stage,
      stage,
      message: '模型调用尚未提交，已在安全检查点暂停',
      article_id: item.article_id || undefined,
      result: {
        ...(item.result || {}),
        ...resultPatch
      }
    })
  } finally {
    await releaseItemLease(item.id, stage).catch(() => null)
  }
}

const checkpointIssue = async (itemId, stage, error, fallback) => {
  const evidence = automationErrorEvidence(error, fallback)
  await checkpoint(itemId, {
    status: 'need_action',
    stage,
    message: evidence.state_unknown ? '结果无法确认，已停止自动重试' : evidence.message,
    error: evidence
  })
}

const editableArticleSignature = article => JSON.stringify({
  id: Number(article?.id),
  title: String(article?.title || ''),
  summary: String(article?.summary || ''),
  content: String(article?.content || ''),
  tags: Array.isArray(article?.tags) ? article.tags.map(String) : [],
  status: String(article?.status || '')
})

const recoverGeneration = async (item) => {
  if (!item.generation_request_id) return null
  try {
    const response = await articleApi.getContentGenerationRunByRequestId(
      item.generation_request_id,
      { suppressGlobalError: true }
    )
    const run = response.data
    if (run.status === 'succeeded' && run.result) return run
    if (run.status === 'started') {
      throw Object.assign(new Error('生成仍处于执行中，暂不重复调用模型'), {
        code: 'generation_in_progress'
      })
    }
    throw Object.assign(new Error(run.error_message || '生成未成功'), {
      code: run.error_code || 'generation_failed',
      response: { data: { data: { state_unknown: run.status === 'unknown' } } }
    })
  } catch (error) {
    if (error?.response?.status === 404) return null
    throw error
  }
}

const saveGeneratedArticle = async (item, draft, generationRunId) => {
  const context = executionRun()
  if (item.article_id) return articleApi.getArticle(item.article_id).then(response => response.data)
  try {
    const response = await articleApi.createArticle({
      project_id: context.project_id,
      title: draft.title,
      summary: draft.summary || '',
      content: draft.content,
      tags: Array.isArray(draft.tags) ? draft.tags : [],
      status: 'draft',
      generation_run_id: generationRunId,
      target_length: context.config.target_length
    })
    return response.data
  } catch (error) {
    const recovered = await articleApi.getContentGenerationRun(generationRunId, {
      suppressGlobalError: true
    }).catch(() => null)
    if (recovered?.data?.article_id) {
      const article = await articleApi.getArticle(recovered.data.article_id)
      return article.data
    }
    throw error
  }
}

const generateAndSave = async (item) => {
  const context = executionRun()
  const requestId = item.generation_request_id || `geo-auto-${context.id}-item-${item.id}-generate`
  if (item.status === 'queued') {
    item = await checkpoint(item.id, {
      status: 'generating',
      stage: 'generating',
      message: '正在生成并记录模型回执',
      generation_request_id: requestId
    })
  }
  let recoveredBeforeLease = null
  let generationInProgressError = null
  try {
    recoveredBeforeLease = await recoverGeneration(item)
  } catch (error) {
    if (automationErrorCode(error) !== 'generation_in_progress') throw error
    generationInProgressError = error
  }
  await acquireItemLease(item, 'generating')
  return withLeaseHeartbeat(item.id, 'generating', async () => {
    if (generationInProgressError) throw generationInProgressError
    let generationRun = recoveredBeforeLease || await recoverGeneration(item)
    let draft = generationRun?.result
    if (!draft) {
      if (stopRequested || currentRun.value.pause_requested) {
        await releaseItemLease(item.id, 'generating')
        return item
      }
      item = await checkpoint(item.id, {
        status: 'generating',
        stage: 'generating',
        message: '已锁定本篇写稿调用；结果未知时不会自动重试',
        generation_request_id: requestId,
        result: {
          ...(item.result || {}),
          generation_attempt_started: true
        }
      })
      if (stopRequested || currentRun.value.pause_requested) {
        await resetUnsubmittedAttemptAndRelease(item, 'generating', {
          generation_attempt_started: false,
          generation_paused_before_call: true
        })
        return null
      }
      try {
        const response = await articleApi.generateArticle({
          project_id: context.project_id,
          topic: item.topic,
          keywords: context.config.keywords,
          length: context.config.target_length,
          content_type: context.config.content_type,
          target_platform: context.config.target_platform,
          optimization_direction: context.config.optimization_direction,
          project_snapshot: context.project_snapshot,
          idempotency_key: `${requestId}-key`,
          automation_context: {
            run_id: context.id,
            item_id: item.id,
            worker_id: workerId,
            lease_token: workerLeases.get(leaseKey(item.id, 'generating'))?.lease_token,
            lease_epoch: workerLeases.get(leaseKey(item.id, 'generating'))?.lease_epoch
          }
        }, {
          headers: {
            'X-Request-ID': requestId,
            'Idempotency-Key': `${requestId}-key`
          },
          suppressGlobalError: true
        })
        draft = response.data
        assertWorkerLeaseHeld(item.id, 'generating')
        generationRun = {
          id: Number(draft.generation?.run_id),
          result: draft
        }
      } catch (error) {
        if (automationErrorCode(error) === 'automation_paused') {
          await resetUnsubmittedAttemptAndRelease(item, 'generating', {
            generation_attempt_started: false,
            generation_paused_before_call: true
          })
          return null
        }
        const recovered = await recoverGeneration({
          ...item,
          generation_request_id: requestId
        }).catch(() => null)
        if (!recovered?.result) throw error
        generationRun = recovered
        draft = recovered.result
      }
    }
    const generationRunId = Number(generationRun.id || draft?.generation?.run_id)
    if (!generationRunId || !draft?.title || !draft?.content) {
      throw new Error('生成回执缺少文章或运行标识')
    }
    assertWorkerLeaseHeld(item.id, 'generating')
    const article = await saveGeneratedArticle(item, draft, generationRunId)
    assertWorkerLeaseHeld(item.id, 'generating')
    return checkpoint(item.id, {
      status: 'optimizing',
      stage: 'optimizing',
      message: '草稿已保存，正在执行 GEO 优化',
      article_id: article.id,
      generation_request_id: requestId,
      generation_run_id: generationRunId,
      result: {
        generated_title: article.title,
        generated_geo_score: article.geo_score,
        generation_attempt_started: true,
        generation_completed: true
      }
    })
  })
}

const optimizeArticle = async (item) => {
  if (!item.article_id) throw new Error('优化阶段缺少文章标识')
  const context = executionRun()
  await acquireItemLease(item, 'optimizing')
  return withLeaseHeartbeat(item.id, 'optimizing', async () => {
  const sourceArticle = (await articleApi.getArticle(item.article_id)).data
  const sourceSignature = editableArticleSignature(sourceArticle)
  if (stopRequested || currentRun.value.pause_requested) {
    await releaseItemLease(item.id, 'optimizing')
    return null
  }
  item = await checkpoint(item.id, {
    status: 'optimizing',
    stage: 'optimizing',
    message: '已发起 GEO 优化调用；结果未知时不会自动重试',
    article_id: item.article_id,
    result: {
      ...(item.result || {}),
      optimization_attempt_started: true
    }
  })
  if (stopRequested || currentRun.value.pause_requested) {
    await resetUnsubmittedAttemptAndRelease(item, 'optimizing', {
      optimization_attempt_started: false,
      optimization_paused_before_call: true
    })
    return null
  }
  let response
  try {
    response = await articleApi.optimizeArticle(item.article_id, {
      target_platforms: context.config.channels.map(channel => channel.platform),
      optimization_direction: context.config.optimization_direction,
      project_snapshot: context.project_snapshot,
      automation_context: {
        run_id: context.id,
        item_id: item.id,
        worker_id: workerId,
        lease_token: workerLeases.get(leaseKey(item.id, 'optimizing'))?.lease_token,
        lease_epoch: workerLeases.get(leaseKey(item.id, 'optimizing'))?.lease_epoch
      }
    }, { suppressGlobalError: true })
  } catch (error) {
    if (automationErrorCode(error) === 'automation_paused') {
      await resetUnsubmittedAttemptAndRelease(item, 'optimizing', {
        optimization_attempt_started: false,
        optimization_paused_before_call: true
      })
      return null
    }
    throw error
  }
  assertWorkerLeaseHeld(item.id, 'optimizing')
  const optimized = response.data?.optimized
  const after = response.data?.after || {}
  if (!optimized?.title || !optimized?.content) throw new Error('优化结果缺少标题或正文')
  const latestArticle = (await articleApi.getArticle(item.article_id)).data
  assertWorkerLeaseHeld(item.id, 'optimizing')
  if (editableArticleSignature(latestArticle) !== sourceSignature) {
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'optimizing',
      message: '模型优化已返回，但文章同时被人工修改，未覆盖人工版本',
      article_id: item.article_id,
      result: {
        ...(item.result || {}),
        optimization_attempt_started: true,
        optimization_completed: true,
        optimized_not_saved: true,
        optimized_geo_score: after.score,
        citeability_score: after.citeability?.score
      },
      error: {
        code: 'article_changed_during_optimization',
        message: '请人工比较当前文章与优化建议后，再新建一轮或手工保存',
        state_unknown: false
      }
    })
    return null
  }
  const publishGatePassed = after.citeability?.publish_gate_passed === true
  let saved
  try {
    assertWorkerLeaseHeld(item.id, 'optimizing')
    saved = await articleApi.updateArticle(item.article_id, {
      title: optimized.title,
      summary: optimized.summary || '',
      content: optimized.content,
      tags: Array.isArray(optimized.tags) ? optimized.tags : [],
      status: publishGatePassed ? 'ready' : 'draft',
      expected_content_fingerprint: sourceArticle.content_fingerprint
    })
  } catch (error) {
    if (error?.response?.status !== 409) throw error
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'optimizing',
      message: '保存优化稿前文章又被修改，未覆盖人工版本',
      article_id: item.article_id,
      result: {
        ...(item.result || {}),
        optimization_attempt_started: true,
        optimization_completed: true,
        optimized_not_saved: true,
        optimized_geo_score: after.score,
        citeability_score: after.citeability?.score
      },
      error: {
        code: 'article_changed_during_optimization',
        message: '请人工比较当前文章与优化建议后，再新建一轮或手工保存',
        state_unknown: false
      }
    })
    return null
  }
  if (!publishGatePassed) {
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'optimizing',
      message: '优化稿已保存，但 AI 可引用性发布门禁未通过',
      article_id: saved.data.id,
      result: {
        optimized_geo_score: after.score,
        citeability_score: after.citeability?.score,
        publish_gate_passed: false,
        optimization_attempt_started: true,
        optimization_completed: true
      },
      error: {
        code: 'citeability_gate_blocked',
        message: '补齐证据、实体或回答结构后再准备发布',
        state_unknown: false
      }
    })
    return null
  }
  return checkpoint(item.id, {
    status: 'preflighting',
    stage: 'preflighting',
    message: '优化稿已保存并通过引用门禁，正在检查平台副本',
    article_id: saved.data.id,
    result: {
      optimized_geo_score: after.score,
      citeability_score: after.citeability?.score,
      publish_gate_passed: true,
      optimization_attempt_started: true,
      optimization_completed: true
    }
  })
  })
}

const preflightArticle = async (item) => {
  const context = executionRun()
  const sourceArticle = (await articleApi.getArticle(item.article_id)).data
  const sourceSignature = editableArticleSignature(sourceArticle)
  item = await checkpoint(item.id, {
    status: 'preflighting',
    stage: 'preflighting',
    message: '正在按品牌快照检查全部平台副本',
    article_id: item.article_id
  })
  const platformKeys = context.config.channels.map(channel => channel.platform)
  const response = await publishApi.previewVariants({
    article_id: item.article_id,
    batch_article_ids: [item.article_id],
    platforms: platformKeys,
    account_ids: Object.fromEntries(context.config.channels.map(channel => [
      channel.platform,
      channel.account_id
    ]))
  })
  const variants = response.data?.variants || []
  await refreshCurrentRun()
  if (stopRequested || currentRun.value.pause_requested) return null
  const latestArticle = (await articleApi.getArticle(item.article_id)).data
  if (editableArticleSignature(latestArticle) !== sourceSignature) {
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'preflighting',
      message: '平台预检期间文章被修改，未创建发布任务',
      error: {
        code: 'article_changed_during_preflight',
        message: '请核对人工修改后重新发起一轮，避免发布未经本轮门禁确认的内容',
        state_unknown: false
      }
    })
    return null
  }
  if (stopRequested || currentRun.value.pause_requested) return null
  const blockers = variants.filter(variant => (
    variant.offline_preflight?.passed !== true
    || variant.citeability?.publish_gate_passed !== true
  ))
  if (variants.length !== platformKeys.length || blockers.length) {
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'preflighting',
      message: blockers.length
        ? `${blockers.map(variant => platformName(variant.platform)).join('、')}平台副本未通过门禁`
        : '平台预检结果不完整',
      error: {
        code: 'platform_preflight_blocked',
        message: '平台副本存在阻断项，已暂存并继续下一篇',
        state_unknown: false
      }
    })
    return null
  }
  if (context.config.publish_mode === 'prepare') {
    await checkpoint(item.id, {
      status: 'completed',
      stage: 'preflighting',
      message: '写稿、优化与平台预检已完成；未创建发布任务',
      preflight_article_fingerprint: sourceArticle.content_fingerprint,
      result: {
        prepared_platforms: platformKeys,
        publish_mode: 'prepare'
      }
    })
    return null
  }
  const next = await checkpoint(item.id, {
    status: 'publishing',
    stage: 'publishing',
    message: '平台副本已通过门禁，正在按渠道串行执行',
    preflight_article_fingerprint: sourceArticle.content_fingerprint,
    result: {
      prepared_platforms: platformKeys,
      publish_mode: context.config.publish_mode
    }
  })
  return { item: next, variants }
}

const recoverAutomationJobs = async (item) => {
  const response = await publishApi.getJobs({ automation_item_id: item.id, page_size: 100 })
  return response.data?.items || []
}

const createAutomationJobs = async (item, variants) => {
  const context = executionRun()
  const variantByPlatform = Object.fromEntries(variants.map(variant => [variant.platform, variant]))
  const jobs = [...(item.publish_jobs || [])]
  for (const channel of context.config.channels) {
    if (stopRequested || currentRun.value.pause_requested) {
      const ordered = orderAutomationPublishJobs(jobs, context.config.channels)
      if (!ordered.valid) throw new Error('发布任务与授权渠道不一致')
      return { item, jobs: ordered.jobs, paused: true }
    }
    if (jobs.some(job => job.platform === channel.platform)) continue
    const idempotencyKey = `geo-auto-${context.id}-${item.id}-${channel.platform}`
    const variant = variantByPlatform[channel.platform]
    try {
      const response = await publishApi.createJob({
        article_id: item.article_id,
        batch_article_ids: [item.article_id],
        automation_item_id: item.id,
        idempotency_key: idempotencyKey,
        platform: channel.platform,
        account_id: channel.account_id,
        images: [],
        video: '',
        auto_image: true,
        auto_execute: false,
        publish_title: variant?.title ?? null,
        publish_content: variant?.publish_content ?? null
      }, {
        headers: { 'Idempotency-Key': idempotencyKey },
        suppressGlobalError: true
      })
      jobs.push(publishJobEvidence(response.data))
    } catch (error) {
      const recovered = await recoverAutomationJobs(item).catch(() => [])
      const job = recovered.find(candidate => candidate.platform === channel.platform)
      if (job) jobs.push(publishJobEvidence(job))
      else jobs.push({
        platform: channel.platform,
        job_id: 0,
        status: 'need_action',
        message: automationErrorEvidence(error, '发布任务创建结果未知').message,
        result_url: '',
        outcome_category: 'creation_unknown'
      })
    }
    const ordered = orderAutomationPublishJobs(jobs, context.config.channels)
    if (!ordered.valid) throw new Error('发布任务与授权渠道不一致')
    const validJobs = ordered.jobs.filter(job => Number(job.job_id) > 0)
    item = await checkpoint(item.id, {
      status: 'publishing',
      stage: 'publishing',
      message: `已创建 ${validJobs.length}/${context.config.channels.length} 个发布任务`,
      publish_jobs: validJobs
    })
    if (stopRequested || currentRun.value.pause_requested) {
      return { item, jobs: ordered.jobs, paused: true }
    }
  }
  const ordered = orderAutomationPublishJobs(jobs, context.config.channels)
  if (!ordered.valid) throw new Error('发布任务与授权渠道不一致')
  return { item, jobs: ordered.jobs, paused: false }
}

const executeAutomationJobs = async (item, jobs) => {
  const context = executionRun()
  const outcomes = []
  for (const evidence of jobs) {
    if (stopRequested || currentRun.value.pause_requested) return
    if (!Number(evidence.job_id)) {
      outcomes.push(evidence)
      continue
    }
    let job
    try {
      job = (await publishApi.getJob(evidence.job_id)).data
      if (job.status === 'processing') return
      if (job.status === 'queued') {
        if (stopRequested || currentRun.value.pause_requested) return
        job = context.config.publish_mode === 'demo'
          ? (await publishApi.executeDemoJob(job.job_id)).data
          : (await publishApi.executeRealJob(job)).data
      }
    } catch (error) {
      const recovered = await publishApi.getJob(evidence.job_id).catch(() => null)
      const errorCode = error?.response?.data?.data?.error_code
      if (
        recovered?.data?.status === 'processing'
        || (
          recovered?.data?.status === 'queued'
          && ['automation_paused', 'automation_channel_waiting'].includes(errorCode)
        )
      ) return
      job = recovered?.data || {
        ...evidence,
        status: 'need_action',
        message: automationErrorEvidence(error, '发布结果未知，已停止重试').message,
        outcome_category: 'outcome_unknown'
      }
    }
    outcomes.push(Number(job?.job_id ?? job?.id) > 0 ? publishJobEvidence(job) : job)
    const persistedOutcomes = outcomes.filter(outcome => Number(outcome.job_id) > 0)
    item = await checkpoint(item.id, {
      status: 'publishing',
      stage: 'publishing',
      message: `已处理 ${outcomes.length}/${jobs.length} 个渠道`,
      publish_jobs: persistedOutcomes
    })
    if (stopRequested || currentRun.value.pause_requested) return
  }
  const issues = outcomes.filter(job => job.status !== 'success')
  const persistedOutcomes = outcomes.filter(outcome => Number(outcome.job_id) > 0)
  if (issues.length) {
    await checkpoint(item.id, {
      status: 'need_action',
      stage: 'publishing',
      message: `${issues.length} 个渠道未确认成功，已停止重试并继续下一篇`,
      publish_jobs: persistedOutcomes,
      error: {
        code: 'publish_channels_need_action',
        message: issues.map(job => `${platformName(job.platform)}：${job.message || job.status}`).join('；'),
        state_unknown: issues.some(job => ['processing', 'need_action'].includes(job.status))
      }
    })
    return
  }
  await checkpoint(item.id, {
    status: 'completed',
    stage: 'publishing',
    message: '所有已授权渠道均已确认完成',
    publish_jobs: persistedOutcomes,
    result: { published_platforms: outcomes.map(job => job.platform) }
  })
}

const processItem = async (sourceItem) => {
  let item = freshItem(sourceItem.id) || sourceItem
  try {
    if (item.status === 'queued' || item.status === 'generating') {
      item = await generateAndSave(item)
    }
    if (stopRequested || currentRun.value.pause_requested) return
    if (item?.status === 'optimizing') {
      item = await optimizeArticle(item)
      if (!item) return
    }
    if (stopRequested || currentRun.value.pause_requested) return
    if (item.status === 'preflighting') {
      const prepared = await preflightArticle(item)
      if (!prepared) return
      item = prepared.item
      const created = await createAutomationJobs(item, prepared.variants)
      if (created.paused) return
      await executeAutomationJobs(created.item, created.jobs)
      return
    }
    if (item.status === 'publishing') {
      const context = executionRun()
      const recoveredJobs = await recoverAutomationJobs(item)
      const orderedRecovery = orderAutomationPublishJobs(
        recoveredJobs,
        context.config.channels
      )
      const recoveredEvidence = orderedRecovery.jobs
      if (!orderedRecovery.valid) {
        await checkpoint(item.id, {
          status: 'need_action',
          stage: 'publishing',
          message: '恢复到的渠道任务与授权顺序不一致，已停止自动执行',
          publish_jobs: recoveredEvidence,
          error: {
            code: 'publish_job_manifest_mismatch',
            message: '请人工核对发布任务；系统未领取任何新的渠道任务',
            state_unknown: true
          }
        })
        return
      }
      if (recoveredEvidence.length) {
        item = await checkpoint(item.id, {
          status: 'publishing',
          stage: 'publishing',
          message: `已恢复 ${recoveredEvidence.length}/${context.config.channels.length} 个冻结发布任务`,
          publish_jobs: recoveredEvidence
        })
      }
      const recoveredPlatforms = new Set(recoveredEvidence.map(job => job.platform))
      const allJobsRecovered = context.config.channels.every(channel => (
        recoveredPlatforms.has(channel.platform)
      ))
      if (allJobsRecovered) {
        await executeAutomationJobs(item, recoveredEvidence)
        return
      }
      const article = (await articleApi.getArticle(item.article_id)).data
      if (article.status !== 'ready') {
        await checkpoint(item.id, {
          status: 'need_action',
          stage: 'publishing',
          message: '已建任务未覆盖全部渠道，且文章已不再是待发布状态',
          publish_jobs: recoveredEvidence,
          error: {
            code: 'incomplete_publish_job_set',
            message: '为避免重复发布，未重新预检或补建缺失任务，请人工核对平台后台',
            state_unknown: true
          }
        })
        return
      }
      const preflight = await publishApi.previewVariants({
        article_id: item.article_id,
        batch_article_ids: [item.article_id],
        platforms: context.config.channels.map(channel => channel.platform),
        account_ids: Object.fromEntries(context.config.channels.map(channel => [channel.platform, channel.account_id]))
      })
      const variants = preflight.data?.variants || []
      await refreshCurrentRun()
      if (stopRequested || currentRun.value.pause_requested) return
      const blockers = variants.filter(variant => (
        variant.offline_preflight?.passed !== true
        || variant.citeability?.publish_gate_passed !== true
      ))
      if (variants.length !== context.config.channels.length || blockers.length) {
        await checkpoint(item.id, {
          status: 'need_action',
          stage: 'publishing',
          message: '缺失任务无法通过安全预检补建',
          publish_jobs: recoveredEvidence,
          error: {
            code: 'publish_resume_preflight_blocked',
            message: '已保留现有任务，未重复执行或绕过平台门禁',
            state_unknown: false
          }
        })
        return
      }
      const created = await createAutomationJobs(item, variants)
      if (created.paused) return
      await executeAutomationJobs(created.item, created.jobs)
    }
  } catch (error) {
    console.error(`一键 GEO 条目 ${item.id} 未完成:`, error)
    const errorCode = automationErrorCode(error)
    const isConflict = error?.response?.status === 409
    const shouldParkConflict = [
      'automation_attempt_outcome_unknown',
      'automation_project_snapshot_changed'
    ].includes(errorCode)
    if (
      ['automation_item_busy', 'automation_paused', 'generation_in_progress'].includes(errorCode)
      || (isConflict && !shouldParkConflict)
    ) {
      await refreshCurrentRun().catch(() => null)
      return
    }
    if (isConflict) await refreshCurrentRun().catch(() => null)
    const latest = freshItem(item.id)
    if (latest && !TERMINAL_AUTOMATION_STATUSES.has(latest.status)) {
      try {
        await checkpointIssue(latest.id, latest.stage || latest.status, error, '该步骤未完成，已继续下一篇')
      } catch (checkpointError) {
        console.error(`一键 GEO 条目 ${item.id} 异常证据暂存失败:`, checkpointError)
        await refreshCurrentRun().catch(() => null)
      }
    }
  }
}

const processRun = async () => {
  if (!currentRun.value || processing.value || isAutomationTerminal(currentRun.value.status)) return
  processing.value = true
  stopRequested = false
  activeRunContext = Object.freeze({
    id: Number(currentRun.value.id),
    project_id: Number(currentRun.value.project_id),
    project_snapshot: JSON.parse(JSON.stringify(currentRun.value.project_snapshot || {})),
    config: JSON.parse(JSON.stringify(currentRun.value.config || {}))
  })
  try {
    if (currentRun.value.pause_requested) {
      const response = await automationApi.setPaused(currentRun.value, false)
      applyFreshRunSnapshot(response.data, activeRunContext.id)
    }
    for (const snapshot of currentRun.value.items || []) {
      if (stopRequested || currentRun.value.pause_requested) break
      if (TERMINAL_AUTOMATION_STATUSES.has(freshItem(snapshot.id)?.status)) continue
      try {
        await processItem(snapshot)
        await refreshCurrentRun()
        const latest = freshItem(snapshot.id)
        if (latest && !TERMINAL_AUTOMATION_STATUSES.has(latest.status)) break
      } catch (error) {
        console.error(`一键 GEO 条目 ${snapshot.id} 无法继续，但不会阻断后续条目:`, error)
        await refreshCurrentRun().catch(() => null)
        const latest = freshItem(snapshot.id)
        if (latest && !TERMINAL_AUTOMATION_STATUSES.has(latest.status)) break
      }
    }
    await refreshCurrentRun()
    await refreshRecentRuns()
    if (isAutomationTerminal(currentRun.value?.status)) {
      const counts = automationRunCounts(currentRun.value)
      if (counts.issues) ElMessage.warning(`一键 GEO 已跑完，${counts.issues} 篇需要处理`)
      else ElMessage.success('一键 GEO 全流程已完成')
    }
  } finally {
    activeRunContext = null
    processing.value = false
  }
}

const requestPause = async () => {
  stopRequested = true
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await refreshCurrentRun()
      const response = await automationApi.setPaused(currentRun.value, true)
      applyFreshRunSnapshot(response.data, currentRun.value.id)
      ElMessage.info('会在当前模型或平台调用返回后停止，不会中断外部提交')
      return
    } catch (error) {
      if (attempt === 1 || error?.response?.status !== 409) {
        console.error('暂停请求未保存:', error)
        ElMessage.warning('暂停标记暂未保存；本页仍会在当前检查点停止，请刷新核对运行状态')
      }
    }
  }
}

const resumeRun = async () => {
  await refreshCurrentRun()
  await processRun()
}

const openArticle = (articleId) => {
  if (currentRun.value && !isAutomationTerminal(currentRun.value.status)) {
    ElMessage.info('运行中的文章已锁定；完成或暂存后再打开编辑，避免覆盖自动化结果')
    return
  }
  router.push({ path: '/content-create', query: { articleId } })
}
const openPublishJob = jobId => router.push({ path: '/publish-center', query: { focus_job_id: jobId } })

onMounted(async () => {
  try {
    await loadWorkspace()
  } catch (error) {
    console.error('一键 GEO 工作区加载失败:', error)
    ElMessage.error(error?.response?.data?.msg || '一键 GEO 工作区加载失败')
  }
})

watch(
  () => route.fullPath,
  () => {
    runSelectionEpoch += 1
    if (route.path !== '/geo-automation') return
    syncRouteIntent().catch((error) => {
      console.error('一键 GEO 路由目标切换失败:', error)
      ElMessage.error('无法切换一键 GEO 目标，请刷新后重试')
    })
  }
)

watch(
  () => [
    form.publish_mode,
    ...platforms.value.map(platform => (
      `${platform.key}:${Boolean(platform.demo_available)}:${Boolean(platform.runtime_available)}`
    ))
  ],
  () => pruneUnavailableChannels(),
  { flush: 'sync' }
)
</script>

<style lang="scss" scoped>
.geo-automation {
  display: grid;
  gap: 24px;
  color: var(--geo-ink);
}

.automation-header,
.run-console-header,
.recent-runs > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.automation-header {
  padding: 8px 0 4px;

  h1 { margin: 5px 0 8px; font-size: clamp(30px, 4vw, 48px); letter-spacing: -0.045em; }
  p:not(.eyebrow) { max-width: 760px; margin: 0; color: var(--geo-muted); line-height: 1.7; }
}

.eyebrow,
.section-heading > span,
.run-console-header > div:first-child > span,
.recent-runs header span {
  margin: 0;
  color: var(--geo-muted);
  font: 700 11px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace;
  letter-spacing: 0.12em;
}

.run-id {
  padding: 8px 11px;
  border: 1px solid var(--geo-line);
  background: var(--geo-surface);
  color: var(--geo-ink);
  font: 700 11px/1.4 ui-monospace, "Cascadia Mono", Consolas, monospace;
  cursor: pointer;
}

.setup-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  align-items: start;
  gap: 24px;
}

.setup-form,
.authorization-panel,
.run-console,
.recent-runs {
  border: 1px solid var(--geo-line);
  background: var(--geo-surface);
}

.setup-form { padding: clamp(20px, 3vw, 34px); }
.authorization-panel { position: sticky; top: 18px; padding: 26px; }

.section-heading {
  margin-bottom: 22px;

  &.second { margin-top: 34px; }
  h2 { margin: 5px 0; font-size: 21px; letter-spacing: -0.025em; }
  p { margin: 0; color: var(--geo-muted); font-size: 13px; }
}

.form-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;

  &.three { grid-template-columns: 1fr 0.7fr 1fr; }
}

.topic-counter {
  display: flex;
  width: 100%;
  justify-content: space-between;
  margin-top: 7px;
  color: var(--geo-muted);
  font-size: 12px;

  strong { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
  &.invalid { color: var(--geo-danger); }
}

.mode-switch {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;

  button {
    display: grid;
    min-height: 92px;
    align-content: start;
    gap: 8px;
    padding: 15px;
    border: 1px solid var(--geo-line);
    background: var(--geo-surface);
    color: var(--geo-ink);
    text-align: left;
    cursor: pointer;

    small { color: var(--geo-muted); line-height: 1.45; }
    &.active { border-color: var(--geo-ink); box-shadow: inset 0 -3px 0 var(--geo-ink); }
    &:disabled { cursor: not-allowed; opacity: 0.45; }
  }
}

.channel-fieldset {
  margin: 24px 0 0;
  padding: 0;
  border: 0;

  legend { margin-bottom: 10px; font-size: 14px; font-weight: 700; }
}

.channel-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-top: 1px solid var(--geo-line);
  border-left: 1px solid var(--geo-line);
}

.channel-option {
  display: flex;
  min-height: 72px;
  gap: 10px;
  padding: 13px;
  border-right: 1px solid var(--geo-line);
  border-bottom: 1px solid var(--geo-line);
  cursor: pointer;

  input { margin-top: 3px; accent-color: var(--geo-ink); }
  span { display: grid; gap: 4px; }
  small { color: var(--geo-muted); font-size: 11px; line-height: 1.35; }
  &.selected { background: var(--geo-soft); }
  &.disabled { cursor: not-allowed; opacity: 0.48; }
}

.account-bindings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 20px;

  label { display: grid; gap: 7px; color: var(--geo-muted); font-size: 12px; }
}

.authorization-panel dl {
  margin: 0;

  div {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 14px;
    padding: 12px 0;
    border-bottom: 1px solid var(--geo-line);
  }

  dt { color: var(--geo-muted); }
  dd { margin: 0; font-weight: 700; text-align: right; }
}

.safety-contract {
  margin: 22px 0;
  padding: 15px;
  border-left: 2px solid var(--geo-warning);
  background: #faf7ef;

  ul { display: grid; gap: 7px; margin: 10px 0 0; padding-left: 18px; color: var(--geo-ink-secondary); font-size: 12px; line-height: 1.55; }
}

.start-blocker { margin: 0 0 10px; color: var(--geo-danger); font-size: 12px; }
.start-button { width: 100%; }

.run-console { padding: clamp(18px, 3vw, 30px); }

.run-console-header {
  align-items: center;
  margin-bottom: 18px;

  h2 { margin: 4px 0; font-size: 24px; }
  p { margin: 0; color: var(--geo-muted); }
}

.run-tally {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px 14px;
  color: var(--geo-muted);
  font-size: 13px;

  strong { color: var(--geo-success); }
  span { color: var(--geo-danger); }
  em { font-style: normal; }
}

.run-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.run-progress { height: 2px; margin-bottom: 18px; background: var(--geo-line); }
.run-progress span { display: block; height: 100%; background: var(--geo-success); transition: width 220ms ease; }

.recent-runs { padding: 22px; }
.recent-runs header h2 { margin: 4px 0 14px; }
.recent-runs > button {
  display: grid;
  width: 100%;
  grid-template-columns: 100px 1fr auto;
  gap: 16px;
  padding: 12px 4px;
  border: 0;
  border-top: 1px solid var(--geo-line);
  background: transparent;
  color: var(--geo-ink);
  text-align: left;
  cursor: pointer;

  code { color: var(--geo-muted); font-size: 11px; }
  span { color: var(--geo-muted); font-size: 12px; }
}

@media (max-width: 1080px) {
  .setup-grid { grid-template-columns: 1fr; }
  .authorization-panel { position: static; }
  .run-console-header { align-items: flex-start; flex-wrap: wrap; }
}

@media (max-width: 760px) {
  .automation-header,
  .run-console-header { align-items: flex-start; flex-direction: column; }
  .form-pair,
  .form-pair.three,
  .mode-switch,
  .channel-grid,
  .account-bindings { grid-template-columns: 1fr; }
  .recent-runs > button { grid-template-columns: 1fr; gap: 4px; }
  .run-actions { justify-content: flex-start; }
}

@media (prefers-reduced-motion: reduce) {
  .run-progress span { transition: none; }
}
</style>
