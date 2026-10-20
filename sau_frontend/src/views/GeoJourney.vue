<template>
  <main class="geo-page journey" data-testid="geo-journey">
    <header class="journey-header">
      <div><p class="eyebrow">一步一步，把搜索发现用起来</p><h1 class="geo-page-heading">{{ project ? project.name : '从你的品牌开始' }}</h1><p class="geo-page-description">看 AI 如何回答，找到内容机会，再发布与复测。</p></div>
      <el-button v-if="project" :disabled="busy" @click="step = 0">切换 / 修改品牌</el-button>
    </header>
    <nav class="journey-steps" aria-label="优化流程">
      <button v-for="(label,index) in steps" :key="label" type="button" :disabled="busy || !canVisit(index)" :aria-current="step === index ? 'step' : undefined" :class="{active:step===index,done:step!==index&&stepComplete(index)}" @click="step=index"><span>{{ index+1 }}</span>{{ label }}<el-icon v-if="step!==index&&stepComplete(index)"><Check /></el-icon></button>
    </nav>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon class="journey-alert"/>
    <el-alert v-if="saveError" type="warning" :title="saveError" :closable="false" show-icon/>
    <div v-if="recoveryDraft || recoveryFacts" class="recovery-note"><p>其他页面更新过资料，已保留服务端版本。冲突的本机编辑可先导出核对。</p><el-button v-if="recoveryDraft" text @click="exportRecovery('draft')">导出本机未保存稿</el-button><el-button v-if="recoveryFacts" text @click="exportRecovery('facts')">导出本机未保存资料</el-button></div>
    <div v-if="nativeBusy" class="task-progress" role="status" aria-live="polite"><el-icon class="is-loading"><Loading /></el-icon><div><strong>{{ task.message || '任务执行中' }}</strong><p>遇到登录或人机验证，请在专用窗口正常完成。采集时保持窗口打开；专用登录完成后关闭登录窗口以保存会话。</p></div><el-button @click="stopTask">停止</el-button></div>
    <Transition name="geo-reveal" mode="out-in" @before-enter="resetStageScroll">
      <section v-if="step===0" key="brand" class="geo-surface step-panel">
        <div class="section-heading"><span class="step-kicker">01 / 品牌</span><h2>先让软件认识你的业务</h2><p>三个基本信息就能开始，后续资料按需要补充。</p></div>
        <el-form label-position="top" @submit.prevent="primaryAction">
          <el-form-item v-if="projects.length" label="继续已有品牌"><el-select v-model="projectChoice" placeholder="选择已建对象，或填写新对象" clearable :disabled="busy" @change="chooseProject"><el-option v-for="item in projects" :key="item.id" :value="item.id" :label="`${entityLabel(item)} · ${item.name} · ${item.website || '未填网址'}`"/></el-select></el-form-item>
          <el-form-item label="测试对象"><el-radio-group v-model="form.entity_type" :disabled="busy" aria-label="测试对象"><el-radio-button value="company">公司</el-radio-button><el-radio-button value="product">产品</el-radio-button></el-radio-group><p class="hint entity-hint">公司与旗下产品分别建档、出题和统计，互不合并提及率。</p></el-form-item>
          <div class="form-pair"><el-form-item :label="form.entity_type==='company' ? '公司名称' : '产品名称'" required><el-input v-model="form.name" maxlength="100" placeholder="输入本次测试对象的名称" :disabled="busy" data-testid="journey-brand"/></el-form-item><el-form-item :label="form.entity_type==='company' ? '公司官网' : '产品介绍或服务网址'" required><el-input v-model="form.website" maxlength="300" :placeholder="form.entity_type==='company' ? 'https://公司官网' : 'https://官网/products/产品路径'" :disabled="busy" data-testid="journey-website"/></el-form-item></div>
          <el-form-item label="用一句话介绍业务" required><el-input v-model="form.description" type="textarea" :rows="3" maxlength="4000" show-word-limit placeholder="提供什么产品，帮助哪些客户解决什么问题？" :disabled="busy" data-testid="journey-description"/></el-form-item>
          <el-collapse class="advanced"><el-collapse-item title="补充公司、客户、别名与官方页面" name="brand-options"><div class="form-pair"><el-form-item :label="form.entity_type==='company' ? '关联公司（可选）' : '运营公司（可选）'"><el-input v-model="form.company" maxlength="300" :disabled="busy"/></el-form-item><el-form-item label="目标客户（可选）"><el-input v-model="form.audience" maxlength="300" :disabled="busy"/></el-form-item></div><div class="form-pair"><el-form-item label="当前对象别名（每行一个，可选）"><el-input v-model="form.aliasesText" type="textarea" :rows="3" :disabled="busy" placeholder="仅填写这个公司或产品自己的其他名称"/><p class="hint">产品别名不应填写运营公司或其他产品。</p></el-form-item><el-form-item label="额外官方页面 / 服务网址（每行一个，可选）"><el-input v-model="form.officialUrlsText" type="textarea" :rows="3" :disabled="busy" placeholder="填写这个对象的具体官方页面"/><p class="hint">产品请填对应介绍、文档或服务入口；不自动扩展到整站。</p></el-form-item></div><ModelOptions/></el-collapse-item></el-collapse>
        </el-form>
      </section>
      <section v-else-if="step===1" key="baseline" class="geo-surface step-panel">
        <div class="section-heading"><span class="step-kicker">02 / 搜索基线</span><h2>先看 AI 现在如何回答</h2><p>分别观察自然选型、品牌核验与官网核验。</p></div>
        <template v-if="!report">
          <ProviderChoice/>
          <div v-if="plan" class="plan-preview"><p class="small-label">请核对这 {{ plan.cases.length }} 个问题，再开始真实采集</p><p v-if="reportEntityContext(plan).legacy" class="hint">{{ reportEntityContext(plan).note }}</p><article v-for="item in plan.cases" :key="item.id"><el-tag size="small" effect="plain">{{ categoryNames[item.category] }}</el-tag><p>{{ item.question }}</p></article><el-checkbox v-model="planConfirmed" :disabled="busy">这些问题符合我的业务，可以开始测试</el-checkbox></div>
          <p v-else class="hint">生成问题不会自动开始采集。你会先看到问题预览。</p>
          <div class="utility-row"><el-button v-if="nativeAvailable" text :disabled="busy" @click="login">登录豆包</el-button><el-button v-if="plan" text :disabled="busy" @click="makePlan">重新生成问题</el-button><el-button v-if="!nativeAvailable" text @click="router.push('/geo-tests')">查看本机采集方式</el-button></div>
        </template>
        <template v-else>
          <div class="report-heading"><el-tag :type="reportOrigin==='fresh' ? 'success' : 'info'">{{ reportOrigin==='fresh' ? '本次采集' : '历史采集' }}</el-tag><span>{{ journeyDate(report.updated_at) }}</span><el-button v-if="reusablePlan" text :disabled="busy" @click="repeatBaseline">沿用问题新测一轮</el-button><el-button text :disabled="busy" @click="newBaseline">重新出题</el-button></div>
          <div class="metric-grid"><article v-for="key in categoryKeys" :key="key"><span>{{ categoryNames[key] }}</span><strong>{{ rate(report.cohorts?.[key]?.brand_mention_rate) }}</strong><small>{{ reportEntityContext(report).mentionLabel }} · 有效 {{ report.cohorts?.[key]?.answers_evaluable || 0 }} 题</small></article></div>
          <BaselineContext/>
          <details v-if="report.evaluation_projection" class="hint"><summary>评估说明 · {{ report.evaluation_projection.stored_evaluation_version }} → {{ report.evaluation_projection.applied_evaluation_version }}</summary><p>{{ report.evaluation_projection.note }}</p></details>
          <p class="hint">提及不等于推荐。网址未知会保留未知，不按“没有引用”计算。</p><p v-if="!usableReport" class="hint">这份报告没有可分析的有效回答，请新建一轮基线；失败记录仍会保留。</p><p v-if="report.stop_reason" class="hint">采集中断：{{ report.stop_reason }}。有有效回答时可继续分析，或新建一轮。</p>
        </template>
        <el-collapse class="advanced"><el-collapse-item title="继续同一对象的历史采集" name="history"><p class="hint">只显示名称、测试对象类型和完整主网址匹配的报告；同域不同产品路径分别保留，不会当作新采集。未标对象类型的旧报告按产品档案兼容，旧统计口径另行提示。</p><el-select v-model="historyReportId" placeholder="选择带时间的历史报告" :disabled="busy" @change="useHistory"><el-option v-for="item in reports" :key="item.id" :value="item.id" :label="`${journeyDate(item.updated_at)} · ${item.case_count} 题 · ${item.status}`"/></el-select><p v-if="!reports.length" class="hint">这个对象尚无匹配的历史报告。</p></el-collapse-item><el-collapse-item title="采集模型与界面" name="model"><ModelOptions/></el-collapse-item></el-collapse>
      </section>
      <section v-else-if="step===2 && cycle" key="content" class="geo-surface step-panel">
        <div class="section-heading"><span class="step-kicker">03 / 内容</span><h2>{{ activeDraft ? '核对这篇文章，再标记就绪' : '把来源发现变成一篇平台稿' }}</h2><p>来源决定调研方向，已经核对的事实才用于写作。</p></div>
        <BaselineContext/>
        <div class="diagnosis"><p>{{ diagnosisPreview }}</p><details v-if="cycle.analysis?.diagnosis?.length>180" class="hint"><summary>查看完整分析</summary><p>{{ cycle.analysis.diagnosis }}</p></details><span v-if="needsAnalysis" class="hint">当前是规则整理的观察记录，下一步用 AI 制定选题。</span></div>
        <el-collapse class="source-summary"><el-collapse-item :title="`来源线索：${sourceCounts.verified} 个网址平台候选 · ${sourceCounts.unknown} 组网址未知`" name="opportunities"><article v-for="item in cycle.opportunities || []" :key="item.id" class="source-row"><strong>{{ item.candidate?.name || item.domain || '网址未知' }}</strong><el-tag size="small" effect="plain">{{ surfaceLabels[item.surface] || '待核对' }}</el-tag><p>{{ item.note }}</p><p v-for="lead in item.title_leads || []" :key="lead.candidate.name" class="hint">标题线索：{{ lead.candidate.name }}，尚未核实网址。</p></article><el-button text @click="openDetails">接入或配置目标平台</el-button></el-collapse-item></el-collapse>
        <template v-if="!needsAnalysis">
          <div class="form-pair"><el-form-item label="这次写什么"><el-select :model-value="briefId" :disabled="busy" aria-label="内容选题" @change="selectContent('brief', $event)"><el-option v-for="brief in cycle.analysis.briefs" :key="brief.id" :value="brief.id" :label="brief.topic"/></el-select></el-form-item><el-form-item label="写给哪个平台"><el-select :model-value="platform" :disabled="busy" placeholder="选择渠道或已接入平台" aria-label="目标平台" @change="selectContent('platform', $event)"><el-option v-for="(label,key) in cycle.channels" :key="key" :value="key" :label="channelOptionLabel(key,label)"/></el-select></el-form-item></div>
          <p class="hint platform-basis">{{ platformBasis }}</p>
          <template v-if="!activeDraft">
            <div class="subheading"><h3>核对写作事实</h3><el-button text :disabled="busy" @click="factRows.push({text:'',source_url:'',confirmed:false})">添加一条</el-button></div><p class="hint">{{ cycle.fact_candidates && !cycle.facts.length ? '下面包含同品牌上轮资料，请重新核对后勾选。' : '运营方说明没有独立核验；填写来源网址也不代表软件已抓取验证。' }}</p>
            <div v-for="(fact,index) in factRows" :key="index" class="fact-row"><el-checkbox v-model="fact.confirmed" :disabled="busy" :aria-label="`已核对第 ${index+1} 条事实`"/><div><el-input v-model="fact.text" :disabled="busy" type="textarea" :autosize="{minRows:1,maxRows:4}" :aria-label="`第 ${index+1} 条事实`" placeholder="可用于写作的真实信息"/><el-input v-model="fact.source_url" :disabled="busy" :aria-label="`第 ${index+1} 条事实来源网址`" placeholder="来源网址可留空，留空按运营方说明处理"/></div><el-button text :disabled="busy" :aria-label="`移除第 ${index+1} 条事实`" @click="factRows.splice(index,1)"><el-icon><Close/></el-icon></el-button></div>
            <ProviderChoice/>
          </template>
          <template v-else>
            <el-alert v-if="needsReplacement" type="warning" :closable="false" title="目标配置已更新，旧稿仍保留。请按当前目标另存新稿，再重新审稿；旧稿不会重新提交。"/>
            <ProviderChoice v-if="needsReplacement"/>
            <div class="draft-heading"><el-tag :type="!needsReplacement && !lengthInvalid && (reviewedDraft || publishedDraft) ? 'success' : 'warning'">{{ needsReplacement ? '旧目标版本，稿件保留' : lengthInvalid ? '超过当前长度限制，请精简' : publishedDraft ? '已进入发布流程' : reviewedDraft ? '已审稿就绪' : '草稿，待核对审稿' }}</el-tag><el-select v-if="matchingDrafts.length>1" :model-value="articleId" :disabled="busy" aria-label="选择本轮稿件" @change="selectContent('article', $event)"><el-option v-for="draft in matchingDrafts" :key="draft.article_id" :value="draft.article_id" :label="draft.title"/></el-select></div>
            <el-form label-position="top"><el-form-item label="文章标题" :error="lengthReview.titleIssue"><el-input v-model="editor.title" :disabled="busy || publishedDraft || needsReplacement" data-testid="journey-article-title"/><p class="hint">标题 {{ lengthReview.titleCount }} / {{ draftLimits.title }} 字{{ publishChannel==='toutiao' ? ' · 头条文字发布器上限 30 字' : '' }}</p></el-form-item><el-form-item label="正文" :error="lengthReview.bodyIssue"><el-input v-model="editor.content" :disabled="busy || publishedDraft || needsReplacement" type="textarea" :rows="14" data-testid="journey-article-content"/><p class="hint">正文 {{ lengthReview.bodyCount }}{{ draftLimits.body ? ` / ${draftLimits.body}` : '' }} 字</p></el-form-item></el-form><el-checkbox v-if="!publishedDraft && !needsReplacement" v-model="reviewConfirmed" :disabled="busy">我已核对事实、表述和平台要求，允许进入发布流程</el-checkbox><p class="hint">{{ publishedDraft ? '文章已进入发布流程，内容在此只读；请到反馈查看平台处理结果。' : needsReplacement ? '请用下方按钮另存当前目标版本的草稿。旧稿和发布记录会保留。' : '修改后需再次保存审稿，不会把 AI 输出直接标为就绪。' }}</p>
          </template>
        </template>
        <ProviderChoice v-else/>
      </section>
      <section v-else-if="step===3 && activeDraft" key="publish" class="geo-surface step-panel">
        <div class="section-heading"><span class="step-kicker">04 / 发布</span><h2>把这一篇交给对应平台</h2><p>平台要求登录、实名或人机验证时，在专用窗口正常完成。</p></div>
        <div class="publish-preview"><el-tag :type="reviewedDraft ? 'success' : 'warning'">{{ reviewedDraft ? '已审稿就绪' : '稿件有变更，请返回审稿' }}</el-tag><h3>{{ activeDraft.title }}</h3><p>{{ cycle.channels[activeDraft.platform] }} · {{ activeDraft.content.length }} 字</p></div>
        <BaselineContext/>
        <template v-if="needsReplacement"><el-alert type="warning" :closable="false" title="目标配置已更新，旧稿仍保留。请按当前目标另存新稿，再重新审稿；旧稿不会重新提交。"/><ProviderChoice/></template>
        <template v-if="lengthInvalid && !needsReplacement"><el-alert type="warning" :closable="false" :title="[lengthReview.titleIssue,lengthReview.bodyIssue].filter(Boolean).join(' ')"/><el-button text :disabled="busy" @click="step=2">返回内容精简并重新审稿</el-button></template>
        <template v-if="publisherPaused"><el-alert type="warning" :closable="false" :title="`${publisherName}发布器当前暂停，请先保留稿件，等待能力恢复。`"/><el-button text :disabled="busy" @click="refreshAccounts">刷新发布能力</el-button></template>
        <template v-else-if="selectedTarget && !targetPublisher"><p v-if="!capabilitiesLoaded" class="hint">发布能力尚未读取，请刷新后选择发布方式。</p><template v-else><p class="hint">{{ selectedTarget.builtin_publisher?.reason || (platformCapability?.delivery_state==='assisted' ? '该渠道仍需人工复核，当前使用目标平台辅助填写。' : '尚未核验此目标与内置图文发布器的对应关系。') }} 该平台使用独立浏览器会话，填写后请核对并自行提交。</p><p v-if="!selectedTarget.config.title_selector" class="hint">尚未配置标题与正文编辑框，请先打开详细配置。</p></template><el-button text :disabled="busy" @click="refreshAccounts">刷新发布能力</el-button></template>
        <template v-else-if="ownedDraft"><p class="hint">官网稿件可导出给网站编辑，或通过网站 CMS 发布。这里没有可执行的官网发布器，导出也不代表已经发布。</p><el-button text :disabled="busy" @click="openDetails">接入官网 CMS 或配置专用编辑入口</el-button></template>
        <template v-else><p v-if="targetPublisher" class="hint publisher-bridge">使用{{ publisherName }}发布器，保留这篇目标稿和来源记录。进入发布中心后核对账号与素材，再逐篇确认真实发布。</p><div class="subheading"><h3>平台账号</h3><el-button text :disabled="busy" @click="refreshAccounts">刷新账号</el-button></div><article v-for="account in matchingAccounts" :key="account.id" class="account-row"><div><strong>{{ account.account_name || `账号 #${account.id}` }}</strong><p>{{ account.status==='connected' ? '已连接（仍需发布前检查）' : '登录状态待处理' }} · {{ account.check_message || '尚无最新检测说明' }}</p></div><el-button :disabled="busy" @click="checkAccount(account.id)">检测登录</el-button></article><p v-if="!matchingAccounts.length" class="hint">尚未连接{{ publisherName }}账号。发布中心会保留这篇稿件，先连接账号再继续。</p><p v-if="platformCapability?.requires_images" class="hint">该平台需要图片。请在发布中心选择并核对配图，再执行真实发布。</p><el-button text @click="router.push('/account-management')">管理账号与登录</el-button></template>
        <el-alert type="info" :closable="false" :title="ownedDraft ? '请由网站编辑发布后，接入官网目标并登记公开文章链接；未登记前只显示采集观察。' : '在平台完成单篇真实发布后，返回“开始优化”的反馈步骤刷新记录。'"/>
        <el-button text :disabled="busy" @click="goFeedback">已经提交，查看反馈与复测</el-button>
        <el-collapse v-if="selectedTarget" class="advanced"><el-collapse-item title="登记已在该平台提交的文章链接" name="publication"><p class="hint">记录为运营方自报，软件尚未独立核验。请填写公开文章网址和实际提交时间。</p><el-form label-position="top"><el-form-item label="公开文章链接"><el-input v-model="publication.url" placeholder="https://平台/具体文章"/></el-form-item><el-form-item label="实际提交时间"><el-date-picker v-model="publication.publishedAt" type="datetime" placeholder="选择时间"/></el-form-item></el-form><el-checkbox v-model="publication.confirmed">我确认这是这篇文章在对应平台提交后的链接</el-checkbox><el-button :disabled="busy || !publication.confirmed || !publication.url || !publication.publishedAt" @click="recordPublication">保存提交记录</el-button></el-collapse-item></el-collapse>
      </section>
      <section v-else-if="step===4 && cycle" key="feedback" class="geo-surface step-panel">
        <div class="section-heading"><span class="step-kicker">05 / 反馈</span><h2>用同一组问题观察变化</h2><p>发布成功、被索引、被 AI 引用，是不同的结果。</p></div>
        <BaselineContext/>
        <div class="metric-grid feedback-metrics"><article><span>发布器成功回报</span><strong>{{ cycle.publications?.length || 0 }}</strong><small>排除演示任务与无链接任务</small></article><article><span>运营方自报提交</span><strong>{{ cycle.reported_publications?.length || 0 }}</strong><small>尚未独立核验发布成功</small></article><article><span>同题复测</span><strong>{{ cycle.retests?.length || 0 }}</strong><small>按计划、界面、模型和时间检查</small></article></div>
        <p v-if="!hasPublication" class="hint">{{ unfinishedPublications.length ? '尚无公开成功记录，请先查看以下原发布任务。待确认不等于失败，现在复测只记录新的采集观察。' : '当前没有可用于发布后分析的提交记录。现在复测只会记录新的采集观察。' }}</p>
        <div v-if="unfinishedPublications.length" class="publication-tasks" aria-label="原发布任务"><article v-for="job in unfinishedPublications" :key="job.id" class="feedback-card"><el-tag :type="job.status==='failed' ? 'danger' : job.status==='need_action' ? 'warning' : 'info'">{{ publicationStatus[job.status] }}</el-tag><h3>{{ job.title }} · {{ cycle.channels[job.platform] || job.platform }}</h3><p>{{ job.message || publicationMessages[job.status] }}</p><p v-if="job.status==='processing'" class="hint">最终提交结果仍在确认，请核对原任务与平台后台，不要另建重复发布。</p><el-button :disabled="busy" @click="continuePublication(job)">继续查看原发布任务</el-button></article></div>
        <div v-for="url in cycle.publications || []" :key="url" class="publication-link"><el-tag size="small" type="success">发布器回报</el-tag><a v-if="safeLink(url)" :href="safeLink(url)" target="_blank" rel="noopener noreferrer">{{ url }}</a></div><div v-for="item in cycle.reported_publications || []" :key="item.id" class="publication-link"><el-tag size="small" type="info">运营方自报</el-tag><a v-if="safeLink(item.url)" :href="safeLink(item.url)" target="_blank" rel="noopener noreferrer">{{ item.url }}</a></div>
        <article v-for="item in cycle.retests || []" :key="item.run_id" class="feedback-card"><h3>{{ feedbackLabels[item.comparison.feedback_stage] || '采集观察' }} · {{ journeyDate(item.updated_at) }}</h3><p>{{ item.comparison.publication_context }}</p><p v-if="!item.comparison.conditions_match" class="hint">条件差异：{{ item.comparison.reasons?.join('；') }}</p><div v-for="cohort in item.comparison.cohorts || []" :key="cohort.category" class="comparison-row"><span>{{ categoryNames[cohort.category] }}</span><strong>{{ rate(cohort.before_brand_rate) }} → {{ rate(cohort.after_brand_rate) }}</strong><small>共同有效 {{ cohort.paired_answers }} 题</small></div><p class="hint">{{ item.comparison.note }}</p><p v-for="link in item.comparison.publication_citations || []" :key="link.url" class="hint">{{ link.observed_in_citations===true ? '本轮正文引用中观察到' : '暂未观察到引用，索引状态未知' }}：{{ link.url }}</p></article>
        <p class="hint">复测沿用原问题、界面与模型选项；未知不算零。单轮小样本与索引时滞都影响结果，不能直接判断有效、无效或因果。</p><div class="utility-row"><el-button text :disabled="busy" @click="refreshCycle">刷新发布与反馈</el-button><el-button text :disabled="busy" @click="openDetails">查看完整采集与复测明细</el-button></div>
      </section>
    </Transition>
    <footer class="journey-actions"><el-button v-if="step>0" :disabled="busy" @click="step--">上一步</el-button><p aria-live="polite">{{ actionHint }}</p><el-button type="primary" size="large" :disabled="primaryDisabled" :loading="working" data-testid="journey-primary" @click="primaryAction">{{ primaryLabel }}</el-button></footer>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElRadioGroup, ElRadioButton, ElSelect, ElOption, ElInput } from 'element-plus'
