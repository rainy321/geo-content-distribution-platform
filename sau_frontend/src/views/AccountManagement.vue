<template>
  <main class="media-accounts">
    <header class="page-hero">
      <div>
        <span class="eyebrow">CHANNEL CONTROL / ACCOUNT HEALTH</span>
        <h1>媒体账号</h1>
        <p>集中查看发布凭证状态。页面加载只读取本地记录，不会自动访问任何媒体平台。</p>
      </div>
      <div class="hero-actions">
        <span>{{ syncLabel }}</span>
        <el-button :icon="Refresh" :loading="loading" @click="fetchOverview">刷新本地状态</el-button>
        <el-button
          type="primary"
          :icon="Plus"
          :disabled="!hasLoginCapablePlatform"
          :title="hasLoginCapablePlatform ? '添加媒体账号' : '当前没有可用的平台登录运行时'"
          @click="openLogin()"
        >
          添加媒体账号
        </el-button>
      </div>
    </header>

    <section class="account-metrics" aria-label="媒体账号摘要">
      <article>
        <span>01 / TOTAL</span>
        <strong>{{ overview.summary.total }}</strong>
        <p>已录入账号</p>
      </article>
      <article class="connected">
        <span>02 / ONLINE</span>
        <strong>{{ overview.summary.connected }}</strong>
        <p>本地状态正常</p>
      </article>
      <article class="warning">
        <span>03 / ACTION</span>
        <strong>{{ overview.summary.needs_action }}</strong>
        <p>需要重新登录</p>
      </article>
    </section>

    <section class="safety-note" aria-label="账号检测说明">
      <div class="note-icon"><el-icon><Connection /></el-icon></div>
      <div>
        <strong>平台检测采用显式触发</strong>
        <p>“刷新本地状态”不联网；只有点击某个账号的“检测登录”，才会打开本机浏览器访问该平台。退出 / 解绑只移除本机凭证，不代表撤销平台网站上的其他设备会话。</p>
      </div>
    </section>

    <section v-if="capabilitiesLoadError" class="capability-warning" role="alert">
      <strong>发布能力清单暂未载入</strong>
      <p>本地账号记录仍可查看，但登录和在线检测保持禁用，避免在运行环境未知时访问平台。</p>
    </section>

    <section v-if="loadError" class="load-state" role="alert">
      <strong>媒体账号没有载入</strong>
      <p>检查后端服务后重试；Cookie 文件和账号记录不会受到影响。</p>
      <el-button type="primary" @click="fetchOverview">重新加载</el-button>
    </section>

    <template v-else>
      <section class="platform-section" aria-labelledby="primary-platforms">
        <div class="section-heading">
          <div>
            <span>GEO DISTRIBUTION / 01</span>
            <h2 id="primary-platforms">文章分发渠道</h2>
            <p>任务书优先接入的图文平台；账号未连接时仍显示能力占位。</p>
          </div>
          <small>{{ primaryPlatforms.length }} 个渠道</small>
        </div>
        <div class="platform-grid" v-loading="loading && !loaded">
          <PlatformCard
            v-for="platform in primaryPlatforms"
            :key="platform.key"
            :platform="platform"
            :checking-ids="checkingIds"
            :disconnecting-ids="disconnectingIds"
            @check="checkAccount"
            @login="openLogin"
            @disconnect="disconnectAccount"
          />
        </div>
      </section>

      <section class="platform-section secondary" aria-labelledby="video-platforms">
        <div class="section-heading">
          <div>
            <span>OMNIPOST CAPABILITY / 02</span>
            <h2 id="video-platforms">扩展图文与视频渠道</h2>
            <p>P2 渠道已接入统一任务流；真实执行仍需要本机 Worker、有效账号和人工授权。</p>
          </div>
          <small>{{ secondaryPlatforms.length }} 个渠道</small>
        </div>
        <div class="platform-grid compact">
          <PlatformCard
            v-for="platform in secondaryPlatforms"
            :key="platform.key"
            :platform="platform"
            :checking-ids="checkingIds"
            :disconnecting-ids="disconnectingIds"
            @check="checkAccount"
            @login="openLogin"
            @disconnect="disconnectAccount"
          />
        </div>
      </section>
    </template>

    <el-dialog
      v-model="loginDialogVisible"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :close-on-press-escape="loginCanClose"
      :show-close="loginCanClose"
      @closed="resetLoginDialog"
    >
      <template #header>
        <div class="dialog-heading">
          <span>AUTHORIZED LOGIN</span>
          <h2>{{ loginDialogTitle }}</h2>
        </div>
      </template>

      <div class="login-dialog">
        <el-form v-if="loginStage === 'idle'" label-position="top">
          <el-form-item label="媒体平台">
            <el-select v-model="loginForm.platformType" placeholder="选择平台" :disabled="loginStage !== 'idle'">
              <el-option
                v-for="platform in overview.platforms"
                :key="platform.type"
                :label="platform.name"
                :value="platform.type"
                :disabled="platform.login_available === false"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="账号备注名">
            <el-input
              v-model="loginForm.accountName"
              maxlength="50"
              placeholder="例如：知乎品牌主号"
              :disabled="loginStage !== 'idle'"
            />
          </el-form-item>
        </el-form>

        <div v-else class="login-session-context" aria-label="当前登录对象">
          <div><span>PLATFORM</span><strong>{{ selectedPlatform?.name || '媒体平台' }}</strong></div>
          <div><span>LOCAL LABEL</span><strong>{{ loginForm.accountName }}</strong></div>
        </div>

        <div v-if="loginStage === 'idle'" class="login-consent">
          <el-icon><WarningFilled /></el-icon>
          <p>继续后会打开对应平台的登录流程。请只操作你有权使用的账号；扫码、验证码和人工确认不会被绕过。</p>
        </div>

        <div v-else class="login-progress" :class="`stage-${loginStage}`">
          <ol class="login-stage-rail" aria-label="登录进度">
            <li :class="loginStepState(1)"><span>01</span>启动流程</li>
            <li :class="loginStepState(2)"><span>02</span>平台验证</li>
            <li :class="loginStepState(3)"><span>03</span>保存账号</li>
          </ol>

          <div class="login-visual">
            <img v-if="qrCodeData" :src="qrCodeData" alt="平台登录二维码" />
            <div v-else class="progress-symbol" :class="loginStage">
              <el-icon v-if="loginStage === 'success'"><CircleCheckFilled /></el-icon>
              <span v-else>{{ loginStage === 'failed' ? '!' : (loginStage === 'review' ? '?' : '•••') }}</span>
            </div>
          </div>

          <div class="login-status-copy">
            <div class="login-status-meta">
              <span>{{ loginStageCode }}</span>
              <time>{{ loginElapsedLabel }}</time>
            </div>
            <strong>{{ loginStageTitle }}</strong>
            <p>{{ loginStageMessage }}</p>
          </div>

          <aside
            v-if="['opening', 'manual'].includes(loginStage) && !qrCodeData"
            class="login-window-tip"
            role="status"
          >
            <strong>请查看桌面和任务栏</strong>
            <p>软件会另外打开{{ loginWindowName }}。登录窗口可能被挡在本软件后面；请切换过去完成{{ selectedPlatform?.name || '平台' }}验证，并保持该窗口打开。</p>
          </aside>

          <aside v-if="loginStage === 'failed'" class="login-error-detail" role="alert">
            <span>{{ loginFailure.code === 'operator_cancelled' ? 'STATUS' : 'ERROR' }} / {{ loginFailure.code }}</span>
            <strong>{{ loginFailure.message }}</strong>
            <p><b>下一步：</b>{{ loginFailure.action }}</p>
          </aside>
          <aside v-if="loginStage === 'review'" class="login-review-detail" role="status">
            <span>STATUS / OUTCOME UNKNOWN</span>
            <strong>{{ loginFailure.message }}</strong>
            <p>{{ loginFailure.action }}</p>
          </aside>
        </div>
      </div>

      <template #footer>
        <el-button v-if="loginStage === 'idle'" @click="loginDialogVisible = false">取消</el-button>
        <el-button
          v-if="loginStage === 'idle'"
          type="primary"
          :disabled="!loginForm.platformType || !loginForm.accountName.trim()"
          @click="startLogin"
        >
          打开登录流程
        </el-button>
        <template v-else-if="loginStage === 'failed'">
          <el-button @click="loginDialogVisible = false">关闭</el-button>
          <el-button
            v-if="loginFailure.code === 'cancel_pending'"
            type="primary"
            :loading="loginCancelPending"
            @click="checkLoginCancellation"
          >
            检查关闭状态
          </el-button>
          <el-button v-else type="primary" @click="retryLogin">按提示重试</el-button>
        </template>
        <template v-else-if="loginStage === 'review'">
          <el-button @click="loginDialogVisible = false">关闭</el-button>
          <el-button type="primary" :loading="loading" @click="refreshLoginStatus">刷新账号状态</el-button>
        </template>
        <el-button v-else-if="loginStage === 'success'" type="primary" @click="completeLogin">
          {{ returnToPublishCenter ? '返回发布中心继续' : '完成' }}
        </el-button>
        <template v-else>
          <el-button :disabled="loginCancelPending" @click="cancelLogin">
            {{ loginCancelPending ? '正在结束登录…' : '结束本次登录' }}
          </el-button>
          <el-button type="primary" disabled>等待平台确认</el-button>
        </template>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CircleCheckFilled, Connection, Plus, Refresh, WarningFilled } from '@element-plus/icons-vue'
