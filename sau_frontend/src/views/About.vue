<template>
  <main class="product-about">
    <header class="identity-panel">
      <div class="identity-copy">
        <div class="product-mark" aria-hidden="true"><i /><span /></div>
        <p class="eyebrow">GEO DESKTOP / PRODUCT IDENTITY</p>
        <h1>GEO 内容增长工作台</h1>
        <p>在一台 Windows 设备上管理品牌事实、观察 AI 回答中的品牌可见度，并把证据转成可复核的内容资产。</p>
      </div>
      <div class="version-block">
        <span>VERSION</span>
        <strong>1.0.0</strong>
        <small>PORTABLE BETA · UNSIGNED</small>
      </div>
    </header>

    <section class="status-ledger" aria-label="当前产品边界">
      <article>
        <span>DATA</span>
        <strong>本机保存</strong>
        <p>独立于正式网站与其他电脑</p>
      </article>
      <article>
        <span>ACCOUNT</span>
        <strong>本机管理员</strong>
        <p>不是云端统一账号</p>
      </article>
      <article :class="['publish-boundary', `is-${publishingState}`]">
        <span>PUBLISH</span>
        <strong>{{ publishingLabel }}</strong>
        <p>{{ publishingNote }}</p>
      </article>
      <article>
        <span>PACKAGE</span>
        <strong>便携 Beta</strong>
        <p>当前构建尚未代码签名</p>
      </article>
    </section>

    <div class="about-grid">
      <section class="about-panel purpose-panel" aria-labelledby="purpose-title">
        <div class="section-heading">
          <span>01 / PRODUCT</span>
          <h2 id="purpose-title">这套软件解决什么问题</h2>
        </div>
        <p class="lead-copy">它不是网站快捷方式，也不只是自动发布器。它首先是一套可追溯的 GEO 内容工作台。</p>
        <ul class="capability-list">
          <li>
            <span>品牌底稿</span>
            <p>维护品牌名称、产品、官网、关键词和竞品，作为生成与分析的统一口径。</p>
          </li>
          <li>
            <span>AI 可见度</span>
            <p>对粘贴的 AI 回答执行本地规则分析，输出提及率、SOV、位次、引用和逐样本证据。</p>
          </li>
          <li>
            <span>本机历史</span>
            <p>手工导入回答只在本机分析；受控采样仅在你预览问题并明确确认后，把该问题原文发送给当前配置模型。</p>
          </li>
          <li>
            <span>内容资产</span>
            <p>生成、评分、修订并保存内容；使用外部生成模型时，以系统设置中的模型配置为准。</p>
          </li>
        </ul>
      </section>

      <section class="about-panel account-panel" aria-labelledby="account-title">
        <div class="section-heading inverse">
          <span>02 / LOCAL ACCOUNT</span>
          <h2 id="account-title">账号只属于这台设备</h2>
        </div>
        <div class="account-flow">
          <div><span>首次启动</span><strong>设置本机管理员口令</strong></div>
          <i aria-hidden="true">→</i>
          <div><span>后续使用</span><strong>通过本机会话进入工作台</strong></div>
        </div>
        <p>口令不会以明文保存，只保存加盐摘要。当前版本没有口令找回功能；它也不会自动创建云端账户或同步到其他电脑。</p>
      </section>
    </div>

    <section class="data-panel" aria-labelledby="data-title">
      <div class="section-heading">
        <span>03 / DATA OWNERSHIP</span>
        <h2 id="data-title">数据、备份与外部边界</h2>
        <p>默认数据目录：<code>%LOCALAPPDATA%\GEOContentPlatform</code></p>
      </div>

      <div class="data-rows">
        <article>
          <span class="row-code">LOCAL</span>
          <div><strong>保存在本机</strong><p>数据库、品牌项目、可见度回答与历史、素材、媒体账号会话和运行日志。</p></div>
          <small>当前 Windows 用户目录</small>
        </article>
        <article>
          <span class="row-code">MODEL</span>
          <div><strong>按功能区分外部调用</strong><p>手工导入的 AI 可见度分析只在本机运行；真实采样和内容生成仅在你主动确认后使用系统设置中的统一模型。</p></div>
          <small>使用前核对模型设置</small>
        </article>
        <article>
          <span class="row-code">BACKUP</span>
          <div><strong>即时数据库备份 + 完整目录迁移</strong><p>顶部“立即备份”会生成并校验 SQLite 快照；迁移整套工作区时仍需退出软件后复制完整目录。</p></div>
          <small>保留最近 5 份 · 无云备份</small>
        </article>
      </div>
    </section>

    <section :class="['release-warning', `is-${publishingState}`]" aria-labelledby="release-title">
      <span class="warning-mark" aria-hidden="true">!</span>
      <div>
        <p class="eyebrow">04 / RELEASE STATUS</p>
        <h2 id="release-title">当前是未签名的 Windows 便携 Beta</h2>
        <p>{{ releaseBoundaryCopy }}</p>
      </div>
    </section>

    <footer class="about-footer">
      <span>GEO CONTENT WORKBENCH</span>
      <p>版本 1.3.1 · 本机优先 · 可审计证据 · {{ publishingFooter }}</p>
    </footer>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive } from 'vue'

