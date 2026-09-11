<template>
  <main class="settings-page">
    <section class="settings-hero">
      <div class="hero-copy">
        <span class="eyebrow">SYSTEM / UNIFIED AI BUS</span>
        <h1>系统设置</h1>
        <p>
          写稿模型作为安全默认值；调优、回测和生图可以独立配置，也可以明确回退。
          API Key 由当前 Windows 用户加密保护，界面和状态接口都不会回显明文。
        </p>
      </div>
      <div :class="['active-source', sourceTone]">
        <span>统一 AI 引擎</span>
        <strong>{{ activeSourceLabel }}</strong>
        <small>{{ activeSourceHint }}</small>
      </div>
    </section>

    <div class="settings-layout">
      <section class="config-card" v-loading="statusLoading">
        <header class="section-heading">
          <div>
            <span class="section-kicker">ROLE ROUTER / SECURE MODEL BUS</span>
            <h2>{{ activeRoleDefinition.label }}</h2>
          </div>
          <el-tag :type="activeRoleStatus.effectiveConfigured ? 'success' : 'info'" effect="plain">
            {{ activeRoleStateLabel }}
          </el-tag>
        </header>

        <section class="engine-bus" aria-label="统一 AI 引擎总线状态">
          <div class="bus-line" aria-hidden="true"><i :class="{ live: aiStatus.configured }" /></div>
          <article>
            <span>SECURE VAULT</span>
            <strong>{{ activeCredentialLabel }}</strong>
            <small>{{ credentialStorageLabel }}</small>
          </article>
          <article>
            <span>MODEL CORE</span>
            <strong>{{ activeRoleStatus.model || '等待配置' }}</strong>
            <small>{{ activeRoleStatus.providerLabel || 'OpenAI-compatible' }}</small>
          </article>
          <article class="bus-output">
            <span>ROLE OUTPUT</span>
            <strong>{{ activeRoleDefinition.code }} · {{ activeRoleStatus.fallback ? 'FALLBACK' : 'DIRECT' }}</strong>
            <small>{{ activeRoleDefinition.description }}</small>
          </article>
        </section>

        <nav class="role-switcher" aria-label="AI 模型角色">
          <button
            v-for="role in roleDefinitions"
            :key="role.id"
            type="button"
            :class="{ active: selectedRole === role.id }"
            :aria-current="selectedRole === role.id ? 'true' : undefined"
            @click="selectRole(role.id)"
          >
            <span>{{ role.code }}</span>
            <strong>{{ role.label }}</strong>
            <small>{{ aiStatus.roles[role.id]?.configured ? '独立配置' : (role.id === 'writer' ? '等待配置' : '回退到写稿') }}</small>
          </button>
        </nav>

        <div v-if="reviewerSimilarityRisk" class="role-risk" role="status">
          <strong>调优模型与写稿模型相同</strong>
          <span>该旧配置不会再执行第二次付费调用。请为 REVIEW 更换模型 ID、服务商或服务地址后重新保存。</span>
        </div>
        <div v-if="selectedRole === 'image'" class="role-runtime-note" role="note">
          <strong>生图连接已接入文章封面流程</strong>
          <span>配置后，发布中心“生成封面”会调用 /images/generations；未配置时才使用本地确定性封面。调用回执进入成本账单，服务商不返回 Token 时保持未知。</span>
        </div>

        <div v-if="statusError" class="config-error" role="status">
          {{ statusError }}
          <button type="button" @click="loadAIStatus">重新读取</button>
        </div>

        <div class="provider-picker" role="radiogroup" aria-label="模型服务商">
          <button
            v-for="preset in providerPresets"
            :key="preset.id"
            type="button"
            role="radio"
            :aria-checked="form.provider === preset.id"
            :class="{ active: form.provider === preset.id }"
            :disabled="saving"
            @click="selectProvider(preset.id)"
          >
            <strong>{{ preset.label }}</strong>
            <small>{{ preset.id === 'custom' ? 'COMPATIBLE' : preset.id.toUpperCase() }}</small>
          </button>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          class="config-form"
        >
          <el-form-item label="Base URL" prop="baseUrl">
            <el-input
              v-model="form.baseUrl"
              placeholder="https://api.example.com/v1"
              autocomplete="url"
              clearable
            >
              <template #prefix><el-icon><Connection /></el-icon></template>
            </el-input>
            <p class="field-help">
              必须使用公开 HTTPS 地址；系统会自动且只补一次
              {{ selectedRole === 'image' ? '/images/generations' : '/chat/completions' }}。
            </p>
            <p v-if="resolvedEndpointPreview" class="field-help endpoint-preview">
              实际请求地址：<code>{{ resolvedEndpointPreview }}</code>
            </p>
            <p class="field-help provider-hint">{{ providerConfigurationHint }}</p>
          </el-form-item>

          <div class="field-grid">
            <el-form-item label="模型名称" prop="model">
              <el-input
                v-model="form.model"
                :placeholder="modelPlaceholder"
                autocomplete="off"
                clearable
              >
                <template #prefix><el-icon><Cpu /></el-icon></template>
              </el-input>
            </el-form-item>

            <el-form-item label="API Key" prop="apiKey">
              <el-input
                v-model="form.apiKey"
                type="password"
                :placeholder="activeRoleStatus.configured && activeRoleStatus.apiKeyConfigured ? '已安全保存；连接不变时可留空' : (canReuseWriterCredential ? '可复用写稿密钥，或填写独立 API Key' : '首次保存必须填写 API Key')"
                autocomplete="new-password"
                show-password
                clearable
              >
                <template #prefix><el-icon><Key /></el-icon></template>
              </el-input>
            </el-form-item>
          </div>
          <el-checkbox
            v-if="selectedRole !== 'writer'"
            v-model="form.reuseWriterCredential"
            :disabled="!canReuseWriterCredential || saving"
            class="credential-reuse"
          >
            同一服务地址时复用写稿模型密钥
          </el-checkbox>
        </el-form>

        <div class="session-note">
          <el-icon><Lock /></el-icon>
          <div>
            <strong>{{ activeCredentialLabel }}</strong>
            <p>密钥经 Windows DPAPI 加密后只供当前用户使用；状态接口仅返回“已配置”，不会返回密钥。</p>
          </div>
        </div>

        <div v-if="connectionResult" :class="['connection-result', connectionResult.ok ? 'success' : 'failure']" aria-live="polite">
          <span>{{ connectionResult.ok ? 'CONNECTION / READY' : 'CONNECTION / FAILED' }}</span>
          <strong>{{ connectionResult.message }}</strong>
          <small v-if="connectionResult.ok">
            {{ connectionResult.provider }} · {{ connectionResult.model }} · {{ connectionResult.elapsedMs }} ms
          </small>
          <small v-if="connectionResult.ok && connectionResult.endpoint">{{ connectionResult.endpoint }}</small>
        </div>

        <footer class="form-actions">
          <el-button :disabled="!canClearActiveRole || clearing" :loading="clearing" @click="clearConfig">{{ clearButtonLabel }}</el-button>
          <div>
            <el-button :loading="testing" :disabled="!activeRoleStatus.effectiveConfigured" @click="testConnection">测试已保存角色</el-button>
            <el-button type="primary" :loading="saving" :disabled="!aiStatus.saveSupported" @click="saveConfig">
              {{ saveButtonLabel }}
            </el-button>
          </div>
        </footer>
        <p class="test-boundary">
          {{ selectedRole === 'image'
            ? '测试连接会生成并立即丢弃一张固定合成测试图，不发送项目、文章、账号或 Cookie；本次真实调用会进入成本账单。'
            : '测试连接只会发送固定合成文本“仅回复 OK”，不发送项目、文章、账号或 Cookie；本次真实调用会进入成本账单。' }}
        </p>
      </section>

      <aside class="routing-column">
        <section class="engine-status-card" aria-live="polite">
          <header class="engine-status-heading">
            <div>
              <span class="section-kicker">CONTENT ENGINE / RUNTIME</span>
              <h2>内容引擎状态</h2>
            </div>
            <el-button text :loading="engineStatusLoading" @click="loadEngineStatus">刷新状态</el-button>
          </header>

          <div v-if="engineStatusLoading && !engineStatus" class="engine-status-loading">
            正在读取部署环境状态…
          </div>
          <div v-else class="engine-status-body">
            <div class="engine-health-row">
              <span :class="['health-dot', engineHealthTone]" />
              <div>
                <strong>{{ engineStatusLabel }}</strong>
                <small>{{ engineStatusHint }}</small>
              </div>
            </div>
            <dl class="engine-facts">
              <div>
                <dt>当前引擎</dt>
                <dd>{{ engineStatus?.engine || '无法确认' }}</dd>
              </div>
              <div>
                <dt>版本</dt>
                <dd>{{ engineStatus?.version || '未提供' }}</dd>
              </div>
              <div>
                <dt>回退策略</dt>
                <dd>{{ fallbackStatusLabel }}</dd>
              </div>
              <div>
                <dt>最近检查</dt>
                <dd>{{ engineStatus?.checkedAt || '未提供' }}</dd>
              </div>
            </dl>
            <p class="engine-managed-note">该路由由部署环境管理，本页面不提供引擎切换，也不会显示内部地址或密钥。</p>
          </div>
        </section>

        <section class="routing-card">
          <header>
            <span class="section-kicker">REQUEST PRIORITY</span>
            <h2>请求如何选择配置</h2>
          </header>

          <div class="routing-rail">
            <article :class="['route-node', { active: aiStatus.savedConfigured }]">
              <span class="node-index">01</span>
              <div>
                <strong>Windows 安全配置</strong>
                <p>保存一次后由当前 Windows 用户解密，关闭软件也不会丢失。</p>
              </div>
              <el-icon><Monitor /></el-icon>
            </article>
            <article :class="['route-node', { active: !aiStatus.savedConfigured && aiStatus.serverConfigured }]">
              <span class="node-index">02</span>
              <div>
                <strong>运行环境配置</strong>
                <p>{{ aiStatus.serverConfigured ? '当前运行环境已配置，可在未保存本机设置时使用。' : '运行环境没有备用模型设置。' }}</p>
              </div>
              <el-icon><Cloudy /></el-icon>
            </article>
            <article class="route-node provider-node">
              <span class="node-index">03</span>
              <div>
                <strong>角色路由总线</strong>
                <p>写稿、调优、回测与生图可以独立配置；未配置的角色会明确回退到写稿模型。</p>
              </div>
              <el-icon><Promotion /></el-icon>
            </article>
          </div>
        </section>

        <section class="guardrail-card">
          <el-icon><Lock /></el-icon>
          <div>
            <span>SECURITY BOUNDARY</span>
            <h3>{{ accessControlEnabled ? '运营访问保护已启用' : '密钥不会显示在状态接口里' }}</h3>
            <p>
              {{ accessControlEnabled
                ? '模型调用、数据修改和媒体控制均要求有效的 HttpOnly 会话。'
                : '地址只允许 HTTPS，并拒绝本机、私网、保留地址以及 URL 内嵌凭据。' }}
            </p>
            <el-button v-if="accessControlEnabled" text @click="logoutSession">
              <el-icon><SwitchButton /></el-icon>
              退出运营会话
            </el-button>
          </div>
        </section>
      </aside>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Cloudy,
  Connection,
  Cpu,
  Key,
  Lock,
  Monitor,
  Promotion,
  SwitchButton
} from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { aiConfigApi } from '@/api/aiConfig'
import { authApi } from '@/api/auth'
import {
  AI_ROLE_DEFINITIONS,
  UNIFIED_AI_PROVIDER_PRESETS,
  applyUnifiedAIProviderPreset,
  buildUnifiedAIRoleConfigPayload,
  buildUnifiedAIConfigPayload,
  normalizeUnifiedAIStatus,
  resolveUnifiedAIChatEndpoint,
  resolveUnifiedAIImageEndpoint,
  unifiedAIProviderConfigurationHint,
  validateUnifiedAIProviderBinding
} from '@/utils/aiConfig'