import { ElButton, ElIcon, ElMessage, ElMessageBox } from 'element-plus'
import { accountApi } from '@/api/account'
import { publishApi } from '@/api/publish'
import { apiUrl } from '@/config/api'
import {
  buildStreamFailure,
  parseLoginSessionEvent,
  planLoginStreamReconnect,
  resolveLoginCancellation
} from '@/utils/loginSession'

const route = useRoute()
const router = useRouter()

const emptyOverview = () => ({
  summary: { total: 0, connected: 0, needs_action: 0, checking: 0 },
  platforms: [],
  accounts: []
})

const statusCopy = {
  connected: { label: '已连接', note: '本地 Cookie 状态正常' },
  expired: { label: '已失效', note: '需要重新登录' },
  missing_cookie: { label: '缺少 Cookie', note: '凭证文件不存在' },
  checking: { label: '检测中', note: '正在确认平台状态' }
}

const PlatformCard = defineComponent({
  name: 'PlatformCard',
  props: {
    platform: { type: Object, required: true },
    checkingIds: { type: Object, required: true },
    disconnectingIds: { type: Object, required: true }
  },
  emits: ['check', 'login', 'disconnect'],
  setup(props, { emit }) {
    const formatCheckedAt = value => {
      if (!value) return '尚未检测'
      const text = String(value)
      const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
        ? `${text.replace(' ', 'T')}Z`
        : text
      const date = new Date(normalized)
      if (Number.isNaN(date.getTime())) return '时间未知'
      return new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
      }).format(date)
    }

    return () => h('article', { class: ['platform-card', { online: props.platform.connected_count > 0 }] }, [
      h('header', { class: 'platform-card-head' }, [
        h('div', { class: 'platform-code' }, props.platform.name.slice(0, 1)),
        h('div', [
          h('span', `${props.platform.priority} / ${String(props.platform.type).padStart(2, '0')}`),
          h('h3', props.platform.name)
        ]),
        h('small', props.platform.login_available === false
          ? 'RUNTIME OFF'
          : props.platform.connected_count ? `${props.platform.connected_count} ONLINE` : 'OFFLINE')
      ]),
      props.platform.login_available === false
        ? h('div', { class: 'platform-runtime-note', role: 'note' }, [
            h('strong', '平台登录不可用'),
            h('span', props.platform.unavailable_reason || '本机发布运行时尚未就绪')
          ])
        : null,
          props.platform.accounts.length
        ? h('div', { class: 'account-stack' }, props.platform.accounts.map(account => {
            const copy = statusCopy[account.status] || { label: account.status, note: '状态未知' }
            const checking = props.checkingIds.has(account.id)
            const disconnecting = props.disconnectingIds.has(account.id)
            const lifecycleBusy = checking || disconnecting
            return h('section', { class: 'account-entry', key: account.id }, [
              h('div', { class: 'account-identity' }, [
                h('strong', `${account.account_name} · ID ${account.id}`),
                h('span', { class: ['status-chip', account.status] }, copy.label),
                h('p', copy.note),
                h('time', `最后检查：${formatCheckedAt(account.last_checked_at)}`)
              ]),
              h('div', { class: 'account-actions' }, [
                h(ElButton, {
                  size: 'small',
                  loading: checking,
                  disabled: props.platform.login_available === false || disconnecting,
                  onClick: () => emit('check', account, props.platform)
                }, () => props.platform.login_available === false ? '检测不可用' : '检测登录'),
                h(ElButton, {
                  size: 'small', type: 'primary', plain: true,
                  disabled: props.platform.login_available === false || lifecycleBusy,
                  onClick: () => emit('login', props.platform, account)
                }, () => '重新登录'),
                h(ElButton, {
                  size: 'small', type: 'danger', plain: true,
                  loading: disconnecting,
                  disabled: checking,
                  onClick: () => emit('disconnect', account, props.platform)
                }, () => '退出 / 解绑')
              ])
            ])
          }))
        : h('div', { class: 'platform-empty' }, [
            h('p', '尚未连接账号'),
            h(ElButton, {
              size: 'small', type: 'primary', plain: true,
              disabled: props.platform.login_available === false,
              onClick: () => emit('login', props.platform)
            }, () => props.platform.login_available === false ? '运行时不可用' : '添加 / 登录')
          ])
    ])
  }
})