const runtimeStatus = reactive({
  real_publishing_authorized: false,
  real_publishing_enabled: false,
  publishing_runtime: {
    ready: false,
    browser_ready: false,
    missing_modules: []
  }
})

const publishingState = computed(() => {
  if (!runtimeStatus.real_publishing_authorized) return 'locked'
  if (runtimeStatus.real_publishing_enabled && runtimeStatus.publishing_runtime?.ready) return 'enabled'
  return 'blocked'
})

const publishingLabel = computed(() => ({
  enabled: '真实发布已开启',
  blocked: '运行时未就绪',
  locked: '安全构建锁定'
}[publishingState.value]))

const publishingNote = computed(() => {
  if (publishingState.value === 'enabled') return '绑定账号后进行单任务或明确批次强确认'
  if (publishingState.value === 'blocked') {
    const runtime = runtimeStatus.publishing_runtime || {}
    if (runtime.browser_ready === false) return '已授权，尚未检测到 Chrome'
    if (Array.isArray(runtime.missing_modules) && runtime.missing_modules.length) return '已授权，发布组件不完整'
    return '已授权，发布组件探针未通过'
  }
  return '媒体账号与真实发布未授权'
})

const releaseBoundaryCopy = computed(() => {
  const unsigned = 'Windows 可能显示来源或安全提示。只运行从可信渠道取得并核对校验值的安装包。'
  if (publishingState.value === 'enabled') {
    return `${unsigned} 本机构建已开启真实发布；发布前必须核对目标平台、账号、正文与素材，并完成单任务或明确批次强确认。批次会逐项执行且不会因单项失败停止；平台接受内容后不能依靠本软件撤回。`
  }
  if (publishingState.value === 'blocked') {
    return `${unsigned} 本机构建已授权真实发布，但 Chrome 或发布组件尚未通过探针；当前不能创建或执行真实发布任务。`
  }
  return `${unsigned} 当前是安全构建，媒体账号与真实发布入口保持锁定，不会把内容发送到媒体平台。`
})

const publishingFooter = computed(() => ({
  enabled: '真实发布已开启',
  blocked: '发布运行时未就绪',
  locked: '真实发布锁定'
}[publishingState.value]))

const loadRuntimeStatus = async () => {
  const api = window.pywebview?.api
  if (!api?.get_runtime_status) return
  try {
    Object.assign(runtimeStatus, await api.get_runtime_status())
  } catch (error) {
    console.warn('无法读取桌面发布状态', error)
  }
}

onMounted(() => {
  window.addEventListener('pywebviewready', loadRuntimeStatus)
  loadRuntimeStatus()
})

onBeforeUnmount(() => {
  window.removeEventListener('pywebviewready', loadRuntimeStatus)
})
</script>