import { Check, Close, Loading } from '@element-plus/icons-vue'
import { projectApi } from '@/api/project'
import { articleApi } from '@/api/article'
import { geoTestApi } from '@/api/geoTest'
import { geoContentApi } from '@/api/geoContent'
import { accountApi } from '@/api/account'
import { publishApi } from '@/api/publish'
import { articleSnapshot, baselineEvidenceSummary, canRestoreArticleEdits, canRestoreFactEdits, entityLabel, entityProfileFields, entityType, journeyDate, journeyDraftLimits, journeyLengthReview, lastJourneyProject, normalizeWebsite, profileLines, readJourney, reportEntityContext, restoreEntityDraftFields, safeLink, sameBrand, targetDraftOutdated, writeJourney } from '@/utils/geoJourney'

const route=useRoute(), router=useRouter(), steps=['品牌','搜索基线','内容','发布','反馈']
const categoryKeys=['discovery','brand_identity','website_guided'], categoryNames={discovery:'自然选型',brand_identity:'品牌核验',website_guided:'官网核验'}
const surfaceLabels={community_candidate:'网址匹配的候选',vendor_documentation:'厂商文档',owned_site:'客户官网',url_unknown:'网址未知',unknown:'需调研'}
const feedbackLabels={collection_only:'采集观察，尚非发布后比较',conditions_mismatch:'采集条件不匹配',retest_precedes_publication:'复测早于提交时间',publication_time_unknown:'提交时间不足，不能比较',post_publication_observation:'发布后观察，尚无因果结论'}
const publicationStatus={queued:'已排队，待确认执行',scheduled:'计划任务，等待到期',processing:'处理中，结果待确认',need_action:'待人工核对',failed:'发布失败'}
const publicationMessages={queued:'任务已保存，尚未确认真实执行。',scheduled:'任务尚未到达计划时间，请查看原任务。',processing:'平台最终提交结果仍在确认中。',need_action:'请先核对原任务说明与平台状态，再决定下一步。',failed:'请先查看失败原因，再决定是否重新处理。'}
const projects=ref([]), projectId=ref(null), projectChoice=ref(null), step=ref(0), provider=ref('doubao'), nativeAvailable=ref(false)
const emptyBrand=()=>({name:'',website:'',description:'',entity_type:'product',company:'',audience:'',aliasesText:'',officialUrlsText:''})
const form=reactive(emptyBrand()), clientMode=ref('consumer'), model=ref('')
const plan=ref(null), planConfirmed=ref(false), report=ref(null), reportId=ref(''), reportOrigin=ref('history'), reports=ref([]), historyReportId=ref('')
const cycle=ref(null), cycleId=ref(''), briefId=ref(''), platform=ref(''), articleId=ref(null), factRows=ref([]), reviewConfirmed=ref(false)
const replacementSourceId=ref(null)
const editor=reactive({title:'',content:'',summary:'',tags:[]}), publication=reactive({url:'',publishedAt:null,confirmed:false})
const recoveryDraft=ref(null), recoveryFacts=ref(null)
const accounts=ref([]), capabilities=ref([]), capabilitiesLoaded=ref(false), task=ref({status:'idle'}), taskId=ref(''), taskKind=ref(''), working=ref(false), finishingTask=ref(false), error=ref(''), saveError=ref('')
let timer, bridgeTimer, pollPending=false, handledTask='', restoring=false, destroyed=false, liveTaskId='', initialized=false
const nativeMethodsReady=()=>['get_task','plan_with_doubao','start_run','open_login','generate_research','start_research_retest','open_destination','stop_task'].every(name=>typeof window.pywebview?.api?.[name]==='function')
const activateNative=()=>{if(destroyed||!nativeMethodsReady())return false;nativeAvailable.value=true;if(initialized&&!timer){void poll();timer=setInterval(poll,1000)}clearInterval(bridgeTimer);return true}
const project=computed(()=>projects.value.find(item=>item.id===projectId.value)), nativeBusy=computed(()=>task.value.status==='running'), busy=computed(()=>working.value||nativeBusy.value||finishingTask.value)
const formProfile=()=>entityProfileFields({...form,aliases:form.aliasesText,official_urls:form.officialUrlsText})
const profileDirty=computed(()=>{if(!project.value)return true;try{return !sameBrand(form,project.value)||form.description.trim()!==String(project.value.description||'').trim()||JSON.stringify(formProfile())!==JSON.stringify(entityProfileFields(project.value))}catch{return true}})
const needsAnalysis=computed(()=>cycle.value?.analysis?.method==='rule_observations_not_ai')
const diagnosisPreview=computed(()=>{const text=cycle.value?.analysis?.diagnosis||'';return text.length>180?`${text.slice(0,180).trim()}…`:text})
const matchingDrafts=computed(()=>(cycle.value?.drafts||[]).filter(item=>item.platform===platform.value&&item.brief_id===briefId.value))
const channelOptionLabel=(key,label)=>cycle.value?.destinations?.some(item=>item.key===key)?`${label} · 已接入目标`:`${label} · 通用渠道`
const activeDraft=computed(()=>matchingDrafts.value.find(item=>item.article_id===articleId.value)||null)
const selectedTarget=computed(()=>(cycle.value?.destinations||[]).find(item=>item.key===(activeDraft.value?.platform||platform.value)))
const needsReplacement=computed(()=>targetDraftOutdated(activeDraft.value,selectedTarget.value))
const ownedDraft=computed(()=>activeDraft.value?.platform==='owned_web'&&!selectedTarget.value)
const publishChannel=computed(()=>selectedTarget.value?.config.channel||activeDraft.value?.platform||'')
const matchingAccounts=computed(()=>accounts.value.filter(item=>item.platform_key===publishChannel.value))
const platformCapability=computed(()=>capabilities.value.find(item=>item.key===publishChannel.value))
const publisherName=computed(()=>platformCapability.value?.name||cycle.value?.channels[publishChannel.value]||publishChannel.value)
const publisherPaused=computed(()=>platformCapability.value?.delivery_state==='paused')
const targetPublisher=computed(()=>Boolean(selectedTarget.value?.builtin_publisher?.available===true&&selectedTarget.value.builtin_publisher.platform===publishChannel.value&&platformCapability.value?.delivery_state==='verified'&&['article','image_article'].includes(platformCapability.value.content_mode)&&!platformCapability.value.requires_video))
const draftLimits=computed(()=>journeyDraftLimits(selectedTarget.value,publishChannel.value))
const lengthReview=computed(()=>journeyLengthReview(editor,draftLimits.value))
const lengthInvalid=computed(()=>Boolean(lengthReview.value.titleIssue||lengthReview.value.bodyIssue))
const evidenceReport=computed(()=>cycle.value?.baseline||report.value)
const evidenceSummary=computed(()=>baselineEvidenceSummary(evidenceReport.value))
const unfinishedPublications=computed(()=>(cycle.value?.drafts||[]).flatMap(draft=>(draft.jobs||[]).filter(job=>!job.demo&&publicationStatus[job.status]).map(job=>({...job,article_id:draft.article_id,title:draft.title}))))
const hasPublication=computed(()=>Boolean(cycle.value?.publications?.length||cycle.value?.reported_publications?.length))
const usableReport=computed(()=>Boolean(report.value?.cases?.some(item=>item.assessment?.answer_evaluable===true)))
const sourceCounts=computed(()=>({verified:(cycle.value?.opportunities||[]).filter(item=>item.surface==='community_candidate').length,unknown:(cycle.value?.opportunities||[]).filter(item=>item.surface==='url_unknown').length}))
const confirmedFacts=()=>factRows.value.filter(item=>item.confirmed&&item.text.trim()).map(item=>({text:item.text.trim(),source_url:item.source_url.trim(),confirmed:true}))
const factsSaved=computed(()=>Boolean(confirmedFacts().length&&JSON.stringify(confirmedFacts().map(({text,source_url})=>({text,source_url})))===JSON.stringify((cycle.value?.facts||[]).map(({text,source_url})=>({text,source_url:source_url||''})))))
const editorDirty=computed(()=>activeDraft.value&&(editor.title!==activeDraft.value.title||editor.content!==activeDraft.value.content))
const publishedDraft=computed(()=>['published','publishing'].includes(activeDraft.value?.status))
const reviewedDraft=computed(()=>activeDraft.value?.status==='ready'&&!editorDirty.value)
const platformBasis=computed(()=>{
  if(!platform.value)return '尚未选择平台。平台候选只表示值得调研，不表示可以自动投稿。'
  const target=selectedTarget.value, matched=(cycle.value?.opportunities||[]).some(item=>item.matches?.some(value=>value.key===platform.value))
  if(target)return matched?`已接入 ${target.config.name}，来源网址与配置范围匹配；投稿资格仍需确认。`:`手动选择 ${target.config.name}；本轮尚无匹配的来源网址。`
  return '手动选择通用渠道稿；这不表示豆包已引用或推荐该平台。'
})
const canVisit=index=>index===0||Boolean(project.value&&!profileDirty.value&&(index===1||cycle.value&&(index===2||reviewedDraft.value&&index===3||index===4)))
const stepComplete=index=>index===0?Boolean(project.value):index===1?usableReport.value:index===2?reviewedDraft.value||publishedDraft.value:index===3?hasPublication.value:Boolean(cycle.value?.retests?.length)
const primaryLabel=computed(()=>[2,3].includes(step.value)&&needsReplacement.value?'按当前目标另存新稿':step.value===0?'保存品牌并继续':step.value===1?(report.value?'分析这份报告':plan.value?'开始真实搜索测试':'生成 3 个测试问题'):step.value===2?(needsAnalysis.value?'分析来源并制定选题':!activeDraft.value?(!factsSaved.value?'保存已核对的事实':'生成平台草稿'):publishedDraft.value?'查看发布反馈':!reviewedDraft.value?'保存审稿并标记就绪':'继续发布'):step.value===3?(publisherPaused.value?'发布器当前暂停':targetPublisher.value?`使用${publisherName.value}发布器`:selectedTarget.value?(selectedTarget.value.config.title_selector?'打开目标平台并填写':'配置平台编辑入口'):ownedDraft.value?'导出官网稿件':'打开发布中心'):'开始同题复测')
const primaryDisabled=computed(()=>{
  if(busy.value)return true
  if([2,3].includes(step.value)&&needsReplacement.value)return !factsSaved.value||provider.value==='doubao'&&!nativeAvailable.value
  return step.value===0&&(!form.name.trim()||!form.website.trim()||!form.description.trim())||step.value===1&&(report.value?!usableReport.value:Boolean(plan.value)&&(!planConfirmed.value||!nativeAvailable.value))||step.value===2&&!needsAnalysis.value&&(!activeDraft.value?(!factsSaved.value?!confirmedFacts().length:!platform.value||!briefId.value):!publishedDraft.value&&(lengthInvalid.value||!reviewedDraft.value&&(!reviewConfirmed.value||!editor.title.trim()||!editor.content.trim())))||step.value===3&&(!reviewedDraft.value||lengthInvalid.value||publisherPaused.value||Boolean(selectedTarget.value)&&(!capabilitiesLoaded.value||!targetPublisher.value&&Boolean(selectedTarget.value.config.title_selector)&&!nativeAvailable.value))||step.value===4&&!nativeAvailable.value
})
const actionHint=computed(()=>busy.value?'任务进行中，当前品牌和步骤已锁定':step.value===0?'品牌资料用于出题，不视为独立核验事实':step.value===1?report.value?'分析将使用上方标注时间的采集报告':plan.value?'检查问题并勾选确认后，才会访问豆包采集':'先生成问题，再确认采集':step.value===2?'草稿需要审稿，不会自动发布':step.value===3?'一次处理一篇，最终提交以平台结果为准':'复测记录变化，不承诺被索引或被引用')
const rate=value=>typeof value==='number'&&Number.isFinite(value)?`${Math.round(value*100)}%`:'未知'
const options=()=>({client_mode:clientMode.value,model_label:model.value.trim()})
const resetStageScroll=()=>{const element=document.querySelector('.workspace-main');if(element)element.scrollTop=0}
const BaselineContext=defineComponent({setup:()=>()=>{
  const baseline=evidenceReport.value, summary=evidenceSummary.value;if(!baseline)return null
  const origin=reportOrigin.value==='fresh'&&report.value?.run_id===baseline.run_id?'本次采集报告':'历史采集报告'
  const counts=summary.known?`有效 ${summary.effective} / ${summary.total} 题；失败 ${summary.failed} 题未计入${summary.otherExcluded?`；另有 ${summary.otherExcluded} 题未完成或不可评估`:''}`:'题数待核对'
  const entity=reportEntityContext(baseline)
  return h('aside',{class:'baseline-context',role:'note','aria-label':'采集依据与来源复查时间','data-testid':'journey-baseline-context'},[
    h('p',`${origin} · ${journeyDate(baseline.updated_at)} · ${counts}。接入发布器不会产生新的搜索引用。`),
    h('p',`测试对象：${entity.label} · ${baseline.profile?.name||'名称未知'}${entity.legacy?'（旧报告，混合统计口径）':''}`),
    h('details',[h('summary','测试网址与统计口径'),h('p',entity.note),...entity.urls.map(url=>h('p',{key:url},safeLink(url)?h('a',{href:safeLink(url),target:'_blank',rel:'noopener noreferrer'},url):url))]),
    ...summary.inspections.map(item=>h('p',{key:item.caseId},`来源只读复查 ${item.caseId} · ${journeyDate(item.inspectedAt)}${item.originalAt?`（原回答 ${journeyDate(item.originalAt)}）`:''}；${item.partial?'来源仍有缺项，':''}不是新回答或同题复测。`))])
}})
const ProviderChoice=defineComponent({setup:()=>()=>h('div',{class:'provider-choice'},[h('span',{class:'small-label'},'使用'),h(ElRadioGroup,{modelValue:provider.value,'onUpdate:modelValue':value=>provider.value=value,disabled:busy.value,'aria-label':'AI 生成方式'},()=>[nativeAvailable.value?h(ElRadioButton,{value:'doubao'},()=>'豆包网页'):null,h(ElRadioButton,{value:'api'},()=>'AI API')])])})
const ModelOptions=defineComponent({setup:()=>()=>h('div',{class:'form-pair'},[h('div',[h('label',{class:'small-label'},'豆包界面'),h(ElSelect,{modelValue:clientMode.value,'onUpdate:modelValue':value=>{clientMode.value=value;model.value=''},disabled:busy.value,'aria-label':'豆包界面'},()=>[h(ElOption,{value:'consumer',label:'豆包对话'}),h(ElOption,{value:'work',label:'豆包工作'})])]),h('div',[h('label',{class:'small-label'},'模型选项（可选）'),h(ElInput,{modelValue:model.value,'onUpdate:modelValue':value=>model.value=value,disabled:busy.value,placeholder:'留空使用当前模型','aria-label':'模型选项'})])])})
const run=async action=>{if(busy.value)return;working.value=true;error.value='';try{await action()}catch(cause){error.value=cause?.response?.data?.msg||cause?.message||'操作未完成，当前资料已保留，请检查后重试'}finally{working.value=false;persist()}}
const persist=()=>{if(restoring||!projectId.value||!sameBrand(project.value,form))return;try{writeJourney(projectId.value,{project_id:projectId.value,step:step.value,company:form.company,audience:form.audience,aliases:profileLines(form.aliasesText),official_urls:profileLines(form.officialUrlsText),clientMode:clientMode.value,model:model.value,provider:provider.value,plan:plan.value,reportId:reportId.value,cycleId:cycleId.value,briefId:briefId.value,platform:platform.value,articleId:articleId.value,taskId:taskId.value,taskKind:taskKind.value,replacementSourceId:replacementSourceId.value,editor:{...editor},editorDirty:Boolean(editorDirty.value),editorBase:articleSnapshot(activeDraft.value),factRows:factRows.value,factsBase:cycle.value?.facts||[],recoveryDraft:recoveryDraft.value,recoveryFacts:recoveryFacts.value});saveError.value=''}catch{saveError.value='本机进度保存未成功，请先保留稿件；服务器上已保存的品牌、报告与文章不受影响。'}}
const exportRecovery=kind=>{const value=kind==='draft'?recoveryDraft.value:recoveryFacts.value,url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=`journey-${projectId.value}-${kind}-recovery.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const loadEditor=()=>{const draft=activeDraft.value;if(draft)Object.assign(editor,{title:draft.title,content:draft.content,summary:draft.summary||'',tags:draft.tags||[]});reviewConfirmed.value=false}
const selectContent=async(field,value)=>{if(busy.value)return;try{if(editorDirty.value)await ElMessageBox.confirm('当前文章有未保存的修改。切换后将打开另一份内容，未保存修改会被放弃。','切换内容',{confirmButtonText:'放弃修改并切换',cancelButtonText:'继续编辑'});if(field==='brief')briefId.value=value;else if(field==='article'){articleId.value=value;loadEditor()}else platform.value=value}catch{}}
const applyCycle=(value,{keepEditor=false}={})=>{
  if(value.project_id!==projectId.value||!sameBrand(value.profile,project.value))throw new Error('内容任务与当前品牌或官网不一致，已阻止混用')
  const keepFactRows=keepEditor&&cycle.value?.id===value.id&&JSON.stringify(cycle.value.facts)===JSON.stringify(value.facts)
  cycle.value=value;cycleId.value=value.id
  if(!value.analysis.briefs.some(item=>item.id===briefId.value))briefId.value=value.analysis.briefs[0]?.id||''
  if(!value.channels[platform.value])platform.value=''
  if(!matchingDrafts.value.some(item=>item.article_id===articleId.value))articleId.value=matchingDrafts.value[0]?.article_id||null
  if(!keepEditor)loadEditor()
  const facts=value.facts.length?value.facts:value.fact_candidates?.facts||[{text:project.value.description,source_url:'',confirmed:false}]
  if(!keepFactRows)factRows.value=facts.map(item=>({text:item.text,source_url:item.source_url||'',confirmed:value.facts.length>0&&item.confirmed===true}))
}
const refreshCycle=()=>run(async()=>{if(cycleId.value)applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true});await loadAccounts()})
const loadReports=async()=>{
  const all=(await geoTestApi.listReports()).data||[], sameName=all.filter(item=>item.name?.trim().toLocaleLowerCase()===project.value?.name.trim().toLocaleLowerCase())
  const results=await Promise.allSettled(sameName.map(async item=>({...item,report:(await geoTestApi.getReport(item.id)).data})))
  reports.value=results.filter(item=>item.status==='fulfilled'&&sameBrand(item.value.report.profile,project.value)).map(item=>item.value)
}
const chooseProject=async id=>{
  if(busy.value)return
  if(!id){projectChoice.value=null;projectId.value=null;Object.assign(form,emptyBrand());resetRecords();return}
  await run(async()=>{
    persist();restoring=true
    try{
      const selected=projects.value.find(item=>item.id===id);if(!selected)throw new Error('品牌项目不存在')
      projectId.value=id;projectChoice.value=id;resetRecords()
      const saved=readJourney(id)
      const draft=restoreEntityDraftFields(selected,saved,id)
      Object.assign(form,{name:selected.name,website:selected.website,description:selected.description||'',entity_type:entityType(selected),company:draft.company,audience:draft.audience,aliasesText:profileLines(draft.aliases).join('\n'),officialUrlsText:profileLines(draft.official_urls).join('\n')})
      clientMode.value=['consumer','work'].includes(saved.clientMode)?saved.clientMode:'consumer';model.value=saved.model||'';provider.value=saved.provider==='api'?'api':nativeAvailable.value?'doubao':'api'
      await loadReports()
      if(saved.plan&&sameBrand(saved.plan.profile,selected))plan.value=saved.plan
      if(saved.reportId){const record=reports.value.find(item=>item.id===saved.reportId);if(record){report.value=record.report;reportId.value=record.id;historyReportId.value=record.id}}
      briefId.value=saved.briefId||'';platform.value=saved.platform||'';articleId.value=saved.articleId||null
      recoveryDraft.value=saved.recoveryDraft||null;recoveryFacts.value=saved.recoveryFacts||null
      if(saved.cycleId){try{
        applyCycle((await geoContentApi.get(saved.cycleId)).data)
        if(saved.editorDirty&&saved.editor){
          if(canRestoreArticleEdits(saved,activeDraft.value,cycleId.value))Object.assign(editor,{title:String(saved.editor.title||''),content:String(saved.editor.content||''),summary:String(saved.editor.summary||''),tags:Array.isArray(saved.editor.tags)?saved.editor.tags:[]})
          else recoveryDraft.value={article_id:saved.articleId,editor:saved.editor,baseline:saved.editorBase}
        }
        if(canRestoreFactEdits(saved,cycle.value))factRows.value=saved.factRows.filter(item=>typeof item?.text==='string'&&typeof item?.source_url==='string').map(item=>({...item,confirmed:cycle.value.facts.some(fact=>fact.text===item.text&&fact.source_url===item.source_url)&&item.confirmed===true}))
        else if(Array.isArray(saved.factRows)&&saved.factRows.length&&JSON.stringify(saved.factRows.map(({text,source_url,confirmed})=>({text,source_url,confirmed})))!==JSON.stringify((saved.factsBase||[]).map(({text,source_url,confirmed})=>({text,source_url,confirmed}))))recoveryFacts.value=saved.factRows
      }catch(cause){error.value=cause.message||'上次内容任务无法恢复，可重新选择报告'}}
      taskId.value=saved.taskId||'';taskKind.value=saved.taskKind||'';replacementSourceId.value=Number.isSafeInteger(saved.replacementSourceId)&&saved.replacementSourceId>0?saved.replacementSourceId:null;step.value=Number.isInteger(saved.step)&&canVisit(saved.step)?Math.max(0,Math.min(saved.step,4)):0
      if(step.value===3)await loadAccounts()
    }finally{restoring=false}
  })
}
const resetRecords=()=>{plan.value=null;planConfirmed.value=false;report.value=null;reportId.value='';historyReportId.value='';reportOrigin.value='history';cycle.value=null;cycleId.value='';briefId.value='';platform.value='';articleId.value=null;factRows.value=[];recoveryDraft.value=null;recoveryFacts.value=null;replacementSourceId.value=null;taskId.value='';taskKind.value='';step.value=0}
const saveBrand=async()=>{
  const website=normalizeWebsite(form.website), name=form.name.trim(), description=form.description.trim(), fields=formProfile(), existing=projects.value.find(item=>sameBrand(item,{name,website,...fields}))
  const payload={name,website,description,...fields}
  const saved=(await(existing?projectApi.updateProject(existing.id,payload):projectApi.createProject({...payload,product:fields.entity_type==='product'?name:'',industry:'',keywords:[],competitors:[]}))).data
  const changed=projectId.value!==saved.id
  if(changed)resetRecords()
  projectId.value=saved.id;projectChoice.value=saved.id;projects.value=projects.value.filter(item=>item.id!==saved.id).concat(saved);Object.assign(form,{name:saved.name,website:saved.website,description:saved.description||'',entity_type:entityType(saved),company:saved.company??fields.company,audience:saved.audience??fields.audience,aliasesText:profileLines(saved.aliases??fields.aliases).join('\n'),officialUrlsText:profileLines(saved.official_urls??fields.official_urls).join('\n')});await loadReports();step.value=1
}
const startNative=async(kind,action)=>{const result=await action();if(result.error)throw new Error(result.error);task.value={...result,kind};taskId.value=result.id;liveTaskId=result.id;taskKind.value=kind;handledTask='';persist()}
const login=()=>run(()=>startNative('login',()=>window.pywebview.api.open_login(options())))
const makePlan=()=>run(async()=>{if(profileDirty.value)throw new Error('测试对象资料已变更，请先保存，再为该对象生成新问题');planConfirmed.value=false;const profile={...project.value,...formProfile()};if(provider.value==='doubao'){if(!nativeAvailable.value)throw new Error('桌面豆包采集服务尚未就绪');await startNative('plan',()=>window.pywebview.api.plan_with_doubao(profile,3,options()))}else plan.value=(await geoTestApi.generatePlan({project_id:projectId.value,case_count:3,...entityProfileFields(profile)})).data})
const useHistory=()=>run(async()=>{const record=reports.value.find(item=>item.id===historyReportId.value);if(!record)throw new Error('报告不存在');report.value=record.report;reportId.value=record.id;reportOrigin.value='history';cycle.value=null;cycleId.value='';articleId.value=null;plan.value=null;planConfirmed.value=false})
const reusablePlan=computed(()=>Boolean(plan.value&&report.value&&plan.value.plan_sha256===report.value.plan_sha256&&sameBrand(plan.value.profile,project.value)))
const resetBaseline=async reuse=>{try{await ElMessageBox.confirm(reuse?'将用原来的问题开启独立新一轮，包括再次提问已完成的题目。原记录会保留；请检查模型与界面设置，再预览确认。':'将重新生成问题，原报告、文章和任务单仍会保留。新问题仍需你预览确认。',reuse?'沿用问题新测一轮':'重新出题',{confirmButtonText:'继续',cancelButtonText:'取消'});report.value=null;reportId.value='';historyReportId.value='';cycle.value=null;cycleId.value='';articleId.value=null;if(!reuse)plan.value=null;planConfirmed.value=false;briefId.value='';platform.value='';taskId.value='';taskKind.value=''}catch{}}
const newBaseline=()=>resetBaseline(false)
const repeatBaseline=()=>resetBaseline(true)
const createCycle=async()=>{if(!sameBrand(report.value.profile,project.value))throw new Error('采集报告与当前品牌不一致');if(!usableReport.value)throw new Error('当前报告没有有效回答，请新建一轮基线');applyCycle((await geoContentApi.create({report_id:reportId.value,project_id:projectId.value})).data);step.value=2}
const generateResearch=async kind=>{if(provider.value==='doubao')await startNative(kind,()=>window.pywebview.api.generate_research(cycleId.value,kind,briefId.value,platform.value,options()));else{const result=(await geoContentApi.generate(cycleId.value,{kind,brief_id:briefId.value,platform:platform.value})).data;applyCycle(result.cycle);if(result.article_id){articleId.value=result.article_id;loadEditor()}}}
const replaceTargetDraft=async()=>{
  applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true})
  if(!needsReplacement.value)throw new Error('目标配置与这份稿件没有版本差异，请继续核对当前稿件')
  if(!factsSaved.value)throw new Error('请先在完整资料页核对并保存当前写作事实，再另存新版')
  const original=activeDraft.value
  try{await ElMessageBox.confirm('将按当前目标配置另存一篇草稿，再重新审稿。旧稿和原发布记录会保留，不会重复提交旧稿；已有当前版本时将打开该稿。未保存的旧稿编辑会保留为可导出的本机恢复副本。','按当前目标另存新稿',{confirmButtonText:'另存新稿',cancelButtonText:'保留旧稿'})}catch{return}
  if(editorDirty.value)recoveryDraft.value={article_id:original.article_id,editor:{...editor},baseline:articleSnapshot(original)}
  replacementSourceId.value=original.article_id;step.value=2
  try{await generateResearch('draft');if(provider.value==='api')replacementSourceId.value=null}catch(cause){replacementSourceId.value=null;throw cause}
}
const saveFacts=async()=>{applyCycle((await geoContentApi.facts(cycleId.value,{facts:confirmedFacts(),revision:cycle.value.revision})).data);ElMessage.success('核对后的事实已保存')}
const reviewArticle=async()=>{if(needsReplacement.value)throw new Error('目标配置已变化，请先按当前目标另存新稿');if(lengthInvalid.value)throw new Error([lengthReview.value.titleIssue,lengthReview.value.bodyIssue].filter(Boolean).join(' '));await articleApi.updateArticle(activeDraft.value.article_id,{title:editor.title.trim(),content:editor.content.trim(),summary:editor.summary,tags:editor.tags,status:'ready'});applyCycle((await geoContentApi.get(cycleId.value)).data);reviewConfirmed.value=false;ElMessage.success('审稿已保存，可以进入发布')}
const loadAccounts=async()=>{capabilitiesLoaded.value=false;const [overview,platforms]=await Promise.all([accountApi.getMediaAccounts(),publishApi.getPlatforms()]);accounts.value=overview.data?.accounts||[];capabilities.value=platforms.data||[];capabilitiesLoaded.value=true}
const refreshAccounts=()=>run(loadAccounts)
const checkAccount=id=>run(async()=>{await accountApi.checkMediaAccount(id);await loadAccounts()})
const openDetails=()=>router.push({path:'/geo-content',query:{cycle:cycleId.value,journey_project:String(projectId.value)}})
const openPublisher=async()=>{
  applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true})
  if(needsReplacement.value)throw new Error('目标配置已变化，请先按当前目标另存新稿')
  if(!reviewedDraft.value)throw new Error('稿件尚未审稿就绪，或已有内容变更，请返回内容步骤重新核对')
  if(lengthInvalid.value)throw new Error([lengthReview.value.titleIssue,lengthReview.value.bodyIssue].filter(Boolean).join(' '))
  if(ownedDraft.value){const draft=activeDraft.value,url=URL.createObjectURL(new Blob([`# ${draft.title}\n\n${draft.content}`],{type:'text/markdown;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=`article-${draft.article_id}.md`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);ElMessage.success('官网稿件已导出，发布结果需由网站编辑或 CMS 确认');return}
  await loadAccounts()
  if(publisherPaused.value)throw new Error(`${publisherName.value}发布器当前暂停，稿件已保留，请等待能力恢复`)
  if(targetPublisher.value){await router.push({path:'/publish-center',query:{article_id:String(activeDraft.value.article_id),platform:publishChannel.value,journey_project:String(projectId.value)}});return}
  if(selectedTarget.value){if(!selectedTarget.value.config.title_selector){openDetails();return}await ElMessageBox.confirm('将向目标平台的空白编辑器填写这篇已审稿文章，网站可能自动保存。请核对后自行提交，软件不会点击发布。','打开并填写',{confirmButtonText:'打开并填写',cancelButtonText:'取消'});await startNative('destination',()=>window.pywebview.api.open_destination(cycleId.value,selectedTarget.value.id,activeDraft.value.article_id))}
  else router.push({path:'/publish-center',query:{article_id:String(activeDraft.value.article_id),platform:activeDraft.value.platform,journey_project:String(projectId.value)}})
}
const goFeedback=()=>run(async()=>{applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true});step.value=4})
const continuePublication=job=>run(async()=>{
  applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true})
  const draft=cycle.value.drafts.find(item=>item.article_id===job.article_id), current=draft?.jobs.find(item=>item.id===job.id&&!item.demo)
  if(!current)throw new Error('原发布任务尚未读取到，请刷新记录后重试，未另建任务')
  await router.push({path:'/publish-center',query:{article_id:String(draft.article_id),platform:current.platform,journey_project:String(projectId.value)}})
})
const recordPublication=()=>run(async()=>{if(!geoContentApi.publication)throw new Error('提交结果登记服务尚未就绪，请在采集驱动内容页登记');applyCycle((await geoContentApi.publication(cycleId.value,{article_id:activeDraft.value.article_id,target_id:selectedTarget.value.id,url:publication.url,published_at:new Date(publication.publishedAt).toISOString(),confirmed:publication.confirmed})).data);publication.confirmed=false;step.value=4})
const primaryAction=async()=>{
  if(primaryDisabled.value)return
  if(step.value===1&&!report.value&&!plan.value){await makePlan();return}
  await run(async()=>{
    if([2,3].includes(step.value)&&needsReplacement.value)await replaceTargetDraft()
    else if(step.value===0)await saveBrand()
    else if(step.value===1){if(report.value)await createCycle();else await startNative('run',()=>window.pywebview.api.start_run(plan.value,options()))}
    else if(step.value===2){if(needsAnalysis.value)await generateResearch('analysis');else if(!activeDraft.value){if(!factsSaved.value)await saveFacts();else await generateResearch('draft')}else if(publishedDraft.value){applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:true});step.value=4}else if(!reviewedDraft.value)await reviewArticle();else{await loadAccounts();step.value=3}}
    else if(step.value===3)await openPublisher()
    else await startNative('retest',()=>window.pywebview.api.start_research_retest(cycleId.value))
  })
}
const stopTask=async()=>{try{await window.pywebview.api.stop_task()}catch(cause){error.value=cause.message||'停止请求未成功'}}
const poll=async()=>{
  if(!nativeAvailable.value||pollPending)return;pollPending=true
  try{
    const current=await window.pywebview.api.get_task();if(destroyed)return
    finishingTask.value=Boolean(current.id&&current.id===taskId.value&&current.status!=='running'&&handledTask!==current.id);task.value=current
    if(!current.id||current.status==='running'||current.id!==taskId.value||handledTask===current.id)return
    if(current.status==='failed'||current.status==='stopped'||current.status==='needs_review')error.value=current.message||'任务未完成，记录已保留，请检查后重试'
    if(current.result?.plan&&sameBrand(current.result.plan.profile,project.value)){plan.value=current.result.plan;planConfirmed.value=false}
    if(current.result?.report&&taskKind.value==='run'&&sameBrand(current.result.report.profile,project.value)){await loadReports();const item=reports.value.find(item=>item.run_id===current.result.report.run_id);if(item){report.value=item.report;reportId.value=item.id;reportOrigin.value=liveTaskId===current.id?'fresh':'history';historyReportId.value=item.id}else throw new Error('本轮报告尚未进入报告列表，请稍后重试，未使用其他报告替代')}
    if(cycleId.value&&['analysis','draft','retest','destination'].includes(taskKind.value))applyCycle((await geoContentApi.get(cycleId.value)).data,{keepEditor:taskKind.value!=='draft'})
    if(taskKind.value==='draft'&&replacementSourceId.value){
      if(current.status==='completed'){
        const target=selectedTarget.value, draft=matchingDrafts.value.find(item=>item.article_id!==replacementSourceId.value&&item.provenance?.destination?.id===target?.id&&item.provenance?.destination?.revision===target?.revision&&item.provenance?.destination_config_sha256===target?.config_sha256)
        if(!draft)throw new Error('新版任务已结束，但尚未读取到对应目标版本的新稿，请刷新记录；旧稿未重新提交')
        articleId.value=draft.article_id;loadEditor()
      }
      replacementSourceId.value=null
    }
    handledTask=current.id;persist()
  }catch(cause){if(!destroyed)error.value=cause.message||'任务状态读取失败，可保留当前窗口稍后重试'}finally{pollPending=false;finishingTask.value=false}
}
watch([step,form,clientMode,model,provider,plan,reportId,cycleId,briefId,platform,articleId,editor,factRows,replacementSourceId],persist,{deep:true})
watch(step,value=>{if(value===3&&!capabilitiesLoaded.value&&!busy.value)void refreshAccounts()})
watch(()=>[form.name,form.website,form.entity_type,form.company,form.audience,form.description,form.aliasesText,form.officialUrlsText],()=>{if(!restoring){plan.value=null;planConfirmed.value=false}},{flush:'sync'})
watch(()=>[editor.title,editor.content],()=>{reviewConfirmed.value=false},{flush:'sync'})
watch([briefId,platform],()=>{if(restoring)return;articleId.value=matchingDrafts.value[0]?.article_id||null;loadEditor()},{flush:'sync'})
onMounted(async()=>{
  window.addEventListener('pywebviewready',activateNative)
  working.value=true
  try{projects.value=(await projectApi.getProjects()).data||[];for(let attempt=0;attempt<50&&!nativeMethodsReady()&&!destroyed;attempt++)await new Promise(resolve=>setTimeout(resolve,100));nativeAvailable.value=nativeMethodsReady();if(!nativeAvailable.value)provider.value='api'}catch(cause){error.value=cause.message||'品牌资料加载失败'}finally{working.value=false}
  if(destroyed)return
  const requested=Number(route.query.journey_project||route.query.project)||lastJourneyProject();if(projects.value.some(item=>item.id===requested))await chooseProject(requested)
  if(route.query.journey_project&&cycle.value&&activeDraft.value)step.value=4
  initialized=true
  if(!activateNative())bridgeTimer=setInterval(activateNative,250)
})
onUnmounted(()=>{persist();destroyed=true;clearInterval(timer);clearInterval(bridgeTimer);window.removeEventListener('pywebviewready',activateNative)})
</script>