const overview = reactive(emptyOverview())
const loading = ref(false)
const loaded = ref(false)
const loadError = ref(false)
const capabilitiesLoadError = ref(false)
const lastSyncedAt = ref(null)
const checkingIds = ref(new Set())
const disconnectingIds = ref(new Set())
const loginDialogVisible = ref(false)
const loginStage = ref('idle')
const qrCodeData = ref('')
const loginEventMessage = ref('')
const loginElapsedSeconds = ref(0)
const loginCancelPending = ref(false)
const loginStreamReconnectAttempt = ref(0)
const loginStreamRecovering = ref(false)
const loginFailure = reactive({ code: '', message: '', action: '' })
const loginForm = reactive({ platformType: null, accountName: '', existingAccountId: null })
let eventSource = null
let loginTimer = null
let loginReconnectTimer = null
let loginStreamBaseUrl = ''
let loginClientSessionId = ''
let lastLoginEventId = 0

const primaryPlatforms = computed(() => overview.platforms.filter(item => ['P0', 'P1'].includes(item.priority)))
const secondaryPlatforms = computed(() => overview.platforms.filter(item => !['P0', 'P1'].includes(item.priority)))
const syncLabel = computed(() => lastSyncedAt.value
  ? `本地同步 ${lastSyncedAt.value.toLocaleTimeString('zh-CN', { hour12: false })}`
  : '等待首次同步')
const selectedPlatform = computed(() => overview.platforms.find(item => item.type === loginForm.platformType))
const hasLoginCapablePlatform = computed(() => (
  overview.platforms.some(item => item.login_available === true)
))
const returnToPublishCenter = computed(() => route.query.return_to === '/publish-center')
const loginDialogTitle = computed(() => `${loginForm.existingAccountId ? '重新登录' : '连接'}${selectedPlatform.value ? ` · ${selectedPlatform.value.name}` : '媒体账号'}`)
const loginWindowName = computed(() => (
  selectedPlatform.value?.key === 'bilibili' ? ' Bilibili 扫码窗口' : ' Google Chrome'
))
const loginStageTitle = computed(() => {
  if (loginStreamRecovering.value) return '登录状态连接正在恢复'
  if (loginStage.value === 'failed' && loginFailure.code === 'operator_cancelled') {
    return '已请求结束本次登录'
  }
  return ({
    connecting: '正在连接本机登录服务',
    opening: '正在打开平台登录窗口',
    manual: `请完成${selectedPlatform.value?.name || '平台'}验证`,
    success: '账号登录成功',
    failed: '本次登录未完成',
    review: '请确认账号状态'
  }[loginStage.value] || '准备登录')
})
const loginStageMessage = computed(() => {
  if (loginStreamRecovering.value) return loginEventMessage.value
  if (loginStage.value === 'success') return loginEventMessage.value || '本地账号和登录凭证已经保存，可以继续发布流程。'
  if (loginStage.value === 'failed' && loginFailure.code === 'operator_cancelled') return '本次未完成的登录信息不会保存。'
  if (loginStage.value === 'review') return '结果无法可靠判定，软件已刷新本地账号列表。'
  if (loginStage.value === 'failed') return '软件没有执行任何绕过验证的操作；请按下方诊断处理后重试。'
  if (qrCodeData.value) return '请使用对应平台 App 扫描二维码，并在平台侧完成确认。'
  if (loginEventMessage.value) return loginEventMessage.value
  if (loginStage.value === 'manual') return '系统正在等待浏览器中的扫码、验证码或人工确认结果。'
  if (loginStage.value === 'opening') {
    return selectedPlatform.value?.key === 'bilibili'
      ? '本机服务已连接，正在打开 Bilibili 扫码登录窗口。完成扫码前软件会持续等待。'
      : '本机服务已连接，正在调用 Google Chrome 打开平台登录页。'
  }
  return '正在申请本机浏览器登录会话。'
})
const loginStageCode = computed(() => {
  if (loginStreamRecovering.value) {
    return `STREAM / RETRY ${loginStreamReconnectAttempt.value}`
  }
  return ({
    connecting: 'SESSION / CONNECTING',
    opening: 'BROWSER / OPENING',
    manual: 'PLATFORM / WAITING',
    success: 'ACCOUNT / SAVED',
    failed: `LOGIN / ${loginFailure.code || 'FAILED'}`,
    review: 'ACCOUNT / VERIFY'
  }[loginStage.value] || 'LOGIN / READY')
})
const loginElapsedLabel = computed(() => {
  const minutes = Math.floor(loginElapsedSeconds.value / 60)
  const seconds = loginElapsedSeconds.value % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
})
const loginCurrentStep = computed(() => {
  if (['connecting', 'opening'].includes(loginStage.value)) return 1
  if (loginStage.value === 'failed') {
    if (['browser_launch_failed', 'login_page_unavailable', 'stream_disconnected'].includes(loginFailure.code)) return 1
    if (loginFailure.code === 'session_save_failed') return 3
    return 2
  }
  if (loginStage.value === 'manual' || qrCodeData.value) return 2
  if (loginStage.value === 'review') return 3
  if (loginStage.value === 'success') return 3
  return 0
})
const loginCanClose = computed(() => ['idle', 'success', 'failed', 'review'].includes(loginStage.value))
const loginStepState = step => ({
  active: loginCurrentStep.value === step,
  complete: loginCurrentStep.value > step || loginStage.value === 'success',
  failed: loginStage.value === 'failed' && step === loginCurrentStep.value
})