const route = useRoute()
const formRef = ref(null)
const saving = ref(false)
const clearing = ref(false)
const testing = ref(false)
const statusLoading = ref(false)
const statusError = ref('')
const accessControlEnabled = ref(false)
const engineStatusLoading = ref(false)
const engineStatusError = ref('')
const engineStatus = ref(null)
const connectionResult = ref(null)
const providerPresets = UNIFIED_AI_PROVIDER_PRESETS
const roleDefinitions = AI_ROLE_DEFINITIONS
const selectedRole = ref('writer')
const aiStatus = reactive(normalizeUnifiedAIStatus({}))
const form = reactive({
  provider: 'qwen',
  baseUrl: '',
  apiKey: '',
  model: '',
  reuseWriterCredential: true
})

const activeRoleDefinition = computed(() => (
  roleDefinitions.find(item => item.id === selectedRole.value) || roleDefinitions[0]
))
const emptyRoleStatus = Object.freeze({
  configured: false,
  effectiveConfigured: false,
  fallback: false,
  sourceRole: '',
  provider: '',
  providerLabel: '',
  baseUrl: '',
  model: '',
  apiKeyConfigured: false,
  credentialStorage: ''
})
const activeRoleStatus = computed(() => aiStatus.roles?.[selectedRole.value] || emptyRoleStatus)
const writerRoleStatus = computed(() => aiStatus.roles?.writer || emptyRoleStatus)
const normalizedEndpoint = value => {
  try {
    return resolveUnifiedAIChatEndpoint(value)
  } catch {
    return String(value || '').trim().replace(/\/+$/, '')
  }
}
const resolvedEndpointPreview = computed(() => {
  try {
    return selectedRole.value === 'image'
      ? resolveUnifiedAIImageEndpoint(form.baseUrl)
      : resolveUnifiedAIChatEndpoint(form.baseUrl)
  } catch {
    return ''
  }
})
const providerConfigurationHint = computed(() => (
  unifiedAIProviderConfigurationHint(form.provider)
))
const modelPlaceholder = computed(() => ({
  qwen: '例如 qwen-plus',
  deepseek: '例如 deepseek-chat',
  doubao: '填写控制台模型或接入点 ID',
  custom: '填写兼容服务提供的模型 ID'
}[String(form.provider || '').trim().toLowerCase()] || '填写模型 ID'))
const canReuseWriterCredential = computed(() => (
  selectedRole.value !== 'writer'
  && writerRoleStatus.value.apiKeyConfigured
  && String(form.provider || '').trim().toLowerCase() === writerRoleStatus.value.provider
  && normalizedEndpoint(form.baseUrl) === normalizedEndpoint(writerRoleStatus.value.baseUrl)
))
const canReuseActiveCredential = computed(() => (
  activeRoleStatus.value.configured
  && activeRoleStatus.value.apiKeyConfigured
  && String(form.provider || '').trim().toLowerCase() === activeRoleStatus.value.provider
  && normalizedEndpoint(form.baseUrl) === normalizedEndpoint(activeRoleStatus.value.baseUrl)
))
const activeRoleStateLabel = computed(() => {
  if (!activeRoleStatus.value.effectiveConfigured) return '尚未配置'
  if (activeRoleStatus.value.fallback) return '回退到写稿模型'
  return '独立配置已生效'
})
const activeCredentialLabel = computed(() => {
  if (activeRoleStatus.value.apiKeyConfigured) return '•••••••• · 已受保护'
  if (activeRoleStatus.value.effectiveConfigured) return '由默认角色提供'
  return '未保存凭据'
})
const reviewerSimilarityRisk = computed(() => Boolean(
  aiStatus.roles?.reviewer?.sameAsWriter
))
const canClearActiveRole = computed(() => (
  selectedRole.value === 'writer'
    ? aiStatus.savedConfigured
    : activeRoleStatus.value.configured
))
const clearButtonLabel = computed(() => (
  selectedRole.value === 'writer' ? '清除全部本机配置' : '改为回退到写稿模型'
))
const saveButtonLabel = computed(() => (
  selectedRole.value === 'writer'
    ? '保存写稿模型'
    : `保存${activeRoleDefinition.value.label}`
))