<style scoped>
.entity-hint{flex-basis:100%;margin-bottom:0}
.baseline-context{display:grid;gap:6px;padding:12px 16px;margin:16px 0;border-left:3px solid #bcd9d2;background:#f6f9f8;color:var(--geo-muted);font-size:12px;line-height:1.8}.publication-tasks h3{margin-top:12px}.publication-tasks .el-button{margin-top:12px}
.recovery-note{padding:16px 20px;border:1px solid #e7d4b3;border-radius:12px;background:#fffaf1;color:#7b5b26;font-size:12px;line-height:1.8}.recovery-note .el-button{margin-top:6px}
.journey{display:grid;gap:24px;max-width:1060px}.journey-header{display:flex;align-items:center;justify-content:space-between;gap:24px}.eyebrow{margin-bottom:8px;color:var(--geo-primary);font-size:11px;letter-spacing:.08em}.journey-steps{display:flex;gap:8px}.journey-steps>button{display:flex;align-items:center;justify-content:center;gap:9px;flex:1;padding:13px 10px;border:1px solid transparent;border-radius:10px;color:var(--geo-muted);font-size:13px;transition:background-color var(--geo-duration-normal),color var(--geo-duration-normal),border-color var(--geo-duration-normal)}.journey-steps>button span{display:grid;place-items:center;width:23px;height:23px;border-radius:50%;background:#e7edec;font-size:11px}.journey-steps>button.active{color:var(--geo-primary);border-color:#bcd9d2;background:#eef6f3;font-weight:600}.journey-steps>button.active span{color:white;background:var(--geo-primary)}.journey-steps>button.done{color:var(--geo-primary)}.journey-steps>button:disabled{cursor:default}.journey-steps .el-icon{font-size:12px}.step-panel{padding:30px 34px}.section-heading{margin-bottom:24px}.step-kicker{display:block;margin-bottom:8px;color:var(--geo-muted);font-size:11px;letter-spacing:.1em}h2{font-size:22px;line-height:1.5;font-weight:650}h3{font-size:15px;font-weight:600}.section-heading>p,.hint{margin:8px 0;color:var(--geo-muted);font-size:13px;line-height:1.8}.form-pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.el-form-item{margin-bottom:20px}.el-select{width:100%}.advanced{margin-top:16px;border-bottom:0}.advanced:deep(.el-collapse-item__header){font-size:12px;color:var(--geo-muted)}.advanced:deep(.el-collapse-item__wrap){border-bottom:0}.advanced:deep(.el-collapse-item__content){padding:12px 0 4px}.small-label{display:block;color:var(--geo-text);font-size:12px;margin-bottom:8px}.provider-choice{display:flex;align-items:center;gap:14px;margin:20px 0}.provider-choice .small-label{margin:0}.utility-row,.report-heading,.draft-heading,.subheading{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0}.subheading{justify-content:space-between}.report-heading{font-size:12px;color:var(--geo-muted)}.metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:20px 0}.metric-grid>article{display:grid;gap:8px;padding:20px;border:1px solid var(--geo-border);border-radius:12px;background:#fafcfc}.metric-grid span{font-size:12px;color:var(--geo-text)}.metric-grid strong{font-size:28px;line-height:1.3;color:var(--geo-ink);font-weight:600}.metric-grid small{font-size:11px;color:var(--geo-muted)}.plan-preview{margin:20px 0}.plan-preview article{padding:16px 0;border-top:1px solid var(--geo-border)}.plan-preview article p{margin-top:8px;line-height:1.8;color:var(--geo-text);font-size:14px}.plan-preview .el-checkbox{margin-top:16px}.diagnosis{padding:18px 20px;background:#f1f7f4;border-radius:10px;line-height:1.9;font-size:14px}.source-summary{margin:16px 0 24px}.source-summary:deep(.el-collapse-item__header){font-size:13px}.source-row{padding:12px 0;border-top:1px solid var(--geo-border);font-size:13px;line-height:1.7}.source-row strong{margin-right:12px}.source-row>p{margin-top:8px;color:var(--geo-muted)}.platform-basis{margin:-5px 0 20px}.fact-row{display:flex;align-items:flex-start;gap:10px;margin:14px 0}.fact-row>div{flex:1;min-width:0;display:grid;gap:6px}.fact-row>.el-checkbox{flex-shrink:0}.fact-row>div>.el-input:deep(.el-input__wrapper){background:#fafcfc;box-shadow:none}.draft-heading{justify-content:space-between}.draft-heading .el-select{max-width:320px}.publish-preview{padding:20px;border:1px solid var(--geo-border);border-radius:12px;margin-bottom:24px}.publish-preview h3{margin:14px 0 8px;font-size:18px}.publish-preview p{font-size:12px;color:var(--geo-muted)}.account-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid var(--geo-border)}.account-row p{font-size:12px;color:var(--geo-muted);margin:5px 0}.feedback-card{padding:20px;margin-top:18px;border:1px solid var(--geo-border);border-radius:12px;line-height:1.8}.feedback-card>p{margin-top:12px;font-size:13px;color:var(--geo-text)}.comparison-row{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:12px 0;padding:12px;background:#f6f9f8;border-radius:8px;font-size:13px}.comparison-row small{color:var(--geo-muted);font-size:11px}.publication-link{display:flex;gap:12px;align-items:center;margin:12px 0;font-size:12px;overflow-wrap:anywhere}.publication-link a{color:var(--geo-primary)}.journey-actions{display:flex;align-items:center;gap:16px;padding:0 0 20px;min-height:64px}.journey-actions>p{flex:1;font-size:12px;color:var(--geo-muted);line-height:1.7}.journey-actions>.el-button--primary{margin-left:auto;min-width:180px}.task-progress{display:flex;align-items:center;gap:16px;padding:18px 20px;border:1px solid #c7dfd8;border-radius:12px;background:#eef7f3;color:var(--geo-primary)}.task-progress>div{flex:1}.task-progress strong{font-size:13px}.task-progress p{font-size:12px;line-height:1.8;color:var(--geo-muted);margin-top:5px}.task-progress>.el-icon{font-size:20px}.el-checkbox{max-width:100%;height:auto;min-height:32px}.el-checkbox:deep(.el-checkbox__label){white-space:normal;line-height:1.8;font-size:13px}.el-alert{margin-top:16px}.journey-alert{margin-top:0}@media(max-width:850px){.step-panel{padding:24px}.journey-steps>button{gap:6px;padding:12px 4px;font-size:12px}.journey-steps .el-icon{display:none}.metric-grid{gap:8px}.metric-grid>article{padding:14px}.comparison-row{flex-wrap:wrap}}@media(max-width:600px){.journey-header{align-items:flex-start;gap:12px}.journey-header>.el-button{font-size:11px;padding:8px}.journey-steps{gap:2px}.journey-steps>button{flex-direction:column;font-size:10px;gap:6px}.step-panel{padding:20px 16px}.section-heading h2{font-size:20px}.form-pair{grid-template-columns:1fr;gap:0}.metric-grid{grid-template-columns:1fr}.metric-grid>article{grid-template-columns:1fr auto;align-items:center}.metric-grid small{grid-column:1/-1}.metric-grid strong{font-size:24px}.journey-actions{flex-wrap:wrap;gap:12px}.journey-actions>p{order:2;flex-basis:100%}.journey-actions>.el-button--primary{min-width:0;flex:1}.provider-choice{flex-wrap:wrap}.publication-link{align-items:flex-start;flex-direction:column}.task-progress{align-items:flex-start}.task-progress .el-button{padding:8px}.account-row{align-items:flex-start}}
</style>