const fetchOverview = async () => {
  loading.value = true
  loadError.value = false
  capabilitiesLoadError.value = false
  try {
    const [accountResult, capabilityResult] = await Promise.allSettled([
      accountApi.getMediaAccounts(),
      publishApi.getPlatforms()
    ])
    if (accountResult.status === 'rejected') throw accountResult.reason

    const capabilityItems = capabilityResult.status === 'fulfilled'
      ? (capabilityResult.value.data || [])
      : []
    capabilitiesLoadError.value = capabilityResult.status === 'rejected'
    if (capabilitiesLoadError.value) {
      console.error('加载发布能力清单失败:', capabilityResult.reason)
    }
    const capabilities = new Map(capabilityItems.map(item => [item.key, item]))
    const rawOverview = accountResult.value.data || emptyOverview()
    const nextOverview = {
      ...rawOverview,
      summary: { ...emptyOverview().summary, ...(rawOverview.summary || {}) },
      accounts: [...(rawOverview.accounts || [])],
      platforms: (rawOverview.platforms || []).map(platform => ({
        ...platform,
        login_available: false,
        runtime_available: false,
        unavailable_reason: capabilitiesLoadError.value
          ? '发布能力清单未载入，登录与检测已安全禁用'
          : '当前构建未开放该平台登录',
        ...(capabilities.get(platform.key) || {})
      }))
    }
    Object.assign(overview, nextOverview)
    loaded.value = true
    lastSyncedAt.value = new Date()
    const requestedPlatform = String(route.query.login_platform || '')
    if (requestedPlatform && !loginDialogVisible.value) {
      const platform = overview.platforms.find(item => item.key === requestedPlatform)
      if (platform) {
        openLogin(platform)
        await router.replace({
          path: '/account-management',
          query: {
            ...(route.query.return_to ? { return_to: route.query.return_to } : {}),
            ...(route.query.job_id ? { job_id: route.query.job_id } : {})
          }
        })
      }
    }
  } catch (error) {
    loadError.value = true
    console.error('加载媒体账号失败:', error)
  } finally {
    loading.value = false
  }
}

const checkAccount = async (account, platform) => {
  if (platform?.login_available !== true) {
    ElMessage.warning(platform?.unavailable_reason || '该平台登录运行时当前不可用')
    return
  }
  if (checkingIds.value.has(account.id) || disconnectingIds.value.has(account.id)) return
  checkingIds.value = new Set([...checkingIds.value, account.id])
  try {
    const response = await accountApi.checkMediaAccount(account.id)
    ElMessage.success(response.data?.check_message || '登录状态检测完成')
    await fetchOverview()
  } catch (error) {
    ElMessage.error(error?.message || '平台状态检测失败，请稍后重试')
  } finally {
    const next = new Set(checkingIds.value)
    next.delete(account.id)
    checkingIds.value = next
  }
}

const openLogin = (platform = null, account = null) => {
  if (platform && platform.login_available !== true) {
    ElMessage.warning(platform.unavailable_reason || '该平台登录运行时当前不可用')
    return
  }
  if (!platform && !hasLoginCapablePlatform.value) {
    ElMessage.warning('当前没有可用的平台登录运行时')
    return
  }
  if (
    account
    && (checkingIds.value.has(account.id) || disconnectingIds.value.has(account.id))
  ) return
  loginForm.platformType = platform?.type || null
  loginForm.accountName = account?.account_name || ''
  loginForm.existingAccountId = account?.id || null
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginEventMessage.value = ''
  loginCancelPending.value = false
  loginStreamReconnectAttempt.value = 0
  loginStreamRecovering.value = false
  loginStreamBaseUrl = ''
  loginClientSessionId = ''
  lastLoginEventId = 0
  Object.assign(loginFailure, { code: '', message: '', action: '' })
  loginDialogVisible.value = true
}

