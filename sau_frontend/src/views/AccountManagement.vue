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
        <el-button type="primary" :icon="Plus" @click="openLogin()">添加媒体账号</el-button>
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
        <p>“刷新本地状态”不联网；只有点击某个账号的“检测登录”，才会打开无头浏览器访问该平台并更新最后检查时间。</p>
      </div>
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
            @check="checkAccount"
            @login="openLogin"
          />
        </div>
      </section>

      <section class="platform-section secondary" aria-labelledby="video-platforms">
        <div class="section-heading">
          <div>
            <span>OMNIPOST CAPABILITY / 02</span>
            <h2 id="video-platforms">既有视频渠道</h2>
            <p>保留 OmniPost 原有平台能力，不让视频发布拖慢文章 GEO 主流程。</p>
          </div>
          <small>{{ secondaryPlatforms.length }} 个渠道</small>
        </div>
        <div class="platform-grid compact">
          <PlatformCard
            v-for="platform in secondaryPlatforms"
            :key="platform.key"
            :platform="platform"
            :checking-ids="checkingIds"
            @check="checkAccount"
            @login="openLogin"
          />
        </div>
      </section>
    </template>

    <el-dialog
      v-model="loginDialogVisible"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      @closed="resetLoginDialog"
    >
      <template #header>
        <div class="dialog-heading">
          <span>AUTHORIZED LOGIN</span>
          <h2>{{ loginDialogTitle }}</h2>
        </div>
      </template>

      <div class="login-dialog">
        <el-form label-position="top">
          <el-form-item label="媒体平台">
            <el-select v-model="loginForm.platformType" placeholder="选择平台" :disabled="loginStage !== 'idle'">
              <el-option
                v-for="platform in overview.platforms"
                :key="platform.type"
                :label="platform.name"
                :value="platform.type"
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

        <div v-if="loginStage === 'idle'" class="login-consent">
          <el-icon><WarningFilled /></el-icon>
          <p>继续后会打开对应平台的登录流程。请只操作你有权使用的账号；扫码、验证码和人工确认不会被绕过。</p>
        </div>

        <div v-else class="login-progress">
          <img v-if="qrCodeData" :src="qrCodeData" alt="平台登录二维码" />
          <div v-else class="progress-symbol" :class="loginStage">
            <el-icon v-if="loginStage === 'success'"><CircleCheckFilled /></el-icon>
            <span v-else>{{ loginStage === 'failed' ? '!' : '•••' }}</span>
          </div>
          <strong>{{ loginStageTitle }}</strong>
          <p>{{ loginStageMessage }}</p>
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
        <el-button v-else-if="loginStage === 'failed'" type="primary" @click="retryLogin">重新尝试</el-button>
        <el-button v-else-if="loginStage === 'success'" type="primary" @click="loginDialogVisible = false">完成</el-button>
        <el-button v-else @click="loginDialogVisible = false">取消登录</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { CircleCheckFilled, Connection, Plus, Refresh, WarningFilled } from '@element-plus/icons-vue'
import { ElButton, ElIcon, ElMessage } from 'element-plus'
import { accountApi } from '@/api/account'
import { apiUrl } from '@/config/api'

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
    checkingIds: { type: Object, required: true }
  },
  emits: ['check', 'login'],
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
        h('small', props.platform.connected_count ? `${props.platform.connected_count} ONLINE` : 'OFFLINE')
      ]),
      props.platform.accounts.length
        ? h('div', { class: 'account-stack' }, props.platform.accounts.map(account => {
            const copy = statusCopy[account.status] || { label: account.status, note: '状态未知' }
            return h('section', { class: 'account-entry', key: account.id }, [
              h('div', { class: 'account-identity' }, [
                h('strong', account.account_name),
                h('span', { class: ['status-chip', account.status] }, copy.label),
                h('p', copy.note),
                h('time', `最后检查：${formatCheckedAt(account.last_checked_at)}`)
              ]),
              h('div', { class: 'account-actions' }, [
                h(ElButton, {
                  size: 'small',
                  loading: props.checkingIds.has(account.id),
                  onClick: () => emit('check', account)
                }, () => '检测登录'),
                h(ElButton, {
                  size: 'small', type: 'primary', plain: true,
                  onClick: () => emit('login', props.platform, account)
                }, () => '重新登录')
              ])
            ])
          }))
        : h('div', { class: 'platform-empty' }, [
            h('p', '尚未连接账号'),
            h(ElButton, {
              size: 'small', type: 'primary', plain: true,
              onClick: () => emit('login', props.platform)
            }, () => '添加 / 登录')
          ])
    ])
  }
})

const overview = reactive(emptyOverview())
const loading = ref(false)
const loaded = ref(false)
const loadError = ref(false)
const lastSyncedAt = ref(null)
const checkingIds = ref(new Set())
const loginDialogVisible = ref(false)
const loginStage = ref('idle')
const qrCodeData = ref('')
const loginForm = reactive({ platformType: null, accountName: '', existingAccountId: null })
let eventSource = null

const primaryPlatforms = computed(() => overview.platforms.filter(item => ['P0', 'P1'].includes(item.priority)))
const secondaryPlatforms = computed(() => overview.platforms.filter(item => item.priority === 'VIDEO'))
const syncLabel = computed(() => lastSyncedAt.value
  ? `本地同步 ${lastSyncedAt.value.toLocaleTimeString('zh-CN', { hour12: false })}`
  : '等待首次同步')
