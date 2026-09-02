<template>
  <main class="settings-page">
    <section class="settings-hero">
      <div class="hero-copy">
        <span class="eyebrow">MODEL ROUTING / SETTINGS</span>
        <h1>模型连接台</h1>
        <p>
          为当前浏览器指定 OpenAI-compatible API。自定义配置只影响 AI 内容生成和 GEO 优化，
          不会改动服务器默认值。
        </p>
      </div>
      <div :class="['active-source', sourceTone]">
        <span>当前请求来源</span>
        <strong>{{ activeSourceLabel }}</strong>
        <small>{{ activeSourceHint }}</small>
      </div>
    </section>

    <div class="settings-layout">
      <section class="config-card">
        <header class="section-heading">
          <div>
            <span class="section-kicker">BROWSER OVERRIDE</span>
            <h2>自定义 AI API</h2>
          </div>
          <el-tag :type="customActive ? 'success' : 'info'" effect="plain">
            {{ customActive ? '当前会话已启用' : '未启用' }}
          </el-tag>
        </header>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          class="config-form"
        >
          <el-form-item label="API 服务地址" prop="baseUrl">
            <el-input
              v-model="form.baseUrl"
              placeholder="https://api.example.com/v1"
              autocomplete="url"
              clearable
            >
              <template #prefix><el-icon><Connection /></el-icon></template>
            </el-input>
            <p class="field-help">必须使用公开 HTTPS 地址；系统会自动补齐 /chat/completions。</p>
          </el-form-item>

          <div class="field-grid">
            <el-form-item label="模型名称" prop="model">
              <el-input
                v-model="form.model"
                placeholder="例如 qwen3.8-max"
                autocomplete="off"
                clearable
              >
                <template #prefix><el-icon><Cpu /></el-icon></template>
              </el-input>
            </el-form-item>

            <el-form-item label="API Key（仅当前标签页）" prop="apiKey">
              <el-input
                v-model="form.apiKey"
                type="password"
                placeholder="输入服务商提供的 API Key"
                autocomplete="new-password"
                show-password
                clearable
              >
                <template #prefix><el-icon><Key /></el-icon></template>
              </el-input>
            </el-form-item>
          </div>
        </el-form>

        <div class="session-note">
          <el-icon><Timer /></el-icon>
          <div>
            <strong>密钥不会长期保存</strong>
            <p>服务地址和模型名留在本浏览器；API Key 只保存在当前标签页会话，关闭标签页后自动清除。</p>
          </div>
        </div>

        <footer class="form-actions">
          <el-button :disabled="!hasDraft" @click="clearConfig">清除自定义配置</el-button>
          <div>
            <el-button @click="goToCreation">前往内容创作</el-button>
            <el-button type="primary" :loading="saving" @click="saveConfig">
              保存到当前会话
            </el-button>
          </div>
        </footer>
      </section>

      <aside class="routing-column">
        <section class="routing-card">
          <header>
            <span class="section-kicker">REQUEST PRIORITY</span>
            <h2>请求如何选择配置</h2>
          </header>

          <div class="routing-rail">
            <article :class="['route-node', { active: customActive }]">
              <span class="node-index">01</span>
              <div>
                <strong>浏览器自定义配置</strong>
                <p>三项填写完整时优先使用，只影响这个浏览器会话。</p>
              </div>
              <el-icon><Monitor /></el-icon>
            </article>
            <article :class="['route-node', { active: !customActive && serverConfigured }]">
              <span class="node-index">02</span>
              <div>
                <strong>服务器默认配置</strong>
                <p>{{ serverConfigured ? 'Vercel Production 已配置，可作为默认连接。' : '服务器尚未配置完整的模型变量。' }}</p>
              </div>
              <el-icon><Cloudy /></el-icon>
            </article>
            <article class="route-node provider-node">
              <span class="node-index">03</span>
              <div>
                <strong>OpenAI-compatible 服务</strong>
                <p>后端拼接标准 chat/completions 路径并解析模型输出。</p>
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
                : '自定义地址只允许 HTTPS，并拒绝本机、私网、保留地址以及 URL 内嵌凭据。' }}
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
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Cloudy,
  Connection,
  Cpu,
  Key,
  Lock,
  Monitor,
  Promotion,
  SwitchButton,
  Timer
} from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { authApi } from '@/api/auth'
import {
  clearCustomAIConfig,
  getAIConfigDraft,
  getCustomAIConfig,
  saveCustomAIConfig
} from '@/utils/aiConfig'

const router = useRouter()
const formRef = ref(null)
const saving = ref(false)
const serverConfigured = ref(false)
const accessControlEnabled = ref(false)
const activeCustomModel = ref(getCustomAIConfig()?.model || '')
const form = reactive({
  baseUrl: '',
  apiKey: '',
  model: ''
})

const validateBaseUrl = (rule, value, callback) => {
  try {
    const parsed = new URL(String(value || '').trim())
    const host = parsed.hostname.toLowerCase().replace(/\.$/, '')
    const isInternalHost = (
      host === 'localhost'
      || host.endsWith('.localhost')
      || host.endsWith('.local')
      || host.endsWith('.internal')
      || !host.includes('.')
    )
    if (
      parsed.protocol !== 'https:'
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || isInternalHost
    ) {
      throw new Error('invalid provider URL')
    }
    callback()
  } catch {
    callback(new Error('请输入不含账号密码的公开 HTTPS 地址'))
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
    { required: true, message: '请输入 API Key', trigger: 'blur' },
    { max: 8192, message: 'API Key 格式异常', trigger: 'blur' }
  ]
}

