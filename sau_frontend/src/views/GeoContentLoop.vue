<template>
  <main class="content-loop" data-testid="geo-content-loop">
    <header><div><h1>采集驱动内容</h1><p>从引用来源找到投放目标，接入平台、适配内容，再追踪发布与复测。</p></div><el-button :loading="loading" @click="reload">刷新</el-button></header>
    <el-card shadow="never">
      <h2>1 · 选择采集依据</h2>
      <div class="row"><el-select v-model="reportId" placeholder="选择采集报告" aria-label="选择采集报告"><el-option v-for="report in reports" :key="report.id" :value="report.id" :label="`${report.name} · ${report.case_count} 题 · ${date(report.updated_at)}`" /></el-select><el-button type="primary" :disabled="!reportId || busy" @click="create">建立内容任务单</el-button></div>
      <p class="hint">使用报告里的原始问题与来源。不同客户各建任务单，自动关联同名品牌项目。</p>
      <el-select v-model="cycleId" placeholder="打开已保存的任务单" aria-label="已保存的内容任务单" @change="loadCycle"><el-option v-for="item in cycles" :key="item.id" :value="item.id" :label="`${item.name} · ${item.draft_count} 份稿件 · ${date(item.updated_at)}`" /></el-select>
    </el-card>
    <el-card v-if="nativeAvailable" shadow="never">
      <div class="row"><strong>豆包写作会话</strong><el-select v-model="clientMode" :disabled="busy" aria-label="写作豆包界面"><el-option label="豆包对话" value="consumer"/><el-option label="豆包工作" value="work"/></el-select><el-input v-model="model" :disabled="busy" placeholder="模型选项可留空" aria-label="写作模型选项"/><el-button :disabled="busy" @click="login">登录豆包</el-button><el-button v-if="nativeBusy" @click="stop">停止任务</el-button></div>
      <p class="hint">{{ task.message || (nativeBusy ? '任务执行中；遇到验证请在专用窗口正常完成。' : '平台登录、填写、写作与曝光采集分开记录。') }}</p>
    </el-card>
    <template v-if="cycle">
      <el-card shadow="never">
        <div class="row"><h2>2 · {{ cycle.profile.name }} · 内容选题</h2><el-tag>{{ cycle.analysis.method === 'rule_observations_not_ai' ? '采集待办，尚未 AI 策划' : '已完成 AI 策划' }}</el-tag></div>
        <p>{{ cycle.analysis.diagnosis }}</p>
        <div class="row"><el-button v-if="nativeAvailable" type="primary" :disabled="busy" @click="generate('analysis', 'doubao')">豆包分析采集并制定选题</el-button><el-button :disabled="busy" @click="generate('analysis', 'api')">用已配置的 AI API 策划</el-button><el-button @click="download(cycle, 'research-task.json')">导出任务单</el-button></div>
        <div class="observations"><p v-for="item in cycle.observations.cases" :key="item.id"><strong>{{ item.id }} · {{ category[item.category] }}</strong>：{{ item.finding }}</p></div>
        <el-table :data="cycle.analysis.briefs" data-testid="research-briefs"><el-table-column label="优先级" width="90"><template #default="{row}">{{ row.priority === 'high' ? '优先' : '后续' }}</template></el-table-column><el-table-column prop="topic" label="选题" min-width="220"/><el-table-column label="采集依据 / 待补资料" min-width="300"><template #default="{row}"><p>{{ row.case_ids.join('、') }} · {{ row.why }}</p><p v-for="text in row.missing_evidence" :key="text" class="hint">待补：{{ text }}</p><p v-if="row.outline.length" class="hint">结构：{{ row.outline.join(' → ') }}</p></template></el-table-column></el-table>
        <el-collapse><el-collapse-item title="查看观察到的来源（网址未知仍保留未知）" name="sources"><p class="hint">{{ cycle.observations.source_note }}</p><ul><li v-for="source in cycle.observations.sources" :key="source.id">{{ source.id }} · {{ source.case_id }} · {{ source.kind === 'citations' ? '正文引用' : '界面资料' }} · <a v-if="source.url" :href="safeUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title || source.domain }}</a><span v-else>{{ source.title || '标题未知' }} · 网址未确认</span></li></ul></el-collapse-item></el-collapse>
      </el-card>
      <GeoDestinations :cycle="cycle" :busy="busy" :native-available="nativeAvailable" @updated="applyCycle" @select="selectDestination" @open="openDestination"/>
      <el-card shadow="never">
        <h2>3 · 补充可用于写作的事实</h2>
        <p class="hint">每行“事实内容 | 来源网址”，没有公开网址时留空，稿件须注明据运营方说明。采集回答不会自动变成事实库。</p>
        <el-input v-model="factsText" type="textarea" :rows="6" :disabled="busy" data-testid="research-facts" placeholder="例如：官网明确标注运营公司全称 | https://客户官网/关于我们" />
        <div class="row"><el-checkbox v-model="confirmed" :disabled="busy">我已核对这些资料，允许据此起草</el-checkbox><el-button :disabled="busy || !confirmed || !factsText.trim()" @click="saveFacts">保存事实资料</el-button></div>
        <p class="hint">资料修改后请先保存；新的事实或选题会生成独立版本。AI 稿件仍需要审稿。</p>
      </el-card>
      <el-card shadow="never">
        <h2>4 · 生成文章或帖子，进入现有发布流程</h2>
        <div class="row"><el-select v-model="briefId" aria-label="内容选题"><el-option v-for="brief in cycle.analysis.briefs" :key="brief.id" :value="brief.id" :label="brief.topic"/></el-select><el-select v-model="platform" aria-label="目标发布渠道"><el-option v-for="(label,key) in cycle.channels" :key="key" :value="key" :label="label"/></el-select></div>
        <p class="hint">{{ selectedDestination ? `已选择接入平台：${selectedDestination.config.name}；稿件将使用该平台的内容规范与长度限制。` : '当前为通用渠道稿。可在上方来源投放目标中接入并选择具体平台。' }}</p>
        <div class="row"><el-button v-if="nativeAvailable" type="primary" :disabled="busy || !canDraft" @click="generate('draft','doubao')">豆包生成并存入内容库</el-button><el-button :disabled="busy || !canDraft" @click="generate('draft','api')">AI API 生成并存入内容库</el-button></div>
        <p v-if="!canDraft" class="hint">请先保存核对后的事实资料，并保持输入与已保存内容一致。</p>
        <article v-for="draft in cycle.drafts" :key="draft.article_id" class="draft" data-testid="research-draft">
          <h3>{{ draft.title }}</h3><p>{{ cycle.channels[draft.platform] }} · {{ status[draft.status] || draft.status }} · 原始问题 {{ draft.provenance.brief.case_ids.join('、') }}</p>
          <p>{{ draft.summary }}</p>
          <div class="row"><el-button @click="edit(draft)">审稿 / 编辑</el-button><el-button v-if="draft.provenance.destination" :disabled="busy || !nativeAvailable || draft.status !== 'ready'" @click="fillDestination(draft)">打开目标平台并填写</el-button><el-button v-if="legacyChannel(draft) && legacyChannel(draft) !== 'owned_web'" :disabled="draft.status !== 'ready'" @click="publish(draft)">进入现有发布中心</el-button><el-button @click="downloadArticle(draft)">导出稿件</el-button></div>
          <p v-if="draft.provenance.destination" class="hint">来源匹配依据：{{ draft.provenance.distribution_basis?.source_ids.join('、') || '手动选择，当前选题未匹配来源网址' }}。填写可能触发网站自动保存；发布状态以实际结果为准。已有发布中心需另外绑定对应账号。</p>
          <p v-if="draft.platform === 'owned_web'" class="hint">官网稿可导出交给网站编辑发布。</p>
          <p v-for="job in draft.jobs" :key="job.id" class="hint">{{ job.demo ? '演示任务' : '发布任务' }} #{{ job.id }} · {{ job.platform }} · {{ jobStatus[job.status] || job.status }} <a v-if="safeUrl(job.result_url)" :href="safeUrl(job.result_url)" target="_blank" rel="noopener noreferrer">查看发布链接</a></p>
          <el-collapse><el-collapse-item title="预览草稿" :name="draft.article_id"><pre>{{ draft.content }}</pre><p class="hint">使用事实：{{ draft.provenance.used_fact_ids.join('、') }}；生成来源：{{ draft.provenance.method }}。文章内容和来源仍需人工核对。</p></el-collapse-item></el-collapse>
        </article>
        <el-empty v-if="!cycle.drafts.length" description="尚未生成平台稿"/>
      </el-card>
      <el-card shadow="never">
        <h2>5 · 发布记录与同题复测</h2>
        <p>已确认成功的真实发布链接：{{ cycle.publications.length }} 条。演示、失败和缺少结果链接的任务不算已发布。</p>
        <p class="hint">复测使用原计划、原界面和原模型选项，新开一轮采集。发布不代表已被索引；条件相同也只能观察变化，不能证明因果。</p>
        <el-button v-if="nativeAvailable" :disabled="busy" @click="retest">按原计划开始新一轮复测</el-button>
        <div class="row"><el-select v-model="retestReportId" aria-label="关联复测报告" placeholder="或选择已有报告"><el-option v-for="report in reports" :key="report.id" :value="report.id" :label="`${report.name} · ${date(report.updated_at)}`"/></el-select><el-button :disabled="!retestReportId || busy" @click="attachRetest">关联并比较</el-button></div>
        <article v-for="report in cycle.retests" :key="report.run_id" class="draft">
          <h3>复测 {{ date(report.updated_at) }}</h3><p>{{ report.comparison.conditions_match ? '计划、界面及模型标签匹配，按共同有效题观察' : `条件不匹配：${report.comparison.reasons.join('；')}` }}</p>
          <el-table :data="report.comparison.cohorts"><el-table-column label="类别"><template #default="{row}">{{ category[row.category] }}</template></el-table-column><el-table-column prop="paired_answers" label="共同有效题"/><el-table-column label="之前提及"><template #default="{row}">{{ rate(row.before_brand_rate) }}</template></el-table-column><el-table-column label="复测提及"><template #default="{row}">{{ rate(row.after_brand_rate) }}</template></el-table-column></el-table>
          <p class="hint">官网正文引用：<span v-for="row in report.comparison.cohorts" :key="row.category">{{ category[row.category] }} {{ rate(row.official_citation_before) }} → {{ rate(row.official_citation_after) }}（共同可判断 {{ row.official_citation_paired }} 题）；</span></p>
          <p class="hint">{{ report.comparison.publication_context }}</p><p class="hint">{{ report.comparison.note }}</p><p v-for="link in report.comparison.publication_citations" :key="link.url" class="hint">{{ link.url }} · {{ link.observed_in_citations === true ? '在本次正文引用中观察到' : '暂未观察到引用，是否被索引未知' }}</p>
        </article>
      </el-card>
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { geoContentApi } from '@/api/geoContent'
import { geoTestApi } from '@/api/geoTest'
import GeoDestinations from '@/components/GeoDestinations.vue'