const disconnectAccount = async (account, platform) => {
  if (disconnectingIds.value.has(account.id) || checkingIds.value.has(account.id)) return
  disconnectingIds.value = new Set([...disconnectingIds.value, account.id])
  try {
    try {
      await ElMessageBox.confirm(
        `将从本机移除“${account.account_name}”的登录凭证，并解除待发布任务的账号绑定。已完成和失败的发布历史仍保留账号名称。此操作不会注销${platform.name}网站上的其他设备会话。`,
        `退出并解绑${platform.name}账号？`,
        {
          confirmButtonText: '退出并解绑',
          cancelButtonText: '保留账号',
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
    } catch {
      return
    }

    try {
      const response = await accountApi.disconnectMediaAccount(account.id)
      if (response.data?.cleanup_pending) {
        ElMessage.warning(response.msg || '账号已禁用，但本机凭证文件仍待清理')
      } else {
        ElMessage.success(response.msg || '已从本机退出并解绑媒体账号')
      }
      await fetchOverview()
    } catch (error) {
      ElMessage.error(error?.response?.data?.msg || '无法确认解绑结果，请刷新账号列表后重试')
    }
  } finally {
    const next = new Set(disconnectingIds.value)
    next.delete(account.id)
    disconnectingIds.value = next
  }
}

const stopLoginTimer = () => {
  if (loginTimer) window.clearInterval(loginTimer)
  loginTimer = null
}

const startLoginTimer = () => {
  stopLoginTimer()
  const startedAt = Date.now()
  loginElapsedSeconds.value = 0
  loginTimer = window.setInterval(() => {
    loginElapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
  }, 1000)
}

const clearLoginReconnectTimer = () => {
  if (loginReconnectTimer) window.clearTimeout(loginReconnectTimer)
  loginReconnectTimer = null
}

const createLoginClientSessionId = () => {
  if (typeof window.crypto?.randomUUID === 'function') {
    return window.crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  window.crypto.getRandomValues(bytes)
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
}

const loginCancellationPayload = () => ({
  type: loginForm.platformType,
  id: loginForm.accountName.trim(),
  ...(loginClientSessionId ? { session_id: loginClientSessionId } : {})
})

const finishLoginWithFailure = failure => {
  if (
    failure.code === 'login_not_completed'
    && loginFailure.code
    && loginFailure.code !== 'login_not_completed'
  ) return
  Object.assign(loginFailure, {
    code: failure.code || 'unexpected_error',
    message: failure.message || '平台登录流程没有完成。',
    action: failure.action || '关闭残留登录窗口后重新尝试。'
  })
  loginCancelPending.value = false
  loginStreamRecovering.value = false
  loginStage.value = 'failed'
  stopLoginTimer()
  closeEventSource()
}

const finishLoginWithSuccess = message => {
  loginStage.value = 'success'
  loginEventMessage.value = message || '账号登录成功，本地凭证已经保存。'
  loginCancelPending.value = false
  loginStreamRecovering.value = false
  Object.assign(loginFailure, { code: '', message: '', action: '' })
  stopLoginTimer()
  closeEventSource()
  fetchOverview()
}

const finishLoginWithReview = resolution => {
  Object.assign(loginFailure, {
    code: resolution.code || 'outcome_unknown',
    message: resolution.message || '登录流程已结束，请刷新确认账号状态。',
    action: resolution.action || '查看媒体账号列表确认当前状态。'
  })
  loginCancelPending.value = false
  loginStreamRecovering.value = false
  loginStage.value = 'review'
  stopLoginTimer()
  closeEventSource()
  fetchOverview()
}

const applyLoginEvent = rawValue => {
  loginStreamReconnectAttempt.value = 0
  loginStreamRecovering.value = false
  const loginEvent = parseLoginSessionEvent(rawValue)
  if (loginEvent.kind === 'stage') {
    loginStage.value = loginEvent.stage
    loginEventMessage.value = loginEvent.message || ''
    return
  }
  if (loginEvent.kind === 'qr_code') {
    qrCodeData.value = loginEvent.dataUrl
    loginStage.value = 'manual'
    loginEventMessage.value = loginEvent.message || ''
    return
  }
  if (loginEvent.kind === 'success') {
    finishLoginWithSuccess(loginEvent.message)
    return
  }
  if (loginEvent.kind === 'failure') {
    finishLoginWithFailure(loginEvent)
    return
  }
  // Do not echo unknown server payloads: they may contain HTML, diagnostics or
  // other data that must not be mistaken for a QR code or shown to operators.
  console.warn('忽略无法识别的登录状态事件')
}

const finalizeDisconnectedLogin = async () => {
  if (['success', 'failed', 'review'].includes(loginStage.value)) return
  loginStreamRecovering.value = false
  loginCancelPending.value = true
  loginEventMessage.value = '状态连接多次重试仍未恢复，正在安全结束本次登录流程…'
  const failure = buildStreamFailure()
  try {
    const response = await accountApi.cancelMediaLogin(loginCancellationPayload())
    const resolution = resolveLoginCancellation(response)
    if (resolution.kind === 'success') {
      finishLoginWithSuccess(resolution.message)
      return
    }
    if (resolution.kind === 'pending') {
      failure.action = `${failure.action} ${resolution.action}`
    } else {
      failure.message = `${failure.message} 后台登录流程已结束。`
    }
  } catch {
    failure.action = `${failure.action} 若${loginWindowName.value.trim()}仍打开，请手动关闭。`
  }
  finishLoginWithFailure(failure)
}

const scheduleLoginStreamReconnect = source => {
  if (source && eventSource !== source) return
  if (source) source.close()
  if (eventSource === source) eventSource = null
  if (['success', 'failed', 'review'].includes(loginStage.value)) return

  const recovery = planLoginStreamReconnect(loginStreamReconnectAttempt.value)
  if (recovery.exhausted) {
    void finalizeDisconnectedLogin()
    return
  }

  loginStreamReconnectAttempt.value = recovery.attempt
  loginStreamRecovering.value = true
  loginEventMessage.value = `状态连接短暂中断，${Math.ceil(recovery.delayMs / 1000)} 秒后进行第 ${recovery.attempt}/${recovery.maxAttempts} 次重连；平台登录窗口请保持打开。`
  clearLoginReconnectTimer()
  loginReconnectTimer = window.setTimeout(() => {
    loginReconnectTimer = null
    if (!['success', 'failed', 'review'].includes(loginStage.value)) {
      connectLoginEventStream()
    }
  }, recovery.delayMs)
}

const connectLoginEventStream = () => {
  const resumeQuery = lastLoginEventId > 0 ? `&last_event_id=${lastLoginEventId}` : ''
  const url = `${loginStreamBaseUrl}&session_id=${encodeURIComponent(loginClientSessionId)}${resumeQuery}`
  const source = (() => {
    try {
      return new EventSource(url, { withCredentials: true })
    } catch {
      return null
    }
  })()
  if (!source) {
    scheduleLoginStreamReconnect(null)
    return
  }
  eventSource = source
  source.onopen = () => {
    if (eventSource !== source) return
    if (loginStage.value === 'connecting') loginStage.value = 'opening'
  }
  source.onmessage = event => {
    if (eventSource !== source) return
    const receivedEventId = Number(event.lastEventId)
    if (Number.isInteger(receivedEventId) && receivedEventId > lastLoginEventId) {
      lastLoginEventId = receivedEventId
    }
    if (loginStreamRecovering.value) {
      loginEventMessage.value = '状态连接已恢复，继续等待平台确认。'
    }
    applyLoginEvent(event.data)
  }
  source.onerror = () => {
    if (eventSource !== source) return
    scheduleLoginStreamReconnect(source)
  }
}

const startLogin = () => {
  if (!selectedPlatform.value || selectedPlatform.value.login_available !== true) {
    ElMessage.warning(selectedPlatform.value?.unavailable_reason || '该平台登录运行时当前不可用')
    return
  }
  closeEventSource()
  loginStage.value = 'connecting'
  qrCodeData.value = ''
  loginEventMessage.value = ''
  loginCancelPending.value = false
  loginStreamReconnectAttempt.value = 0
  loginStreamRecovering.value = false
  lastLoginEventId = 0
  loginClientSessionId = createLoginClientSessionId()
  Object.assign(loginFailure, { code: '', message: '', action: '' })
  startLoginTimer()
  const existingAccountId = Number(loginForm.existingAccountId)
  const existingAccountQuery = Number.isInteger(existingAccountId) && existingAccountId > 0
    ? `&existing_account_id=${existingAccountId}`
    : ''
  loginStreamBaseUrl = apiUrl(`/login?type=${loginForm.platformType}&id=${encodeURIComponent(loginForm.accountName.trim())}&protocol=2${existingAccountQuery}`)
  connectLoginEventStream()
}

const retryLogin = () => {
  closeEventSource()
  stopLoginTimer()
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginEventMessage.value = ''
  loginElapsedSeconds.value = 0
  loginCancelPending.value = false
  loginStreamReconnectAttempt.value = 0
  loginStreamRecovering.value = false
  loginStreamBaseUrl = ''
  loginClientSessionId = ''
  lastLoginEventId = 0
  Object.assign(loginFailure, { code: '', message: '', action: '' })
}

const cancelLogin = async () => {
  try {
    await ElMessageBox.confirm(
      `软件将请求结束本次登录，并以后台返回的最终结果为准；若${loginWindowName.value}仍然打开，请手动关闭。`,
      '结束本次登录？',
      {
        confirmButtonText: '结束本次登录',
        cancelButtonText: '继续等待',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  await requestLoginCancellation()
}

const applyCancellationResponse = response => {
  const resolution = resolveLoginCancellation(response)
  if (resolution.kind === 'success') {
    finishLoginWithSuccess(resolution.message)
    return
  }
  if (resolution.kind === 'cancelled') {
    finishLoginWithFailure(resolution)
    return
  }
  if (resolution.kind === 'pending') {
    finishLoginWithFailure({
      ...resolution,
      action: `${resolution.action} 然后点击“检查关闭状态”。`
    })
    return
  }
  if (resolution.kind === 'failure') {
    finishLoginWithFailure(resolution)
    return
  }
  finishLoginWithReview(resolution)
}

const requestLoginCancellation = async () => {
  clearLoginReconnectTimer()
  loginStreamRecovering.value = false
  loginCancelPending.value = true
  loginEventMessage.value = `正在请求后台结束登录并关闭${loginWindowName.value}…`
  try {
    const response = await accountApi.cancelMediaLogin(loginCancellationPayload())
    applyCancellationResponse(response)
  } catch (error) {
    loginCancelPending.value = false
    loginEventMessage.value = '结束请求没有送达，当前登录仍在等待平台结果。'
    ElMessage.error(error?.response?.data?.msg || '结束登录失败，请继续等待或稍后重试')
  }
}

const checkLoginCancellation = async () => {
  await requestLoginCancellation()
}

const refreshLoginStatus = async () => {
  await fetchOverview()
  ElMessage.success('账号状态已刷新，请核对媒体账号列表')
}

const completeLogin = async () => {
  loginDialogVisible.value = false
  if (returnToPublishCenter.value) {
    await router.push({
      path: '/publish-center',
      query: route.query.job_id ? { job_id: route.query.job_id } : {}
    })
  }
}

const closeEventSource = (source = eventSource) => {
  clearLoginReconnectTimer()
  if (!source) return
  source.close()
  if (eventSource === source) eventSource = null
}

const resetLoginDialog = () => {
  closeEventSource()
  stopLoginTimer()
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginEventMessage.value = ''
  loginElapsedSeconds.value = 0
  loginCancelPending.value = false
  loginStreamReconnectAttempt.value = 0
  loginStreamRecovering.value = false
  loginStreamBaseUrl = ''
  loginClientSessionId = ''
  lastLoginEventId = 0
  Object.assign(loginFailure, { code: '', message: '', action: '' })
  loginForm.platformType = null
  loginForm.accountName = ''
  loginForm.existingAccountId = null
}

onMounted(fetchOverview)
onBeforeUnmount(() => {
  if (['connecting', 'opening', 'manual'].includes(loginStage.value)) {
    void accountApi.cancelMediaLogin(loginCancellationPayload()).catch(() => {})
  }
  closeEventSource()
  stopLoginTimer()
})
</script>

<style lang="scss" scoped>
.media-accounts {
  --ink: #102e34;
  --muted: #6e7f84;
  --rule: #d3ddda;
  --teal: #28b8b0;
  --paper: #fff;
  max-width: 1180px;
  margin: 0 auto;
  color: var(--ink);
}

.page-hero {
  display: flex;
  justify-content: space-between;
  gap: 32px;
  align-items: flex-end;
  padding: 26px 4px 24px;
  border-bottom: 1px solid var(--rule);
}

.eyebrow, .section-heading span, .platform-card-head span, .dialog-heading span {
  color: #087f81;
  font: 700 10px/1.2 "Cascadia Mono", monospace;
  letter-spacing: .16em;
}

.page-hero h1 { margin: 5px 0 6px; font: 800 48px/1 "Microsoft YaHei", sans-serif; letter-spacing: -.06em; }
.page-hero p { max-width: 660px; margin: 0; color: var(--muted); font-size: 14px; line-height: 1.7; }
.hero-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.hero-actions > span { width: 100%; color: var(--muted); font: 10px "Cascadia Mono", monospace; text-align: right; }

.account-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: 22px 0;
  border: 1px solid var(--rule);
  background: var(--paper);
}

.account-metrics article { position: relative; padding: 18px 22px; border-right: 1px solid var(--rule); }
.account-metrics article:last-child { border-right: 0; }
.account-metrics article::after { content: ""; position: absolute; inset: auto 20px 0; height: 3px; background: #8fb7b4; }
.account-metrics article.connected::after { background: var(--teal); }
.account-metrics article.warning::after { background: #d28d34; }
.account-metrics span { color: #578083; font: 700 9px "Cascadia Mono", monospace; letter-spacing: .1em; }
.account-metrics strong { float: right; font: 800 36px/.9 "Bahnschrift SemiCondensed", sans-serif; }
.account-metrics p { margin: 12px 0 0; font-weight: 700; }

.safety-note { display: flex; gap: 14px; align-items: flex-start; padding: 16px 18px; border-left: 3px solid var(--teal); background: #eaf4f2; }
.note-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; border: 1px solid #80bbb7; color: #087f81; }
.safety-note strong { font-size: 13px; }
.safety-note p { margin: 4px 0 0; color: #597177; font-size: 12px; line-height: 1.6; }
.capability-warning { margin-top: 12px; padding: 13px 16px; border: 1px solid #ddc49d; background: #fff9ef; }
.capability-warning strong { color: #6f5027; font-size: 12px; }
.capability-warning p { margin: 4px 0 0; color: #7b684d; font-size: 11px; line-height: 1.55; }

.platform-section { margin-top: 28px; }
.platform-section.secondary { padding-top: 8px; }
.section-heading { display: flex; justify-content: space-between; gap: 20px; align-items: flex-end; margin-bottom: 14px; }
.section-heading h2 { margin: 4px 0 3px; font-size: 24px; letter-spacing: -.04em; }
.section-heading p { margin: 0; color: var(--muted); font-size: 12px; }
.section-heading > small { color: var(--muted); font: 10px "Cascadia Mono", monospace; }

.platform-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.platform-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
:deep(.platform-card) { min-width: 0; border: 1px solid var(--rule); background: var(--paper); }
:deep(.platform-card.online) { border-top-color: var(--teal); }
:deep(.platform-card-head) { display: grid; grid-template-columns: 42px 1fr auto; gap: 12px; align-items: center; padding: 16px 18px; border-bottom: 1px solid var(--rule); }
:deep(.platform-code) { display: grid; place-items: center; width: 40px; height: 40px; color: white; background: var(--ink); font-weight: 800; }
:deep(.platform-card-head h3) { margin: 3px 0 0; font-size: 18px; }
:deep(.platform-card-head small) { color: #719093; font: 700 9px "Cascadia Mono", monospace; }
:deep(.platform-runtime-note) { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; padding: 10px 18px; border-bottom: 1px solid #ecd9b8; background: #fff9ef; }
:deep(.platform-runtime-note strong) { color: #735126; font-size: 10px; }
:deep(.platform-runtime-note span) { color: #806d51; font-size: 10px; line-height: 1.5; }
:deep(.account-stack) { padding: 0 18px; }
:deep(.account-entry) { display: flex; justify-content: space-between; gap: 16px; padding: 15px 0; border-bottom: 1px solid #e1e8e6; }
:deep(.account-entry:last-child) { border-bottom: 0; }
:deep(.account-identity strong) { margin-right: 8px; font-size: 13px; }
:deep(.status-chip) { display: inline-flex; padding: 3px 6px; color: #637578; background: #e8eeec; font-size: 9px; font-weight: 700; }
:deep(.status-chip.connected) { color: #08746f; background: #d9f2ee; }
:deep(.status-chip.expired), :deep(.status-chip.missing_cookie) { color: #a06119; background: #f8ead7; }
:deep(.account-identity p) { margin: 7px 0 2px; color: #5e7378; font-size: 11px; }
:deep(.account-identity time) { color: #839194; font: 9px "Cascadia Mono", monospace; }
:deep(.account-actions) { display: flex; align-items: center; justify-content: flex-end; gap: 6px; flex: 0 0 auto; flex-wrap: wrap; max-width: 250px; }
:deep(.account-actions .el-button + .el-button) { margin-left: 0; }
:deep(.platform-empty) { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 62px; padding: 13px 18px; }
:deep(.platform-empty p) { margin: 0; color: var(--muted); font-size: 12px; }

.load-state { margin-top: 28px; padding: 32px; text-align: center; border: 1px solid var(--rule); background: white; }
.load-state p { color: var(--muted); }
.dialog-heading h2 { margin: 4px 0 0; color: var(--ink); font-size: 23px; }
.login-dialog :deep(.el-select) { width: 100%; }
.login-session-context { display: grid; grid-template-columns: 1fr 1fr; margin-bottom: 10px; border: 1px solid var(--rule); background: white; }
.login-session-context > div { display: grid; gap: 4px; min-width: 0; padding: 10px 12px; border-right: 1px solid var(--rule); }
.login-session-context > div:last-child { border-right: 0; }
.login-session-context span { color: #718689; font: 700 8px "Cascadia Mono", monospace; letter-spacing: .12em; }
.login-session-context strong { overflow: hidden; color: var(--ink); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.login-consent { display: flex; gap: 10px; padding: 13px; color: #74552d; background: #fbf2e5; }
.login-consent p { margin: 0; font-size: 12px; line-height: 1.6; }
.login-progress { min-height: 290px; padding: 18px; border: 1px solid var(--rule); background: #f5f8f7; }
.login-stage-rail { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; width: 100%; margin: 0 0 22px; padding: 0; list-style: none; }
.login-stage-rail li { position: relative; display: grid; gap: 4px; padding-top: 9px; color: #8b999b; border-top: 2px solid #ccd7d5; font-size: 10px; }
.login-stage-rail li span { font: 700 9px "Cascadia Mono", monospace; letter-spacing: .08em; }
.login-stage-rail li.active { color: #087f81; border-top-color: var(--teal); font-weight: 700; }
.login-stage-rail li.complete { color: #496b6d; border-top-color: #78aaa6; }
.login-stage-rail li.failed { color: #9b3e36; border-top-color: #bd594f; }
.login-visual { display: grid; place-items: center; }
.login-visual img { width: 180px; height: 180px; padding: 8px; object-fit: contain; border: 1px solid #d2ddda; background: white; }
.login-status-copy { margin: 15px auto 0; max-width: 390px; text-align: left; }
.login-status-meta { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 7px; color: #087f81; font: 700 9px "Cascadia Mono", monospace; letter-spacing: .1em; }
.login-status-meta time { color: #728689; letter-spacing: 0; }
.login-status-copy > strong { display: block; color: var(--ink); font-size: 15px; }
.login-status-copy > p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.progress-symbol { display: grid; place-items: center; width: 82px; height: 82px; color: white; background: var(--ink); font: 700 18px "Cascadia Mono", monospace; }
.progress-symbol.success { background: #168f87; font-size: 34px; }
.progress-symbol.failed { background: #bd594f; font-size: 34px; }
.progress-symbol.review { background: #b27b32; font-size: 30px; }
.progress-symbol.connecting span, .progress-symbol.opening span, .progress-symbol.manual span { animation: login-pulse 1.2s ease-in-out infinite; }
.login-window-tip, .login-error-detail, .login-review-detail { max-width: 390px; margin: 14px auto 0; padding: 12px 13px; text-align: left; }
.login-window-tip { color: #315b5d; border-left: 3px solid var(--teal); background: #e4f2f0; }
.login-window-tip strong { font-size: 11px; }
.login-window-tip p { margin: 4px 0 0; color: #537174; font-size: 11px; line-height: 1.55; }
.login-error-detail { border: 1px solid #dfb3ae; background: #fff3f1; }
.login-error-detail > span { color: #a3473e; font: 700 9px "Cascadia Mono", monospace; letter-spacing: .08em; text-transform: uppercase; }
.login-error-detail > strong { display: block; margin-top: 7px; color: #72342e; font-size: 12px; line-height: 1.55; }
.login-error-detail p { margin: 7px 0 0; color: #79504c; font-size: 11px; line-height: 1.55; }
.login-review-detail { border: 1px solid #ddc49d; background: #fff9ef; }
.login-review-detail > span { color: #8b632d; font: 700 9px "Cascadia Mono", monospace; letter-spacing: .08em; }
.login-review-detail > strong { display: block; margin-top: 7px; color: #65491f; font-size: 12px; }
.login-review-detail p { margin: 7px 0 0; color: #735f43; font-size: 11px; line-height: 1.55; }

@keyframes login-pulse {
  0%, 100% { opacity: .35; letter-spacing: .08em; }
  50% { opacity: 1; letter-spacing: .2em; }
}

@media (prefers-reduced-motion: reduce) {
  .progress-symbol.connecting span, .progress-symbol.opening span, .progress-symbol.manual span { animation: none; }
}

@media (max-width: 860px) {
  .page-hero { align-items: flex-start; flex-direction: column; }
  .hero-actions { justify-content: flex-start; }
  .hero-actions > span { text-align: left; }
  .platform-grid, .platform-grid.compact { grid-template-columns: 1fr; }
}

@media (max-width: 560px) {
  .media-accounts { width: 100%; }
  .page-hero { padding-top: 10px; }
  .page-hero h1 { font-size: 39px; }
  .hero-actions { width: 100%; }
  .hero-actions .el-button { flex: 1; margin-left: 0; }
  .account-metrics { grid-template-columns: 1fr 1fr; }
  .account-metrics article { border-bottom: 1px solid var(--rule); }
  .account-metrics article:nth-child(2) { border-right: 0; }
  .account-metrics article:last-child { grid-column: 1 / -1; border-bottom: 0; }
  .section-heading { align-items: flex-start; }
  .section-heading > small { display: none; }
  :deep(.account-entry) { align-items: flex-start; flex-direction: column; }
  :deep(.account-actions) { width: 100%; }
  :deep(.account-actions .el-button) { flex: 1; }
  .login-progress { padding: 14px; }
  .login-stage-rail li { font-size: 9px; }
  .login-status-meta { align-items: flex-start; flex-direction: column; gap: 3px; }
  .login-session-context { grid-template-columns: 1fr; }
  .login-session-context > div { border-right: 0; border-bottom: 1px solid var(--rule); }
  .login-session-context > div:last-child { border-bottom: 0; }
}
</style>