const validateBaseUrl = (rule, value, callback) => {
  try {
    validateUnifiedAIProviderBinding(form.provider, value)
    callback()
  } catch (error) {
    callback(new Error(error?.message || '请输入不含账号密码的公开 HTTPS 地址'))
  }
}

const rules = {
  baseUrl: [
    { required: true, message: '请输入 API 服务地址', trigger: 'blur' },
    { validator: validateBaseUrl, trigger: 'blur' }
  ],
  model: [
    { required: true, message: '请输入模型名称', trigger: 'blur' },
    { max: 200, message: '模型名称不能超过 200 个字符', trigger: 'blur' }
  ],
  apiKey: [
    {
      validator: (rule, value, callback) => {
        const hasReusableKey = selectedRole.value === 'writer'
          ? canReuseActiveCredential.value
          : (
              canReuseActiveCredential.value
              || (form.reuseWriterCredential && canReuseWriterCredential.value)
            )
        if (!String(value || '').trim() && !hasReusableKey) {
          callback(new Error('首次配置该连接需要填写 API Key'))
          return
        }
        callback()
      },
      trigger: 'blur'
    },
    { max: 8192, message: 'API Key 格式异常', trigger: 'blur' }
  ]
}

const activeSourceLabel = computed(() => {
  if (aiStatus.source === 'saved') return 'Windows 安全配置'
  if (aiStatus.source === 'environment') return '运行环境配置'
  return '等待接入'
})
const activeSourceHint = computed(() => {
  if (aiStatus.configured) return aiStatus.model || aiStatus.providerLabel
  return '保存一次，全软件共用'
})
const sourceTone = computed(() => ({
  custom: aiStatus.source === 'saved',
  server: aiStatus.source === 'environment',
  missing: !aiStatus.configured
}))
const credentialStorageLabel = computed(() => ({
  windows_dpapi_current_user: 'Windows 当前用户专属加密',
  environment: '由运行环境安全提供',
  unavailable: '当前环境无法安全保存'
}[activeRoleStatus.value.credentialStorage] || '密钥不会通过状态接口返回'))

