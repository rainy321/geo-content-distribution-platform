<template>
  <main class="access-page">
    <section class="brand-panel" aria-label="GEO 内容运营工作区">
      <div class="brand-lockup">
        <div class="brand-mark">G</div>
        <div>
          <strong>GEO 内容引擎</strong>
          <span>CONTROL PLANE</span>
        </div>
      </div>

      <div class="brand-thesis">
        <span class="eyebrow">OPERATOR BOUNDARY</span>
        <h1><span>内容可以公开，</span><span>操作权限不能。</span></h1>
        <p>输入运营访问口令后，才会开放模型调用、项目修改和媒体发布控制。</p>
      </div>

      <ol class="workflow-rail" aria-label="受保护的内容流程">
        <li><span>01</span><strong>生成</strong><small>模型额度</small></li>
        <li><span>02</span><strong>优化</strong><small>品牌资产</small></li>
        <li><span>03</span><strong>分发</strong><small>媒体账号</small></li>
      </ol>
    </section>

    <section class="entry-panel">
      <div class="entry-card">
        <header>
          <span class="eyebrow">AUTHORIZED OPERATOR</span>
          <h2>进入运营工作区</h2>
          <p>访问口令由部署环境校验，不会保存到浏览器本地存储。</p>
        </header>

        <el-form @submit.prevent="unlock">
          <el-form-item label="运营访问口令">
            <el-input
              ref="passwordInput"
              v-model="password"
              type="password"
              autocomplete="current-password"
              placeholder="输入访问口令"
              show-password
              :disabled="submitting"
              @keyup.enter="unlock"
            >
              <template #prefix><el-icon><Key /></el-icon></template>
            </el-input>
          </el-form-item>
          <p v-if="errorMessage" class="entry-error" role="alert">
            {{ errorMessage }}
          </p>
          <el-button
            type="primary"
            native-type="submit"
            :loading="submitting"
            :disabled="!password.trim()"
            @click="unlock"
          >
            验证并进入
          </el-button>
        </el-form>

        <footer>
          <el-icon><Lock /></el-icon>
          <span>会话使用 HttpOnly 安全 Cookie；退出或过期后需要重新验证。</span>
        </footer>
      </div>
    </section>
  </main>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { Key, Lock } from '@element-plus/icons-vue'
import { authApi } from '@/api/auth'

const emit = defineEmits(['authenticated'])
const password = ref('')
const passwordInput = ref(null)
const submitting = ref(false)
const errorMessage = ref('')

const unlock = async () => {
  if (!password.value.trim() || submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const response = await authApi.login(password.value)
    if (!response.data?.authenticated) {
      throw new Error('访问口令校验失败')
    }
    password.value = ''
    emit('authenticated')
  } catch (error) {
    errorMessage.value = error?.response?.data?.msg || error?.message || '访问口令校验失败'
    await nextTick()
    passwordInput.value?.focus()
  } finally {
    submitting.value = false
  }
}

onMounted(() => passwordInput.value?.focus())
</script>

<style lang="scss" scoped>
.access-page {
  --ink: #17222b;
  --deep: #102b31;
  --teal: #0d5c63;
  --signal: #64d6cf;
  display: grid;
  min-height: 100vh;
  grid-template-columns: minmax(420px, 0.9fr) minmax(460px, 1.1fr);
  background: #f3f6f6;
  color: var(--ink);
}

.brand-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 100vh;
  padding: 52px clamp(40px, 6vw, 92px);
  overflow: hidden;
  background:
    linear-gradient(rgba(100, 214, 207, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(100, 214, 207, 0.055) 1px, transparent 1px),
    var(--deep);
  background-size: 44px 44px;
  color: #f7fbfb;

  &::after {
    position: absolute;
    right: -100px;
    bottom: 18%;
    width: 260px;
    height: 260px;
    border: 1px solid rgba(100, 214, 207, 0.22);
    border-radius: 50%;
    box-shadow: 0 0 0 42px rgba(100, 214, 207, 0.035), 0 0 0 84px rgba(100, 214, 207, 0.025);
    content: '';
  }
}