const route = useRoute(), router = useRouter()
const reports = ref([]), cycles = ref([]), cycle = ref(null), cycleId = ref(''), reportId = ref(''), retestReportId = ref('')
const loading = ref(false), working = ref(false), nativeAvailable = ref(false), task = ref({status:'idle'})
const clientMode = ref('consumer'), model = ref(''), factsText = ref(''), confirmed = ref(false), briefId = ref(''), platform = ref('zhihu')
const nativeBusy = computed(() => task.value.status === 'running'), busy = computed(() => working.value || nativeBusy.value)
const category = { discovery:'行业自然发现', brand_identity:'品牌核验', website_guided:'官网引导核验' }
const status = { draft:'待审草稿', ready:'就绪', publishing:'发布中', published:'已发布' }
const jobStatus = { success:'成功', failed:'失败', queued:'待执行', scheduled:'已计划', processing:'执行中', need_action:'待人工处理' }
const factsValue = () => factsText.value.split('\n').filter(line => line.trim()).map(line => {
  const [text,...url] = line.split('|')
  return {text:text.trim(),source_url:url.join('|').trim(),confirmed:confirmed.value}
})
const factsStoredText = facts => facts.map(fact => `${fact.text} | ${fact.source_url || ''}`).join('\n')
const canDraft = computed(() => Boolean(cycle.value?.facts.length && confirmed.value && factsStoredText(factsValue()) === factsStoredText(cycle.value.facts) && briefId.value))
const selectedDestination = computed(() => cycle.value?.destinations.find(item => item.key === platform.value))
const date = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Hong_Kong',hour12:false}) : '时间未知'
const rate = value => typeof value === 'number' ? `${Math.round(value*100)}%` : '未知'
const safeUrl = value => { try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null } catch { return null } }
let timer, handledTask
const applyCycle = value => {
  cycle.value = value; cycleId.value = value.id
  factsText.value = factsStoredText(value.facts); confirmed.value = value.facts.length > 0
  if (!value.analysis.briefs.some(brief => brief.id === briefId.value)) briefId.value = value.analysis.briefs[0]?.id || ''
  if (!value.channels[platform.value]) platform.value = 'zhihu'
}
const loadCycle = async () => { if (cycleId.value) applyCycle((await geoContentApi.get(cycleId.value)).data) }
const reload = async () => {
  loading.value = true
  try { const [r,c] = await Promise.all([geoTestApi.listReports(),geoContentApi.list()]); reports.value = r.data; cycles.value = c.data; await loadCycle() }
  finally { loading.value = false }
}
const create = async () => {
  working.value = true
  try { applyCycle((await geoContentApi.create({report_id:reportId.value})).data); cycles.value = (await geoContentApi.list()).data; router.replace({path:'/geo-content',query:{cycle:cycleId.value}}) }
  finally { working.value = false }
}
const saveFacts = async () => {
  working.value = true
  try { applyCycle((await geoContentApi.facts(cycleId.value,{facts:factsValue(),revision:cycle.value.revision})).data); ElMessage.success('事实资料已保存') }
  finally { working.value = false }
}
const options = () => ({client_mode:clientMode.value,model_label:model.value.trim()})
const generate = async (kind, provider) => {
  working.value = true
  try {
    if (provider === 'doubao') {
      const result = await window.pywebview.api.generate_research(cycleId.value,kind,briefId.value,platform.value,options())
      if (result.error) throw new Error(result.error)
      task.value = result
    } else {
      const result = (await geoContentApi.generate(cycleId.value,{kind,brief_id:briefId.value,platform:platform.value})).data
      applyCycle(result.cycle); cycles.value = (await geoContentApi.list()).data
      ElMessage.success(kind === 'draft' ? '草稿已存入内容库，请审稿' : '选题已保存')
    }
  } catch (error) { if (!error.response) ElMessage.error(error.message || '内容任务未完成') }
  finally { working.value = false }
}
const login = async () => {
  const result = await window.pywebview.api.open_login(options())
  if (result.error) ElMessage.error(result.error); else task.value = result
}
const stop = () => window.pywebview.api.stop_task()
const selectDestination = key => {platform.value=key;ElMessage.success('已选择目标平台，请在下方生成平台稿')}
const openDestination = async ({target_id,article_id}) => {
  try {const result=await window.pywebview.api.open_destination(cycleId.value,target_id,article_id || null);if(result.error) throw new Error(result.error);task.value=result}
  catch(error) {ElMessage.error(error.message || '平台窗口未打开')}
}
const fillDestination = async draft => {
  try {await ElMessageBox.confirm('将把已审稿的标题和正文填入该平台空白编辑器，网站可能自动保存。请在专用窗口核对格式与账号；最后提交由你确认。','打开平台填写',{confirmButtonText:'打开并填写',cancelButtonText:'取消'});await openDestination({target_id:draft.provenance.destination.id,article_id:draft.article_id})}
  catch(error) {if(error instanceof Error) ElMessage.error(error.message)}
}
const retest = async () => {
  try {
    await ElMessageBox.confirm('将按基线问题开启新一轮豆包采集。发布后的索引时滞未知，采集结果只作观察。','开始同题复测',{type:'info'})
    const result = await window.pywebview.api.start_research_retest(cycleId.value)
    if (result.error) throw new Error(result.error)
    task.value = result
  } catch(error) { if (error instanceof Error) ElMessage.error(error.message) }
}
const attachRetest = async () => { applyCycle((await geoContentApi.retest(cycleId.value,retestReportId.value)).data) }
const edit = draft => router.push({path:'/content-create',query:{articleId:draft.article_id}})
const legacyChannel = draft => draft.provenance.destination ? draft.provenance.destination.channel : draft.platform
const publish = draft => router.push({path:'/publish-center',query:{article_id:draft.article_id,platform:legacyChannel(draft)}})
const downloadFile = (data,name,type) => { const url = URL.createObjectURL(new Blob([data],{type})); const a=document.createElement('a'); a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000) }
const download = (data,name) => downloadFile(JSON.stringify(data,null,2),name,'application/json')
const downloadArticle = draft => downloadFile(`# ${draft.title}\n\n${draft.content}`,`article-${draft.article_id}.md`,'text/markdown')
const poll = async () => {
  if (!nativeAvailable.value) return
  try {
    const current = await window.pywebview.api.get_task()
    task.value = current
    if (current.status !== 'running' && current.id && handledTask !== current.id) {
      handledTask = current.id
      if (current.cycle_id === cycleId.value) { await reload(); if (current.status === 'failed') ElMessage.error(current.message || '任务未完成') }
    }
  } catch { /* Native shutdown leaves the last durable task untouched. */ }
}
watch(clientMode, () => { model.value = '' })
onMounted(async () => {
  cycleId.value = typeof route.query.cycle === 'string' ? route.query.cycle : ''
  reportId.value = typeof route.query.report === 'string' ? route.query.report : ''
  await reload()
  if (!cycleId.value && cycles.value.length) { cycleId.value = cycles.value[0].id; await loadCycle() }
  for (let attempt=0; attempt<20 && !window.pywebview?.api; attempt++) await new Promise(resolve=>setTimeout(resolve,100))
  nativeAvailable.value = Boolean(window.pywebview?.api)
  if (nativeAvailable.value) { await poll(); timer=setInterval(poll,1000) }
})
onUnmounted(()=>clearInterval(timer))
</script>

<style scoped>
.content-loop{max-width:1200px;margin:auto;display:grid;gap:20px}.row,header{display:flex;align-items:center;gap:12px;flex-wrap:wrap}header{justify-content:space-between}h1{margin:0;font-size:28px}h2{font-size:20px;margin:0 0 16px}h3{font-size:17px}.hint,header p{color:#59696e;line-height:1.8}.row{margin:12px 0}.row .el-select{width:300px}.row .el-input{width:210px}.observations,.draft{background:#f4f8f7;padding:16px;margin-top:18px;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;font-family:inherit}li{margin:8px 0}a{color:#0a756e}.el-select{max-width:100%}@media(max-width:600px){.row .el-select,.row .el-input{width:100%}}
</style>