const engineHealthTone = computed(() => {
  if (!engineStatus.value || engineStatusError.value) return 'unknown'
  if (engineStatus.value.readiness === 'ready') return 'ready'
  if (engineStatus.value.readiness === 'degraded') return 'degraded'
  return 'unknown'
})

const engineStatusLabel = computed(() => {
  if (engineStatusError.value || !engineStatus.value) return '状态无法确认'
  if (engineStatus.value.readiness === 'ready') return '内容引擎可用'
  if (engineStatus.value.readiness === 'degraded') return '内容引擎处于降级状态'
  if (engineStatus.value.readiness === 'unavailable') return '内容引擎不可用'
  return '状态无法确认'
})

const engineStatusHint = computed(() => {
  if (engineStatusError.value) return engineStatusError.value
  if (!engineStatus.value) return '尚未取得部署环境状态。'
  if (engineStatus.value.message) return engineStatus.value.message
  if (engineStatus.value.readiness === 'ready') return '生成请求将按部署策略路由，最终结果以运行回执为准。'
  if (engineStatus.value.readiness === 'degraded') return '主引擎可能不可用，成功请求会明确标记是否使用备用引擎。'
  return '请稍后刷新；状态未知不代表服务未配置。'
})

const fallbackStatusLabel = computed(() => {
  if (!engineStatus.value || engineStatus.value.fallbackEnabled == null) return '无法确认'
  if (!engineStatus.value.fallbackEnabled) return '未启用'
  return engineStatus.value.fallbackTarget
    ? `已启用 · ${engineStatus.value.fallbackTarget}`
    : '已启用'
})