<style lang="scss" scoped>
.product-about {
  --harbor: #102b31;
  --harbor-soft: #193a41;
  --signal: #39b8b2;
  --ink: #173038;
  --muted: #6d7d82;
  --rule: #d5dfdc;
  --amber: #bd7d2c;
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

.identity-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 230px;
  min-height: 270px;
  border: 1px solid var(--harbor);
  background: var(--harbor);
  color: #edf8f6;
}
.identity-copy { position: relative; display: flex; min-width: 0; flex-direction: column; justify-content: center; padding: 42px 48px 42px 132px; }
.product-mark {
  position: absolute;
  top: 50%;
  left: 44px;
  width: 58px;
  height: 58px;
  transform: translateY(-50%);
  &::before, i, span { position: absolute; width: 19px; border-radius: 7px; background: var(--signal); content: ''; transform: rotate(36deg); }
  &::before { top: 3px; left: 13px; height: 41px; }
  i { right: 12px; bottom: 2px; height: 31px; }
  span { right: 2px; bottom: 7px; width: 8px; height: 8px; border-radius: 50%; transform: none; }
}
.eyebrow { margin: 0; color: #79d8d1; font: 700 10px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.13em; }
.identity-copy h1 { margin: 12px 0 0; color: #f4fbfa; font: 700 clamp(37px, 5vw, 62px)/0.95 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; letter-spacing: -0.04em; }
.identity-copy > p:last-child { max-width: 720px; margin: 18px 0 0; color: rgba(226, 242, 239, 0.64); font-size: 12px; line-height: 1.8; }
.version-block { display: flex; align-items: flex-end; flex-direction: column; justify-content: center; padding: 36px; border-left: 1px solid rgba(255,255,255,0.14); background: rgba(255,255,255,0.025); text-align: right; }
.version-block span { color: #79d8d1; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.14em; }
.version-block strong { margin-top: 12px; color: #fff; font: 700 44px/0.9 "Bahnschrift SemiCondensed", sans-serif; }
.version-block small { margin-top: 14px; color: rgba(226, 242, 239, 0.5); font: 600 8px/1.4 "Cascadia Mono", monospace; }

.status-ledger { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--rule); border-top: 0; background: #fff; }
.status-ledger article { position: relative; min-height: 92px; padding: 18px 20px; border-right: 1px solid var(--rule); }
.status-ledger article:last-child { border-right: 0; }
.status-ledger article::after { position: absolute; right: 20px; bottom: 0; left: 20px; height: 2px; background: #94aaa7; content: ''; }
.status-ledger article:first-child::after { background: var(--signal); }
.status-ledger article.publish-boundary.is-enabled::after { background: var(--amber); }
.status-ledger article.publish-boundary.is-blocked::after { background: #d4ad68; }
.status-ledger span { color: #55827e; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
.status-ledger strong { display: block; margin-top: 9px; font-size: 13px; }
.status-ledger p { margin: 5px 0 0; color: var(--muted); font-size: 9px; }

.about-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(310px, 0.6fr); gap: 22px; margin-top: 22px; }
.about-panel, .data-panel { border: 1px solid var(--rule); background: #fff; }
.purpose-panel { padding: 28px; }
.section-heading { padding-bottom: 18px; border-bottom: 1px solid var(--rule); }
.section-heading > span { color: #318f8b; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
.section-heading h2 { margin: 9px 0 0; color: var(--harbor); font: 700 24px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
.section-heading > p { margin: 8px 0 0; color: var(--muted); font-size: 9px; }
.section-heading code { padding: 2px 5px; border: 1px solid #cddbd8; background: #f1f7f5; color: #356e6a; font: 600 9px/1 "Cascadia Mono", monospace; }
.lead-copy { margin: 20px 0 5px; color: #3f575d; font-size: 12px; line-height: 1.7; }
.capability-list { margin: 0; padding: 0; list-style: none; }
.capability-list li { display: grid; grid-template-columns: 90px 1fr; gap: 16px; padding: 15px 0; border-bottom: 1px solid #e4ebe9; }
.capability-list li:last-child { border-bottom: 0; }
.capability-list span { color: #367773; font-size: 11px; font-weight: 700; }
.capability-list p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.65; }

.account-panel { display: flex; flex-direction: column; padding: 28px; border-color: var(--harbor); background: var(--harbor); color: #edf8f6; }
.section-heading.inverse { border-color: rgba(255,255,255,0.14); }
.section-heading.inverse > span { color: #79d8d1; }
.section-heading.inverse h2 { color: #f3faf9; }
.account-flow { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: center; margin-top: 26px; }
.account-flow div { display: flex; min-height: 76px; flex-direction: column; justify-content: center; padding: 13px; border: 1px solid rgba(145, 202, 196, 0.25); }
.account-flow span { color: #77cfc8; font: 700 8px/1 "Cascadia Mono", monospace; }
.account-flow strong { margin-top: 8px; color: #edf8f6; font-size: 10px; line-height: 1.55; }
.account-flow > i { color: #69aca7; font-style: normal; }
.account-panel > p { margin: auto 0 0; padding-top: 24px; color: rgba(226, 242, 239, 0.59); font-size: 10px; line-height: 1.75; }

.data-panel { margin-top: 22px; padding: 28px; }
.data-rows article { display: grid; grid-template-columns: 66px minmax(0, 1fr) 180px; gap: 18px; align-items: center; min-height: 78px; border-bottom: 1px solid #e3eae8; }
.data-rows article:last-child { border-bottom: 0; }
.row-code { color: #4f8582; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
.data-rows div strong { font-size: 11px; }
.data-rows div p { margin: 5px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }
.data-rows small { color: #718684; font: 600 8px/1.4 "Cascadia Mono", monospace; text-align: right; }

.release-warning { display: grid; grid-template-columns: 58px 1fr; gap: 20px; align-items: center; margin-top: 22px; padding: 24px 28px; border: 1px solid #ddc49d; background: #fff9ef; }
.warning-mark { display: grid; width: 48px; height: 48px; place-items: center; border: 1px solid var(--amber); color: #976522; font: 700 20px/1 "Cascadia Mono", monospace; }
.release-warning .eyebrow { color: #9a6a29; }
.release-warning h2 { margin: 7px 0 0; color: #5b4a31; font-size: 16px; }
.release-warning div > p:last-child { margin: 8px 0 0; color: #756449; font-size: 10px; line-height: 1.7; }
.about-footer { display: flex; justify-content: space-between; gap: 20px; padding: 18px 4px 5px; color: var(--muted); font: 600 8px/1.4 "Cascadia Mono", monospace; }
.about-footer span { color: #4b827e; letter-spacing: 0.1em; }
.about-footer p { margin: 0; }

@media (max-width: 980px) {
  .about-grid { grid-template-columns: 1fr; }
  .account-panel { min-height: 320px; }
}

@media (max-width: 760px) {
  .identity-panel { grid-template-columns: 1fr; }
  .identity-copy { padding: 38px 24px 38px 84px; }
  .product-mark { left: 20px; transform: translateY(-50%) scale(0.72); }
  .version-block { align-items: flex-start; padding: 22px 24px; border-top: 1px solid rgba(255,255,255,0.14); border-left: 0; text-align: left; }
  .status-ledger { grid-template-columns: repeat(2, 1fr); }
  .status-ledger article:nth-child(2) { border-right: 0; }
  .status-ledger article:nth-child(-n + 2) { border-bottom: 1px solid var(--rule); }
  .purpose-panel, .account-panel, .data-panel { padding: 22px 18px; }
  .account-flow { grid-template-columns: 1fr; }
  .account-flow > i { transform: rotate(90deg); text-align: center; }
  .data-rows article { grid-template-columns: 52px 1fr; padding: 14px 0; }
  .data-rows small { grid-column: 2; text-align: left; }
  .release-warning { grid-template-columns: 1fr; padding: 22px; }
  .about-footer { align-items: flex-start; flex-direction: column; }
}

@media (max-width: 460px) {
  .identity-copy { padding: 34px 20px; }
  .product-mark { position: relative; top: auto; left: auto; margin-bottom: 22px; transform: scale(0.8); transform-origin: left center; }
  .status-ledger { grid-template-columns: 1fr; }
  .status-ledger article, .status-ledger article:nth-child(2) { border-right: 0; border-bottom: 1px solid var(--rule); }
  .status-ledger article:last-child { border-bottom: 0; }
  .capability-list li { grid-template-columns: 1fr; gap: 6px; }
}
</style>