const customActive = ref(Boolean(getCustomAIConfig()))
const hasDraft = computed(() => Boolean(form.baseUrl || form.apiKey || form.model))
const activeSourceLabel = computed(() => {
  if (customActive.value) return '浏览器自定义'
  if (serverConfigured.value) return '服务器默认'
  return '尚未配置'
})
const activeSourceHint = computed(() => {
  if (customActive.value) return activeCustomModel.value
  if (serverConfigured.value) return '由部署环境安全提供'
  return '填写左侧三项后启用'
})
const sourceTone = computed(() => ({
  custom: customActive.value,
  server: !customActive.value && serverConfigured.value,
  missing: !customActive.value && !serverConfigured.value
}))

const loadDraft = () => {
  const draft = getAIConfigDraft()
  Object.assign(form, draft)
}

const loadServerStatus = async () => {
  try {
    const response = await articleApi.getAIConfigStatus()
    serverConfigured.value = Boolean(response.data?.server_configured)
  } catch (error) {
    console.error('读取 AI 配置状态失败:', error)
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
    saveCustomAIConfig(form)
    customActive.value = true
    activeCustomModel.value = form.model.trim()
    ElMessage.success('自定义 AI API 已用于当前浏览器会话')
  } catch (error) {
    ElMessage.error(error.message || '浏览器无法保存当前配置')
  } finally {
    saving.value = false
  }
}

const clearConfig = async () => {
  try {
    await ElMessageBox.confirm(
      '清除后，AI 请求将自动回退到服务器默认配置。',
      '清除自定义配置',
      {
        confirmButtonText: '清除配置',
        cancelButtonText: '保留',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  clearCustomAIConfig()
  customActive.value = false
  activeCustomModel.value = ''
  Object.assign(form, { baseUrl: '', apiKey: '', model: '' })
  formRef.value?.clearValidate()
  ElMessage.success('自定义配置已清除')
}

const goToCreation = () => router.push('/content-create')

const logoutSession = async () => {
  await authApi.logout()
  window.dispatchEvent(new CustomEvent('geo-auth-required'))
}

onMounted(() => {
  loadDraft()
  loadServerStatus()
  loadSecurityStatus()
})
</script>

<style lang="scss" scoped>
.settings-page {
  --ink: #17222b;
  --teal: #0d5c63;
  --signal: #39b8b2;
  --mist: #edf3f3;
  --amber: #e8a33a;
  --paper: #ffffff;
  max-width: 1420px;
  margin: 0 auto;
  color: var(--ink);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", sans-serif;
}

.settings-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 36px;
  padding: 34px 38px;
  border: 1px solid #d5e1e1;
  border-radius: 18px 5px 18px 5px;
  background:
    linear-gradient(110deg, rgba(57, 184, 178, 0.11), transparent 46%),
    var(--paper);
}

.hero-copy {
  max-width: 760px;

  h1 {
    margin: 6px 0 12px;
    font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
    font-size: clamp(34px, 4vw, 54px);
    font-weight: 760;
    letter-spacing: -0.045em;
    line-height: 1.04;
  }

  p {
    color: #607078;
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
  border: 1px solid #cbdada;
  border-radius: 12px 4px 12px 4px;
  background: #f8fbfb;

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
    color: #728188;
  }

  &.custom { border-color: rgba(57, 184, 178, 0.75); }
  &.server { border-color: rgba(13, 92, 99, 0.45); }
  &.missing { border-color: rgba(232, 163, 58, 0.58); }
}

.settings-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, 0.65fr);
  gap: 20px;
  margin-top: 20px;
}

.config-card,
.routing-card,
.guardrail-card {
  border: 1px solid #d7e2e2;
  background: var(--paper);
  box-shadow: 0 12px 32px rgba(23, 34, 43, 0.04);
}

.config-card {
  padding: 30px;
  border-radius: 16px 5px 16px 5px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 22px;
  border-bottom: 1px solid #e1e9e9;

  h2 {
    margin-top: 6px;
    font-size: 25px;
  }
}

.config-form {
  padding-top: 24px;
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

:deep(.el-input__wrapper) {
  min-height: 44px;
  border-radius: 9px 3px 9px 3px;
}

.session-note {
  display: flex;
  gap: 13px;
  padding: 15px 17px;
  border-left: 3px solid var(--signal);
  background: #f1f7f7;

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
    --el-button-hover-bg-color: #14747b;
    --el-button-hover-border-color: #14747b;
    border-radius: 9px 3px 9px 3px;
  }
}

.routing-column {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.routing-card {
  padding: 27px;
  border-radius: 5px 16px 5px 16px;

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
    background: #bfd1d1;
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
  border-radius: 10px 3px 10px 3px;
  background: #f7fafa;

  &.active {
    border-color: rgba(57, 184, 178, 0.55);
    background: #edf8f7;
    box-shadow: inset 3px 0 var(--signal);
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

.provider-node { background: #102b31; }
.provider-node strong { color: #f4fbfa; }
.provider-node p { color: rgba(224, 241, 239, 0.65); }
.provider-node > .el-icon { color: #64d6cf; }

.guardrail-card {
  display: flex;
  gap: 15px;
  padding: 22px;
  border-radius: 14px 4px 14px 4px;

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
  .field-grid { grid-template-columns: 1fr; gap: 0; }
  .form-actions > div { display: grid; gap: 8px; }
  .form-actions :deep(.el-button) { margin-left: 0; }
}
</style>