const loadActiveRoleForm = () => {
  const status = activeRoleStatus.value
  Object.assign(form, {
    provider: status.provider || writerRoleStatus.value.provider || 'qwen',
    baseUrl: status.baseUrl || writerRoleStatus.value.baseUrl || '',
    model: status.model || writerRoleStatus.value.model || '',
    apiKey: '',
    reuseWriterCredential: selectedRole.value !== 'writer'
  })
  connectionResult.value = null
  formRef.value?.clearValidate()
}

const selectRole = roleId => {
  if (!roleDefinitions.some(item => item.id === roleId)) return
  selectedRole.value = roleId
  loadActiveRoleForm()
}

const selectRoleFromRoute = () => {
  const roleId = String(route.query.role || '').trim().toLowerCase()
  if (!roleDefinitions.some(item => item.id === roleId) || selectedRole.value === roleId) return
  selectedRole.value = roleId
  loadActiveRoleForm()
}

const loadAIStatus = async () => {
  statusLoading.value = true
  statusError.value = ''
  try {
    const response = await aiConfigApi.get({ suppressGlobalError: true })
    Object.assign(aiStatus, normalizeUnifiedAIStatus(response))
    loadActiveRoleForm()
  } catch (error) {
    statusError.value = readableError(error, '统一 AI 配置状态读取失败')
  } finally {
    statusLoading.value = false
  }
}

const readableError = (error, fallback) => (
  error?.response?.data?.msg
  || error?.response?.data?.message
  || error?.message
  || fallback
)

const selectProvider = providerId => {
  Object.assign(form, applyUnifiedAIProviderPreset(form, providerId))
  form.reuseWriterCredential = selectedRole.value !== 'writer'
  connectionResult.value = null
  formRef.value?.clearValidate()
}

const formatCheckedAt = (value) => {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

const loadEngineStatus = async () => {
  engineStatusLoading.value = true
  engineStatusError.value = ''
  try {
    const response = await articleApi.getContentEngineStatus({ suppressGlobalError: true })
    const root = response.data || {}
    const payload = root.content_engine || root.engine_status || root
    const primary = payload.primary && typeof payload.primary === 'object' ? payload.primary : {}
    const fallback = payload.fallback && typeof payload.fallback === 'object' ? payload.fallback : {}
    const hasFallbackField = Object.prototype.hasOwnProperty.call(payload, 'fallback')
    engineStatus.value = {
      engine: payload.active_engine || payload.active || payload.engine || payload.name || primary.engine || '',
      version: payload.engine_version || payload.version || primary.version || '',
      readiness: String(payload.readiness || payload.status || 'unknown').toLowerCase(),
      fallbackEnabled: payload.fallback_enabled ?? fallback.enabled ?? (hasFallbackField ? Boolean(payload.fallback) : null),
      fallbackTarget: payload.fallback_target || fallback.target || fallback.engine || '',
      checkedAt: formatCheckedAt(payload.last_checked_at || payload.checked_at),
      message: payload.message || ''
    }
  } catch (error) {
    engineStatus.value = null
    engineStatusError.value = error.response?.status === 404
      ? '当前部署尚未提供引擎状态接口。'
      : '读取失败，请稍后刷新。'
  } finally {
    engineStatusLoading.value = false
  }
}

const loadSecurityStatus = async () => {
  try {
    const response = await authApi.getStatus()
    accessControlEnabled.value = Boolean(response.data?.required)
  } catch (error) {
    console.error('读取访问控制状态失败:', error)
  }
}

const saveConfig = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = selectedRole.value === 'writer'
      ? buildUnifiedAIConfigPayload(form, aiStatus)
      : buildUnifiedAIRoleConfigPayload(
          form,
          activeRoleStatus.value,
          writerRoleStatus.value
        )
    const response = selectedRole.value === 'writer'
      ? await aiConfigApi.save(payload, { suppressGlobalError: true })
      : await aiConfigApi.saveRole(selectedRole.value, payload, { suppressGlobalError: true })
    Object.assign(aiStatus, normalizeUnifiedAIStatus(response))
    loadActiveRoleForm()
    window.dispatchEvent(new CustomEvent('geo-unified-ai-config-changed'))
    ElMessage.success(`${activeRoleDefinition.value.label}已安全保存`)
  } catch (error) {
    ElMessage.error(readableError(error, '统一 AI 配置保存失败'))
  } finally {
    saving.value = false
  }
}

