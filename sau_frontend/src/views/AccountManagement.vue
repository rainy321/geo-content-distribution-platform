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
        <p>需要处理</p>
      </article>
    </section>

    <section class="safety-note" aria-label="账号检测说明">
      <div class="note-icon"><el-icon><Connection /></el-icon></div>
      <div>
        <strong>账号在线 ≠ 发布链路可用 ≠ AI 会采纳</strong>
        <p>“刷新本地状态”不联网；只有显式检测才访问平台。真实发布还受渠道验证状态、滚动 24 小时建议量和最小间隔保护，公开链接也只代表进入后续抓取验证。</p>
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
            @check="checkAccount"
            @login="openLogin"
          />
        </div>
      </section>

      <section v-if="overview.planned_platforms.length" class="platform-section roadmap" aria-labelledby="roadmap-platforms">
        <div class="section-heading">
          <div>
            <span>CHANNEL ROADMAP / 03</span>
            <h2 id="roadmap-platforms">经典平台缺口</h2>
            <p>以下渠道尚未接入真实发布，不提供登录按钮；优先级按 GEO 证据价值与官方接入条件排序。</p>
          </div>
          <small>{{ overview.planned_platforms.length }} 个候选</small>
        </div>
        <div class="roadmap-grid">
          <article v-for="platform in overview.planned_platforms" :key="platform.key" class="roadmap-card">
            <header>
              <span>{{ platform.priority }}</span>
              <strong>{{ platform.name }}</strong>
              <i>规划中</i>
            </header>
            <p>{{ platform.channel_role_label }}</p>
            <dl>
              <div><dt>AI / 搜索价值</dt><dd>{{ platform.crawl_label }}</dd></div>
              <div><dt>接入路径</dt><dd>{{ platform.integration_path }}</dd></div>
            </dl>
            <small>{{ platform.release_note }}</small>
          </article>
        </div>
      </section>
    </template>

    <el-dialog
      :model-value="loginDialogVisible"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :before-close="requestLoginDialogClose"
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
            <el-select v-model="loginForm.platformType" placeholder="选择平台" :disabled="loginStage !== 'idle' || !!loginForm.existingAccountId">
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
          <p>{{ loginForm.existingAccountId ? '重新登录会更新这个账号的会话，已绑定的文章任务会保留。' : '继续后会打开对应平台的登录流程。' }} 请在浏览器正常完成扫码或验证。</p>
        </div>

        <div v-else class="login-progress">
          <img v-if="qrCodeData" :src="qrCodeData" alt="平台登录二维码" />
          <div v-else class="progress-symbol" :class="loginStage">
            <el-icon v-if="loginStage === 'success'"><CircleCheckFilled /></el-icon>
            <span v-else>{{ loginStage === 'failed' ? '!' : '•••' }}</span>
          </div>
          <strong>{{ loginStageTitle }}</strong>
          <p>{{ loginStageMessage }}</p>
          <p v-if="pendingLogin" class="login-detach-note">关闭面板会结束本次等待。原浏览器可能仍在运行，请先核对原窗口，待原流程结束后再重试。</p>
          <details v-if="loginFailureCode"><summary>查看原因代码</summary><p>{{ loginFailureCode }}</p></details>
        </div>
      </div>

      <template #footer>
        <el-button v-if="loginStage === 'idle'" @click="closeLoginDialog">取消</el-button>
        <el-button
          v-if="loginStage === 'idle'"
          type="primary"
          :disabled="!loginForm.platformType || !loginForm.accountName.trim()"
          @click="startLogin"
        >
          打开登录流程
        </el-button>
        <el-button v-else-if="loginStage === 'failed'" type="primary" @click="retryLogin">重新尝试</el-button>
        <el-button v-else-if="loginStage === 'success'" type="primary" @click="closeLoginDialog">完成</el-button>
        <el-button v-else @click="closeLoginDialog">关闭等待</el-button>
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
  planned_platforms: [],
  accounts: []
})