const selectedPlatform = computed(() => overview.platforms.find(item => item.type === loginForm.platformType))
const loginDialogTitle = computed(() => `${loginForm.existingAccountId ? '重新登录' : '连接'}${selectedPlatform.value ? ` · ${selectedPlatform.value.name}` : '媒体账号'}`)
const loginStageTitle = computed(() => ({
  connecting: '登录流程已启动', manual: '等待你完成平台登录', success: '账号登录成功', failed: '登录没有完成'
}[loginStage.value] || '准备登录'))
const loginStageMessage = computed(() => {
  if (loginStage.value === 'success') return '本地账号记录已刷新，可以返回媒体账号页继续检测。'
  if (loginStage.value === 'failed') return '平台登录超时或验证失败，没有绕过任何安全步骤。'
  if (qrCodeData.value) return '请使用对应平台 App 扫描二维码，并在平台侧完成确认。'
  if (loginStage.value === 'manual') return '请在已打开的浏览器或终端中完成登录，系统会等待结果。'
  return '正在等待平台返回二维码或人工登录窗口。'
})

const fetchOverview = async () => {
  loading.value = true
  loadError.value = false
  try {
    const response = await accountApi.getMediaAccounts()
    Object.assign(overview, response.data || emptyOverview())
    loaded.value = true
    lastSyncedAt.value = new Date()
  } catch (error) {
    loadError.value = true
    console.error('加载媒体账号失败:', error)
  } finally {
    loading.value = false
  }
}

const checkAccount = async account => {
  if (checkingIds.value.has(account.id)) return
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
  loginForm.platformType = platform?.type || null
  loginForm.accountName = account?.account_name || ''
  loginForm.existingAccountId = account?.id || null
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginDialogVisible.value = true
}

const startLogin = () => {
  closeEventSource()
  loginStage.value = 'connecting'
  qrCodeData.value = ''
  const url = apiUrl(`/login?type=${loginForm.platformType}&id=${encodeURIComponent(loginForm.accountName.trim())}`)
  eventSource = new EventSource(url, { withCredentials: true })
  eventSource.onmessage = event => {
    const data = event.data
    if (data === 'MANUAL_LOGIN') {
      loginStage.value = 'manual'
      return
    }
    if (data === '200') {
      loginStage.value = 'success'
      closeEventSource()
      fetchOverview()
      return
    }
    if (data === '500') {
      loginStage.value = 'failed'
      closeEventSource()
      return
    }
    if (data.length > 100) {
      qrCodeData.value = data.startsWith('data:image') ? data : `data:image/png;base64,${data}`
    }
  }
  eventSource.onerror = () => {
    if (!['success', 'failed'].includes(loginStage.value)) loginStage.value = 'failed'
    closeEventSource()
  }
}

const retryLogin = () => {
  loginStage.value = 'idle'
  qrCodeData.value = ''
}

const closeEventSource = () => {
  if (eventSource) eventSource.close()
  eventSource = null
}

const resetLoginDialog = () => {
  closeEventSource()
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginForm.platformType = null
  loginForm.accountName = ''
  loginForm.existingAccountId = null
}

onMounted(fetchOverview)
onBeforeUnmount(closeEventSource)
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
:deep(.account-stack) { padding: 0 18px; }
:deep(.account-entry) { display: flex; justify-content: space-between; gap: 16px; padding: 15px 0; border-bottom: 1px solid #e1e8e6; }
:deep(.account-entry:last-child) { border-bottom: 0; }
:deep(.account-identity strong) { margin-right: 8px; font-size: 13px; }
:deep(.status-chip) { display: inline-flex; padding: 3px 6px; color: #637578; background: #e8eeec; font-size: 9px; font-weight: 700; }
:deep(.status-chip.connected) { color: #08746f; background: #d9f2ee; }
:deep(.status-chip.expired), :deep(.status-chip.missing_cookie) { color: #a06119; background: #f8ead7; }
:deep(.account-identity p) { margin: 7px 0 2px; color: #5e7378; font-size: 11px; }
:deep(.account-identity time) { color: #839194; font: 9px "Cascadia Mono", monospace; }
:deep(.account-actions) { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
:deep(.platform-empty) { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 62px; padding: 13px 18px; }
:deep(.platform-empty p) { margin: 0; color: var(--muted); font-size: 12px; }

.load-state { margin-top: 28px; padding: 32px; text-align: center; border: 1px solid var(--rule); background: white; }
.load-state p { color: var(--muted); }
.dialog-heading h2 { margin: 4px 0 0; color: var(--ink); font-size: 23px; }
.login-dialog :deep(.el-select) { width: 100%; }
.login-consent { display: flex; gap: 10px; padding: 13px; color: #74552d; background: #fbf2e5; }
.login-consent p { margin: 0; font-size: 12px; line-height: 1.6; }
.login-progress { display: grid; justify-items: center; min-height: 220px; padding: 18px; text-align: center; border: 1px solid var(--rule); background: #f5f8f7; }
.login-progress img { width: 180px; height: 180px; object-fit: contain; background: white; }
.login-progress strong { margin-top: 12px; }
.login-progress p { max-width: 360px; margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.progress-symbol { display: grid; place-items: center; width: 82px; height: 82px; margin-top: 26px; color: white; background: var(--ink); font: 700 18px "Cascadia Mono", monospace; }
.progress-symbol.success { background: #168f87; font-size: 34px; }
.progress-symbol.failed { background: #bd594f; font-size: 34px; }

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
}
</style>