const testConnection = async () => {
  if (!activeRoleStatus.value.effectiveConfigured || testing.value) return
  testing.value = true
  connectionResult.value = null
  try {
    const response = await aiConfigApi.test(
      { suppressGlobalError: true },
      selectedRole.value
    )
    const data = response.data || {}
    connectionResult.value = {
      ok: Boolean(data.ok),
      message: data.message || '固定合成测试已收到模型响应',
      provider: data.provider || aiStatus.providerLabel,
      model: data.model || aiStatus.model,
      endpoint: data.endpoint || resolvedEndpointPreview.value,
      elapsedMs: Number(data.elapsed_ms) || 0
    }
    ElMessage.success(`${activeRoleDefinition.value.label}连接正常`)
  } catch (error) {
    const message = readableError(error, '统一 AI 引擎连接失败')
    connectionResult.value = { ok: false, message }
    ElMessage.error(message)
  } finally {
    testing.value = false
  }
}

const clearConfig = async () => {
  const clearingWriter = selectedRole.value === 'writer'
  try {
    await ElMessageBox.confirm(
      clearingWriter
        ? '清除后，所有角色将回退到运行环境；若环境也未配置，AI 功能会停用。'
        : `${activeRoleDefinition.value.label}将不再独立调用，并明确回退到写稿模型。`,
      clearingWriter ? '清除全部本机 AI 配置' : `清除${activeRoleDefinition.value.label}`,
      {
        confirmButtonText: '清除配置',
        cancelButtonText: '保留',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  clearing.value = true
  try {
    const response = clearingWriter
      ? await aiConfigApi.clear({ suppressGlobalError: true })
      : await aiConfigApi.clearRole(selectedRole.value, { suppressGlobalError: true })
    Object.assign(aiStatus, normalizeUnifiedAIStatus(response))
    loadActiveRoleForm()
    window.dispatchEvent(new CustomEvent('geo-unified-ai-config-changed'))
    ElMessage.success(
      clearingWriter
        ? (aiStatus.configured ? '本机配置已清除，已回退到运行环境' : '统一 AI 配置已清除')
        : `${activeRoleDefinition.value.label}已回退到写稿模型`
    )
  } catch (error) {
    ElMessage.error(readableError(error, '统一 AI 配置清除失败'))
  } finally {
    clearing.value = false
  }
}

const logoutSession = async () => {
  await authApi.logout()
  window.dispatchEvent(new CustomEvent('geo-auth-required'))
}

onMounted(() => {
  selectRoleFromRoute()
  loadAIStatus()
  loadSecurityStatus()
  loadEngineStatus()
})

watch(() => route.query.role, selectRoleFromRoute)
</script>

<style lang="scss" scoped>
.settings-page {
  --ink: var(--geo-ink);
  --teal: var(--geo-ink);
  --signal: var(--geo-line-strong);
  --mist: var(--geo-soft);
  --amber: var(--geo-warning);
  --paper: #ffffff;
  max-width: 1420px;
  margin: 0 auto;
  color: var(--ink);
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
}

.settings-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 36px;
  padding: 34px 38px;
  border: 1px solid var(--geo-line);
  border-radius: 12px;
  background: var(--paper);
}

.hero-copy {
  max-width: 760px;

  h1 {
    margin: 6px 0 12px;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: clamp(34px, 4vw, 54px);
    font-weight: 760;
    letter-spacing: -0.045em;
    line-height: 1.04;
  }

  p {
    color: var(--geo-muted);
    font-size: 14px;
    line-height: 1.8;
  }
}

.eyebrow,
.section-kicker,
.active-source > span,
.guardrail-card span,
.node-index {
  color: var(--teal);
  font: 700 10px/1.2 "Cascadia Mono", monospace;
  letter-spacing: 0.14em;
}

.active-source {
  min-width: 230px;
  padding: 18px 20px;
  border: 1px solid var(--geo-line);
  border-radius: 10px;
  background: var(--geo-canvas);

  strong,
  small {
    display: block;
  }

  strong {
    margin-top: 8px;
    font-size: 21px;
  }

  small {
    margin-top: 5px;
    color: var(--geo-muted);
  }

  &.custom,
  &.server { border-color: var(--geo-line-strong); }
  &.missing { border-color: rgba(138, 100, 36, 0.55); }
}

.settings-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, 0.65fr);
  gap: 20px;
  margin-top: 20px;
}

.config-card,
.engine-status-card,
.routing-card,
.guardrail-card {
  border: 1px solid var(--geo-line);
  background: var(--paper);
  box-shadow: 0 12px 36px rgba(17, 18, 20, 0.04);
}