.brand-lockup {
  display: flex;
  align-items: center;
  gap: 13px;

  .brand-mark {
    display: grid;
    width: 38px;
    height: 38px;
    place-items: center;
    border: 1px solid var(--signal);
    border-radius: 8px 2px 8px 2px;
    color: var(--signal);
    font: 800 20px/1 "Arial Narrow", sans-serif;
  }

  strong,
  span { display: block; }
  strong { font-size: 15px; }
  span {
    margin-top: 4px;
    color: rgba(214, 240, 238, 0.56);
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.17em;
  }
}

.eyebrow {
  color: var(--signal);
  font: 700 10px/1.2 "Cascadia Mono", monospace;
  letter-spacing: 0.16em;
}

.brand-thesis {
  position: relative;
  z-index: 1;
  max-width: 570px;

  h1 {
    margin: 14px 0 20px;
    font-family: "Arial Narrow", "Microsoft YaHei", sans-serif;
    font-size: clamp(42px, 4.2vw, 60px);
    font-weight: 780;
    letter-spacing: -0.055em;
    line-height: 1.08;

    span {
      display: block;
      white-space: nowrap;
    }
  }

  p {
    max-width: 480px;
    color: rgba(225, 241, 240, 0.67);
    font-size: 14px;
    line-height: 1.8;
  }
}

.workflow-rail {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  padding: 0;
  list-style: none;

  li {
    display: grid;
    gap: 5px;
    padding: 17px 18px;
    border-top: 1px solid rgba(100, 214, 207, 0.28);
    border-right: 1px solid rgba(100, 214, 207, 0.15);
    background: rgba(4, 22, 26, 0.24);
  }
  li:last-child { border-right: 0; }
  span {
    color: var(--signal);
    font: 700 9px/1 "Cascadia Mono", monospace;
  }
  strong { font-size: 13px; }
  small { color: rgba(225, 241, 240, 0.48); font-size: 10px; }
}

.entry-panel {
  display: grid;
  min-height: 100vh;
  padding: 36px;
  place-items: center;
}

.entry-card {
  width: min(100%, 470px);
  padding: 42px;
  border: 1px solid #d4e0e0;
  border-radius: 18px 5px 18px 5px;
  background: #fff;
  box-shadow: 0 28px 70px rgba(23, 34, 43, 0.1);

  header {
    padding-bottom: 27px;
    border-bottom: 1px solid #e0e8e8;
  }
  h2 {
    margin: 9px 0 11px;
    font-size: 30px;
    letter-spacing: -0.035em;
  }
  header p {
    color: #6e7c83;
    font-size: 13px;
    line-height: 1.7;
  }
  form { padding-top: 27px; }
  :deep(.el-input__wrapper) {
    min-height: 48px;
    border-radius: 9px 3px 9px 3px;
  }
  :deep(.el-button--primary) {
    width: 100%;
    min-height: 46px;
    margin-top: 7px;
    border-color: var(--teal);
    border-radius: 9px 3px 9px 3px;
    background: var(--teal);
  }
  :deep(.el-button--primary:hover),
  :deep(.el-button--primary:focus-visible) {
    border-color: #14747b;
    background: #14747b;
  }
  footer {
    display: flex;
    gap: 10px;
    margin-top: 24px;
    padding-top: 19px;
    border-top: 1px solid #e0e8e8;
    color: #78878d;
    font-size: 11px;
    line-height: 1.55;
  }
  footer .el-icon { flex: 0 0 auto; margin-top: 2px; color: #e8a33a; }
}

.entry-error {
  margin: -4px 0 10px;
  color: #b42318;
  font-size: 12px;
}

@media (max-width: 880px) {
  .access-page { grid-template-columns: 1fr; }
  .brand-panel {
    min-height: 340px;
    padding: 32px 28px;
  }
  .brand-thesis h1 { font-size: clamp(36px, 9vw, 50px); }
  .workflow-rail { margin-top: 42px; }
  .entry-panel { min-height: auto; padding: 32px 20px 54px; }
}

@media (max-width: 520px) {
  .brand-panel { min-height: 330px; }
  .brand-thesis p { font-size: 12px; }
  .workflow-rail li { padding: 13px 10px; }
  .entry-card { padding: 29px 22px; }
}
</style>