const statusCopy = {
  connected: { label: '已连接', note: '本地 Cookie 状态正常' },
  expired: { label: '需要确认', note: '请检测登录，必要时重新登录' },
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

    return () => h('article', { class: ['platform-card', props.platform.delivery_state, { online: props.platform.connected_count > 0 }] }, [
      h('header', { class: 'platform-card-head' }, [
        h('div', { class: 'platform-code' }, props.platform.name.slice(0, 1)),
        h('div', [
          h('span', `${props.platform.priority} / ${String(props.platform.type).padStart(2, '0')}`),
          h('h3', props.platform.name)
        ]),
        h('small', props.platform.connected_count ? `${props.platform.connected_count} ONLINE` : 'OFFLINE')
      ]),
      h('div', { class: 'platform-policy' }, [
        h('div', { class: 'policy-title' }, [
          h('span', { class: ['delivery-chip', props.platform.delivery_state] }, props.platform.delivery_label),
          h('strong', props.platform.channel_role_label)
        ]),
        h('div', { class: 'policy-metrics' }, [
          h('span', [h('b', `${props.platform.safe_daily_new} → ${props.platform.safe_daily_stable}`), ' 建议篇/24h']),
          h('span', [h('b', `≥ ${props.platform.min_interval_hours}h`), ' 最小间隔']),
          h('span', [h('b', props.platform.crawl_label), ' AI 可见性'])
        ]),
        h('p', props.platform.content_focus),
        h('small', `风险提示：${props.platform.risk_note}`)
      ]),
      props.platform.accounts.length
        ? h('div', { class: 'account-stack' }, props.platform.accounts.map(account => {
            const copy = statusCopy[account.status] || { label: account.status, note: '状态未知' }
            return h('section', { class: 'account-entry', key: account.id }, [
              h('div', { class: 'account-identity' }, [
                h('strong', account.account_name),
                h('span', { class: ['status-chip', account.status] }, copy.label),
                h('p', copy.note),
                h('time', `最后检查：${formatCheckedAt(account.last_checked_at)}`),
                account.publish_policy
                  ? h('div', { class: 'account-quota' }, [
                      h('span', `滚动 24h 已占用 ${account.publish_policy.reserved_24h} / ${account.publish_policy.safe_daily_new}`),
                      h('b', `剩余 ${account.publish_policy.remaining_new}`),
                      h('small', '仅统计本系统任务；平台后台手工发布量未知')
                    ])
                  : null
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
const loginFailureReason = ref('')
const loginFailureCode = ref('')
let eventSource = null,loginEpoch=0,overviewRequest=0,destroyed=false
const pendingLogin=computed(()=>['connecting','opening','manual'].includes(loginStage.value))
const loginErrorCopy={
  browser_not_found:'未找到可用于登录的浏览器，平台窗口尚未打开。请安装本机 Chrome 或 Edge，或联系维护者检查软件的浏览器组件后重试。',
  browser_launch_failed:'登录浏览器未能启动。请先关闭残留的登录窗口，再重试；持续失败时请联系维护者检查浏览器组件。',
  browser_closed:'登录浏览器已关闭，尚未确认登录完成。请核对原窗口，再重新尝试。',
  navigation_failed:'浏览器未能打开平台登录页。请检查网络和平台是否可以正常访问，再重新尝试。',
  login_timeout:'等待平台登录超时。请先核对并关闭原登录窗口，再重新尝试。',
  login_failed:'登录流程未完成，账号是否已登录尚未确认。请核对原浏览器中的提示后重试。',
  component_missing:'缺少此平台的登录组件，请联系软件维护者补齐组件后重试。',
  login_cancelled:'登录流程已结束，尚未确认会话保存。原浏览器如仍在运行，请先核对原窗口。',
}

const primaryPlatforms = computed(() => overview.platforms.filter(item => ['P0', 'P1'].includes(item.priority)))
const secondaryPlatforms = computed(() => overview.platforms.filter(item => !['P0', 'P1'].includes(item.priority)))
const syncLabel = computed(() => lastSyncedAt.value
  ? `本地同步 ${lastSyncedAt.value.toLocaleTimeString('zh-CN', { hour12: false })}`
  : '等待首次同步')
const selectedPlatform = computed(() => overview.platforms.find(item => item.type === loginForm.platformType))
const loginDialogTitle = computed(() => `${loginForm.existingAccountId ? '重新登录' : '连接'}${selectedPlatform.value ? ` · ${selectedPlatform.value.name}` : '媒体账号'}`)
const loginStageTitle = computed(() => ({
  connecting: '正在连接本机登录服务', opening:'正在打开登录浏览器', manual: '等待你完成平台登录', success: '会话已保存', failed: '登录没有完成'
}[loginStage.value] || '准备登录'))
const loginStageMessage = computed(() => {
  if (loginStage.value === 'success') return '本地账号记录已刷新，可以返回媒体账号页继续检测。'
  if (loginStage.value === 'failed') return loginFailureReason.value || '登录未完成，请检查浏览器中的提示后重试。'
  if (qrCodeData.value) return '请使用对应平台 App 扫描二维码，并在平台侧完成确认。'
  if (loginStage.value === 'manual') return Number(loginForm.platformType) === 9
    ? '知乎登录页已打开，请在该浏览器正常完成扫码或安全验证，并保持窗口打开。最多等待10分钟，检测到登录后会自动保存会话。'
    : '平台登录页已打开，请在该浏览器正常完成扫码或安全验证，并保持窗口打开，系统会等待保存结果。'
  if (loginStage.value === 'opening') return '本机服务正在启动浏览器，尚未确认平台登录页已打开。'
  return '正在等待本机登录服务响应，尚未确认浏览器已打开。'
})

const fetchOverview = async () => {
  if(destroyed)return
  const request=++overviewRequest
  loading.value = true
  loadError.value = false
  try {
    const response = await accountApi.getMediaAccounts()
    if(destroyed||request!==overviewRequest)return
    Object.assign(overview, response.data || emptyOverview())
    loaded.value = true
    lastSyncedAt.value = new Date()
  } catch (error) {
    if(destroyed||request!==overviewRequest)return
    loadError.value = true
    console.error('加载媒体账号失败:', error)
  } finally {
    if(!destroyed&&request===overviewRequest)loading.value = false
  }
}

const checkAccount = async account => {
  if (destroyed||checkingIds.value.has(account.id)) return
  checkingIds.value = new Set([...checkingIds.value, account.id])
  try {
    const response = await accountApi.checkMediaAccount(account.id)
    if(destroyed)return
    ElMessage.success(response.data?.check_message || '登录状态检测完成')
    await fetchOverview()
  } catch (error) {
    if(!destroyed)ElMessage.error(error?.message || '平台状态检测失败，请稍后重试')
  } finally {
    if(!destroyed){const next = new Set(checkingIds.value);next.delete(account.id);checkingIds.value = next}
  }
}

const openLogin = (platform = null, account = null) => {
  if(destroyed)return
  closeEventSource()
  loginForm.platformType = platform?.type || null
  loginForm.accountName = account?.account_name || ''
  loginForm.existingAccountId = account?.id || null
  loginStage.value = 'idle'
  loginFailureReason.value = ''
  loginFailureCode.value = ''
  qrCodeData.value = ''
  loginDialogVisible.value = true
}

const startLogin = () => {
  if(destroyed||!loginDialogVisible.value||pendingLogin.value)return
  const platform=Number(loginForm.platformType),name=loginForm.accountName.trim()
  if(!Number.isInteger(platform)||platform<1||platform>10||!name){loginStage.value='idle';ElMessage.error('请选择媒体平台并填写账号备注名');return}
  closeEventSource()
  const epoch=loginEpoch,snapshot={platformType:loginForm.platformType,accountName:loginForm.accountName,existingAccountId:loginForm.existingAccountId}
  loginStage.value = 'connecting'
  loginFailureReason.value = ''
  loginFailureCode.value = ''
  qrCodeData.value = ''
  const query = new URLSearchParams({ type: String(loginForm.platformType), id: loginForm.accountName.trim() })
  if (loginForm.existingAccountId) query.set('account_id', String(loginForm.existingAccountId))
  const url = apiUrl(`/login?${query.toString()}`)
  let source
  try{source=new EventSource(url, { withCredentials: true })}
  catch{loginStage.value='failed';loginFailureReason.value='无法连接本机登录服务，尚未确认浏览器已打开。请检查软件服务后重试。';return}
  eventSource=source
  const current=()=>!destroyed&&loginDialogVisible.value&&epoch===loginEpoch&&eventSource===source
    && Object.keys(snapshot).every(key=>snapshot[key]===loginForm[key])
  const fail=(reason,code='')=>{if(!current())return;loginFailureReason.value=reason;loginFailureCode.value=code;qrCodeData.value='';loginStage.value='failed';closeEventSource()}
  source.onmessage = event => {
    if(!current()||typeof event.data!=='string')return
    const data = event.data
    if(data==='LOGIN_OPENING'){loginStage.value='opening';return}
    if(data.startsWith('LOGIN_ERROR:')){
      const code=data.slice('LOGIN_ERROR:'.length).toLowerCase()
      fail(Object.hasOwn(loginErrorCopy,code)?loginErrorCopy[code]:loginErrorCopy.login_failed,Object.hasOwn(loginErrorCopy,code)?code:'login_failed')
      return
    }
    if (data.startsWith('COMPONENT_MISSING:')) {
      const component = data.slice('COMPONENT_MISSING:'.length)
      fail(/^[A-Za-z0-9_.-]{1,80}$/.test(component)?`缺少此平台的登录组件（${component}），请联系软件维护者补齐组件后重试。`:'缺少此平台的登录组件，请联系软件维护者补齐后重试。','component_missing')
      return
    }
    if (data === 'MANUAL_LOGIN') {
      loginStage.value = 'manual'
      return
    }
    if (data === '200') {
      loginStage.value = 'success'
      qrCodeData.value=''
      closeEventSource()
      fetchOverview()
      return
    }
    if (data === '500') {
      fail(loginFailureReason.value || loginErrorCopy.login_failed,loginFailureCode.value || 'login_failed')
      return
    }
    if (data.length > 100 && data.length<=2_000_000) {
      if(/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(data))qrCodeData.value=data
      else if(/^[A-Za-z0-9+/]+={0,2}$/.test(data))qrCodeData.value=`data:image/png;base64,${data}`
    }
  }
  source.onerror = () => {
    fail(loginFailureReason.value || '与本机登录服务的连接已结束，登录结果尚未确认。原浏览器可能仍在运行，请先核对原窗口，再重试。',loginFailureCode.value)
  }
}

const retryLogin = () => {
  if(loginStage.value==='failed')startLogin()
}

const closeEventSource = () => {
  loginEpoch++
  const source=eventSource
  eventSource = null
  if (source) {source.onmessage=null;source.onerror=null;source.close()}
}

const resetLoginDialog = () => {
  closeEventSource()
  loginStage.value = 'idle'
  qrCodeData.value = ''
  loginFailureReason.value='';loginFailureCode.value=''
  loginForm.platformType = null
  loginForm.accountName = ''
  loginForm.existingAccountId = null
}
const closeLoginDialog=()=>{loginDialogVisible.value=false;resetLoginDialog()}
const requestLoginDialogClose=done=>{closeLoginDialog();if(typeof done==='function')done()}

onMounted(fetchOverview)
onBeforeUnmount(()=>{destroyed=true;overviewRequest++;closeEventSource()})
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

.platform-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: start; }
.platform-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
:deep(.platform-card) { min-width: 0; border: 1px solid var(--rule); background: var(--paper); }
:deep(.platform-card.online) { border-top-color: var(--teal); }
:deep(.platform-card.paused) { border-top-color: #bd594f; }
:deep(.platform-card-head) { display: grid; grid-template-columns: 42px 1fr auto; gap: 12px; align-items: center; padding: 16px 18px; border-bottom: 1px solid var(--rule); }
:deep(.platform-code) { display: grid; place-items: center; width: 40px; height: 40px; color: white; background: var(--ink); font-weight: 800; }
:deep(.platform-card-head h3) { margin: 3px 0 0; font-size: 18px; }
:deep(.platform-card-head small) { color: #719093; font: 700 10px "Cascadia Mono", monospace; }
:deep(.platform-policy) { padding: 14px 18px; border-bottom: 1px solid var(--rule); background: #f7faf9; }
:deep(.policy-title) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
:deep(.policy-title strong) { font-size: 13px; }
:deep(.delivery-chip) { display: inline-flex; padding: 4px 7px; color: #176d68; background: #dff2ef; font-size: 10px; font-weight: 700; }
:deep(.delivery-chip.assisted) { color: #80591f; background: #f7ead6; }
:deep(.delivery-chip.paused) { color: #963f3f; background: #f7dede; }
:deep(.policy-metrics) { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 11px; }
:deep(.policy-metrics span) { min-width: 0; color: #6a7b7f; font-size: 10px; line-height: 1.45; }
:deep(.policy-metrics b) { display: block; overflow: hidden; color: #24474e; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
:deep(.platform-policy > p) { margin: 11px 0 5px; color: #536a70; font-size: 11px; line-height: 1.6; }
:deep(.platform-policy > small) { color: #8a5b2c; font-size: 10px; line-height: 1.5; }
:deep(.account-stack) { padding: 0 18px; }
:deep(.account-entry) { display: flex; justify-content: space-between; gap: 16px; padding: 15px 0; border-bottom: 1px solid #e1e8e6; }
:deep(.account-entry:last-child) { border-bottom: 0; }
:deep(.account-identity strong) { margin-right: 8px; font-size: 13px; }
:deep(.status-chip) { display: inline-flex; padding: 3px 6px; color: #637578; background: #e8eeec; font-size: 9px; font-weight: 700; }
:deep(.status-chip.connected) { color: #08746f; background: #d9f2ee; }
:deep(.status-chip.expired), :deep(.status-chip.missing_cookie) { color: #a06119; background: #f8ead7; }
:deep(.account-identity p) { margin: 7px 0 2px; color: #5e7378; font-size: 12px; }
:deep(.account-identity time) { color: #839194; font: 10px "Cascadia Mono", monospace; }
:deep(.account-quota) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 9px; padding-top: 8px; border-top: 1px dashed #d7e1de; }
:deep(.account-quota span) { color: #4f696e; font-size: 11px; }
:deep(.account-quota b) { color: #08746f; font-size: 11px; }
:deep(.account-quota small) { width: 100%; color: #879497; font-size: 9px; }
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

.roadmap-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.roadmap-card { min-width: 0; padding: 16px; border: 1px dashed #afbfbc; background: rgba(255, 255, 255, .65); }
.roadmap-card header { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 9px; }
.roadmap-card header span { color: #087f81; font: 700 9px "Cascadia Mono", monospace; }
.roadmap-card header strong { font-size: 14px; }
.roadmap-card header i { padding: 3px 6px; color: #68797d; background: #e8eeec; font-size: 9px; font-style: normal; }
.roadmap-card > p { min-height: 36px; margin: 12px 0; color: #405f65; font-size: 12px; line-height: 1.55; }
.roadmap-card dl { margin: 0; }
.roadmap-card dl div { display: grid; grid-template-columns: 88px 1fr; gap: 8px; padding: 6px 0; border-top: 1px solid #e0e7e5; }
.roadmap-card dt { color: #7b898c; font-size: 10px; }
.roadmap-card dd { margin: 0; color: #3f5b61; font-size: 10px; line-height: 1.45; }
.roadmap-card > small { display: block; margin-top: 10px; color: #7a6b58; font-size: 9px; line-height: 1.5; }

@media (max-width: 860px) {
  .page-hero { align-items: flex-start; flex-direction: column; }
  .hero-actions { justify-content: flex-start; }
  .hero-actions > span { text-align: left; }
  .platform-grid, .platform-grid.compact { grid-template-columns: 1fr; }
  .roadmap-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
  :deep(.policy-metrics) { grid-template-columns: 1fr; }
  .roadmap-grid { grid-template-columns: 1fr; }
}
</style>