.config-card {
  padding: 30px;
  border-radius: 12px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--geo-line);

  h2 {
    margin-top: 6px;
    font-size: 25px;
  }
}

.engine-bus {
  position: relative;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 22px 0 20px;
  overflow: hidden;
  border: 1px solid var(--geo-line);
  border-radius: 9px;
  background: var(--geo-line);
}

.bus-line {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 3px;
  background: #e1e5e4;

  i {
    display: block;
    width: 34%;
    height: 100%;
    background: var(--geo-subtle);
    transition: width 280ms ease, background-color 280ms ease;
  }

  i.live {
    width: 100%;
    background: var(--geo-success);
  }
}

.engine-bus article {
  min-width: 0;
  padding: 18px 16px 15px;
  background: #fff;

  span {
    color: var(--geo-muted);
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: .11em;
  }

  strong,
  small { display: block; }

  strong {
    overflow: hidden;
    margin-top: 8px;
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  small {
    margin-top: 5px;
    color: var(--geo-muted);
    font-size: 10px;
  }
}

.engine-bus .bus-output {
  color: #fff;
  background: var(--geo-ink);

  span,
  small { color: rgba(255, 255, 255, .64); }
}

.role-switcher {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0 0 20px;
  border: 1px solid var(--geo-line);
  border-radius: 10px;
  background: var(--geo-line);
  gap: 1px;
  overflow: hidden;
}

.role-switcher button {
  position: relative;
  display: grid;
  min-width: 0;
  min-height: 84px;
  align-content: center;
  gap: 5px;
  padding: 13px 14px;
  border: 0;
  color: var(--geo-ink);
  text-align: left;
  background: #fff;
  cursor: pointer;

  &::before {
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    background: transparent;
    content: "";
  }

  span {
    color: var(--geo-muted);
    font: 700 8px/1.2 "Cascadia Mono", monospace;
    letter-spacing: .12em;
  }

  strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  small { color: var(--geo-muted); font-size: 10px; }

  &.active { background: var(--geo-soft); }
  &.active::before { background: var(--geo-ink); }
  &:focus-visible { z-index: 1; outline: 2px solid var(--geo-line-strong); outline-offset: -2px; }
}

.role-risk,
.role-runtime-note {
  display: grid;
  grid-template-columns: minmax(150px, .42fr) minmax(0, 1fr);
  gap: 14px;
  margin: 0 0 18px;
  padding: 13px 15px;
  border-left: 3px solid var(--geo-warning);
  color: #684b1d;
  background: #fff9ee;
  font-size: 11px;
  line-height: 1.55;
}

.role-runtime-note {
  border-left-color: var(--geo-line-strong);
  color: var(--geo-ink);
  background: var(--geo-soft);
}

.provider-picker {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 20px;
}

.provider-picker button {
  display: flex;
  min-width: 0;
  min-height: 54px;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 13px 12px;
  border: 1px solid var(--geo-line);
  border-radius: 8px;
  color: var(--geo-ink);
  background: #fff;
  cursor: pointer;

  strong { font-size: 11px; }
  small {
    color: var(--geo-muted);
    font: 700 7px/1.2 "Cascadia Mono", monospace;
  }

  &.active {
    border-color: var(--geo-ink);
    color: #fff;
    background: var(--geo-ink);
  }

  &.active small { color: rgba(255, 255, 255, .58); }
  &:focus-visible { outline: 2px solid var(--geo-line-strong); outline-offset: 2px; }
}

.config-error,
.connection-result {
  margin: 18px 0;
  padding: 13px 15px;
  border-left: 3px solid var(--geo-warning);
  color: #6c4d1b;
  background: #fff9ee;
  font-size: 11px;
}

.config-error button {
  margin-left: 8px;
  border: 0;
  color: inherit;
  background: transparent;
  font-weight: 700;
  text-decoration: underline;
  cursor: pointer;
}

.connection-result {
  display: grid;
  gap: 5px;
  border-left-color: var(--geo-danger);
  color: var(--geo-ink);
  background: #fff5f3;

  span {
    color: var(--geo-muted);
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: .1em;
  }

  &.success {
    border-left-color: var(--geo-success);
    background: var(--geo-soft);
  }

  small { color: var(--geo-muted); }
}

.test-boundary {
  margin-top: 13px;
  color: var(--geo-muted);
  font-size: 10px;
  line-height: 1.6;
}

.config-form {
  padding-top: 24px;
}

.credential-reuse {
  margin: 0 0 12px;
  color: var(--geo-muted);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.field-help {
  margin-top: 7px;
  color: #829096;
  font-size: 11px;
  line-height: 1.5;
}

.endpoint-preview {
  max-width: 100%;

  code {
    overflow-wrap: anywhere;
    color: var(--geo-ink);
    font-family: "Cascadia Mono", monospace;
  }
}

.provider-hint { margin-top: 3px; }

:deep(.el-input__wrapper) {
  min-height: 44px;
  border-radius: 8px;
}

.session-note {
  display: flex;
  gap: 13px;
  padding: 15px 17px;
  border-left: 3px solid var(--geo-ink);
  background: var(--geo-soft);

  > .el-icon {
    margin-top: 2px;
    color: var(--teal);
    font-size: 20px;
  }

  strong { font-size: 13px; }
  p {
    margin-top: 4px;
    color: #6f7f86;
    font-size: 12px;
    line-height: 1.6;
  }
}

.form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 24px;

  :deep(.el-button--primary) {
    --el-button-bg-color: var(--teal);
    --el-button-border-color: var(--teal);
    --el-button-hover-bg-color: var(--geo-action-hover);
    --el-button-hover-border-color: var(--geo-action-hover);
    border-radius: 8px;
  }
}

.routing-column {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.engine-status-card {
  padding: 27px;
  border-radius: 12px;
}

.engine-status-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;

  h2 {
    margin-top: 6px;
    font-size: 21px;
  }
}

.engine-status-loading {
  padding: 28px 0 4px;
  color: var(--geo-muted);
  font-size: 12px;
}

.engine-status-body {
  padding-top: 22px;
}

.engine-health-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--geo-line);

  > div {
    display: flex;
    min-width: 0;
    flex-direction: column;
    gap: 4px;
  }

  strong { font-size: 14px; }
  small { color: var(--geo-muted); font-size: 11px; line-height: 1.5; }
}

.health-dot {
  width: 10px;
  height: 10px;
  flex: 0 0 10px;
  border-radius: 50%;
  background: var(--geo-subtle);

  &.ready { background: var(--geo-success); }
  &.degraded { background: var(--geo-warning); }
  &.unknown { background: var(--geo-subtle); }
}

.engine-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin-top: 18px;
  overflow: hidden;
  border: 1px solid var(--geo-line);
  border-radius: 8px;
  background: var(--geo-line);

  > div {
    min-width: 0;
    padding: 12px;
    background: #fff;
  }

  dt {
    color: var(--geo-muted);
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  dd {
    overflow: hidden;
    margin-top: 6px;
    color: var(--geo-ink);
    font-size: 12px;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.engine-managed-note {
  margin-top: 14px;
  color: var(--geo-muted);
  font-size: 10px;
  line-height: 1.55;
}

.routing-card {
  padding: 27px;
  border-radius: 12px;

  h2 {
    margin-top: 6px;
    font-size: 21px;
  }
}

.routing-rail {
  position: relative;
  display: grid;
  gap: 12px;
  margin-top: 22px;

  &::before {
    position: absolute;
    top: 26px;
    bottom: 26px;
    left: 17px;
    width: 1px;
    background: var(--geo-line);
    content: "";
  }
}

.route-node {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) 22px;
  gap: 12px;
  align-items: start;
  padding: 16px 14px 16px 0;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--geo-canvas);

  &.active {
    border-color: var(--geo-line-strong);
    background: var(--geo-soft);
    box-shadow: inset 3px 0 var(--geo-ink);
  }

  strong { font-size: 13px; }
  p {
    margin-top: 5px;
    color: #728188;
    font-size: 11px;
    line-height: 1.55;
  }

  > .el-icon {
    color: #668087;
    font-size: 18px;
  }
}

.node-index {
  position: relative;
  z-index: 1;
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid #bdd1d0;
  border-radius: 50%;
  background: #fff;
  font-size: 9px;
}

.provider-node { background: var(--geo-ink); }
.provider-node strong { color: #f4fbfa; }
.provider-node p { color: rgba(224, 241, 239, 0.65); }
.provider-node > .el-icon { color: #fff; }

.guardrail-card {
  display: flex;
  gap: 15px;
  padding: 22px;
  border-radius: 12px;

  > .el-icon {
    flex: 0 0 auto;
    color: var(--amber);
    font-size: 24px;
  }

  h3 {
    margin: 6px 0;
    font-size: 15px;
  }

  p {
    color: #718087;
    font-size: 11px;
    line-height: 1.65;
  }

  :deep(.el-button) {
    margin-top: 9px;
    padding-left: 0;
    color: var(--teal);
  }
}

@media (max-width: 980px) {
  .settings-layout { grid-template-columns: 1fr; }
}

@media (max-width: 700px) {
  .settings-hero,
  .form-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .settings-hero,
  .config-card { padding: 24px 20px; }
  .active-source { min-width: 0; }
  .engine-bus,
  .role-switcher,
  .provider-picker { grid-template-columns: 1fr; }
  .role-risk,
  .role-runtime-note { grid-template-columns: 1fr; gap: 4px; }
  .field-grid { grid-template-columns: 1fr; gap: 0; }
  .form-actions > div { display: grid; gap: 8px; }
  .form-actions :deep(.el-button) { margin-left: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .bus-line i { transition: none; }
}
</style>
