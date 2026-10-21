<template>
  <main class="content-loop" data-testid="geo-content-loop">
    <div v-if="journeyProjectId" class="journey-return"><span>这里保留详细来源和平台接入配置，品牌资料与稿件会延续到同一轮流程。</span><el-button @click="returnToJourney">返回优化流程</el-button></div>
    <header><div><h1>采集驱动内容</h1><p>从来源发现内容机会，先核对平台用途，再准备稿件与反馈。</p></div><el-button :loading="loading" @click="reload">刷新</el-button></header>
    <el-card shadow="never">
      <h2>1 · 选择采集依据</h2>
      <div class="row"><el-select v-model="reportId" placeholder="选择采集报告" aria-label="选择采集报告"><el-option v-for="report in reports" :key="report.id" :value="report.id" :label="`${report.name} · ${report.case_count} 题 · ${date(report.updated_at)}`" /></el-select><el-button type="primary" :disabled="!reportId || busy" @click="create">建立内容任务单</el-button></div>
      <p class="hint">使用报告里的原始问题与来源。不同客户各建任务单，自动关联同名品牌项目。</p>
      <el-select v-model="cycleId" placeholder="打开已保存的任务单" aria-label="已保存的内容任务单" @change="loadCycle"><el-option v-for="item in cycles" :key="item.id" :value="item.id" :label="`${item.entity_type==='company'?'公司':item.entity_type==='product'?'产品':'旧对象口径'} · ${item.name} · ${item.website || `项目 #${item.project_id}`} · ${item.draft_count} 份稿件 · ${date(item.updated_at)}`" /></el-select>
    </el-card>
    <el-card v-if="nativeAvailable" shadow="never">
      <DoubaoPreparation/>
      <div class="row"><strong>豆包写作会话</strong><el-select :model-value="clientMode" :disabled="busy || writingContextPending" aria-label="写作豆包界面" @update:model-value="selectWritingMode"><el-option label="豆包对话" value="consumer"/><el-option label="豆包工作" value="work"/></el-select><el-input :model-value="model" :disabled="busy || writingContextPending" placeholder="模型选项可留空" aria-label="写作模型选项" @update:model-value="selectWritingModel"/><el-button :disabled="busy || writingContextPending" @click="login">登录豆包</el-button><el-button v-if="nativeBusy" @click="stop">停止任务</el-button></div>
      <p class="hint">{{ writingContextPending ? '正在读取本轮写作选项，请稍候。' : '写作会话初始沿用本轮基线的界面和模型选项；你调整后的选择会按任务单保留。留空表示使用当前模型。' }}</p>
      <p class="hint" aria-live="polite">{{ currentTaskMessage }}</p>
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
      <GeoDestinations :cycle="cycle" :task="task" :busy="busy" :native-available="nativeAvailable" @updated="applyCycle" @select="selectDestination" @open="openDestination" @checked="poll"/>
      <el-card shadow="never">
        <h2>3 · 补充可用于写作的事实</h2>
        <div v-if="cycle.fact_candidates && !cycle.facts.length" class="fact-reuse"><p>上轮 {{ date(cycle.fact_candidates.source_created_at) }} 核对过的资料可作为本轮候选，带入后仍需重新核对。</p><el-button :disabled="busy" @click="reuseFacts">带入上轮资料</el-button></div>
        <p class="hint">每行“事实内容 | 来源网址”，没有公开网址时留空，稿件须注明据运营方说明。采集回答不会自动变成事实库。</p>
        <el-input v-model="factsText" type="textarea" :rows="6" :disabled="busy" data-testid="research-facts" placeholder="例如：官网明确标注运营公司全称 | https://客户官网/关于我们" />
        <div class="row"><el-checkbox v-model="confirmed" :disabled="busy">我已核对这些资料，允许据此起草</el-checkbox><el-button :disabled="busy || !confirmed || !factsText.trim()" @click="saveFacts">保存事实资料</el-button></div>
        <p class="hint">资料修改后请先保存；新的事实或选题会生成独立版本。AI 稿件仍需要审稿。</p>
      </el-card>
      <el-card shadow="never">
        <h2>4 · 生成文章或帖子，进入现有发布流程</h2>
        <div class="row"><el-select v-model="briefId" aria-label="内容选题"><el-option v-for="brief in cycle.analysis.briefs" :key="brief.id" :value="brief.id" :label="brief.topic"/></el-select><el-select v-model="platform" aria-label="目标发布渠道"><el-option v-for="(label,key) in cycle.channels" :key="key" :value="key" :label="writingChannelLabel(cycle,key,label)" :disabled="!writingChannelEnabled(cycle,key)"/></el-select></div>
        <p v-if="selectedDestination && !destinationCanPrepare(selectedDestination)" class="hint">{{ destinationPublicationGate(selectedDestination).reason }}</p>
        <p class="hint">{{ selectedDestination ? `已选择接入平台：${selectedDestination.config.name}；稿件将使用该平台的内容规范与长度限制。` : '当前为通用渠道稿。可在上方来源投放目标中接入并选择具体平台。' }}</p>
        <div class="row"><el-button v-if="nativeAvailable" type="primary" :disabled="busy || !canDraft" @click="generate('draft','doubao')">豆包生成并存入内容库</el-button><el-button :disabled="busy || !canDraft" @click="generate('draft','api')">AI API 生成并存入内容库</el-button></div>
        <p v-if="!canDraft && (!selectedDestination || destinationCanPrepare(selectedDestination))" class="hint">请先保存核对后的事实资料，并保持输入与已保存内容一致。</p>
        <article v-for="draft in cycle.drafts" :key="draft.article_id" class="draft" data-testid="research-draft">
          <h3>{{ draft.title }}</h3><p>{{ cycle.channels[draft.platform] }} · {{ status[draft.status] || draft.status }} · 原始问题 {{ draft.provenance.brief.case_ids.join('、') }}</p>
          <p>{{ draft.summary }}</p>
          <div class="row"><el-button @click="edit(draft)">审稿 / 编辑</el-button><el-button v-if="destinationSupportsFill(draftDestination(draft))" :disabled="busy || !nativeAvailable || draft.status !== 'ready' || !destinationCanPrepare(draftDestination(draft))" @click="fillDestination(draft)">打开目标平台并填写</el-button><el-button v-if="draft.provenance.destination" :disabled="busy || !['ready','published'].includes(draft.status)" @click="openPublication(draft)">登记站内发布链接</el-button><el-button v-if="legacyChannel(draft) && legacyChannel(draft) !== 'owned_web'" :disabled="draft.status !== 'ready' || Boolean(draft.provenance.destination) && !destinationCanPrepare(draftDestination(draft))" @click="publish(draft)">进入现有发布中心</el-button><el-button @click="downloadArticle(draft)">导出稿件</el-button></div>
          <p v-if="draft.provenance.destination && !destinationSupportsFill(draftDestination(draft))" class="hint">该目标当前支持登录与导出，辅助填写尚未接入。可导出稿件后在平台编辑。</p>
          <p v-if="draft.provenance.destination" class="hint">来源匹配依据：{{ draft.provenance.distribution_basis?.source_ids.join('、') || '手动选择，当前选题未匹配来源网址' }}。填写可能触发网站自动保存；发布状态以实际结果为准。已有发布中心需另外绑定对应账号。</p>
          <p v-if="draft.platform === 'owned_web'" class="hint">官网稿可导出交给网站编辑发布。</p>
          <p v-for="job in draft.jobs" :key="job.id" class="hint">{{ job.demo ? '演示任务' : '发布任务' }} #{{ job.id }} · {{ job.platform }} · {{ jobStatus[job.status] || job.status }} <a v-if="safeUrl(job.result_url)" :href="safeUrl(job.result_url)" target="_blank" rel="noopener noreferrer">查看发布链接</a></p>
          <el-collapse><el-collapse-item title="预览草稿" :name="draft.article_id"><pre>{{ draft.content }}</pre><p class="hint">使用事实：{{ draft.provenance.used_fact_ids.join('、') }}；生成来源：{{ draft.provenance.method }}。文章内容和来源仍需人工核对。</p></el-collapse-item></el-collapse>
        </article>
        <el-empty v-if="!cycle.drafts.length" description="尚未生成平台稿"/>
      </el-card>
      <el-card shadow="never">
        <h2>5 · 发布记录与同题复测</h2>
        <GeoPublicationReview v-if="cycle" :cycle="cycle" :busy="busy" @updated="applyCycle"/>
        <p>发布器确认成功：{{ cycle.publications.length }} 条；用户登记：{{ cycle.reported_publications?.length || 0 }} 条（尚未独立核验）。演示和失败任务不计成功。</p>
        <p v-for="item in cycle.reported_publications" :key="item.id" class="hint"><el-tag type="info">用户登记，未核验</el-tag> {{ date(item.published_at) }} · <a :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">查看登记链接</a></p>
        <p class="hint">复测使用原计划、原界面和原模型选项，新开一轮采集。发布不代表已被索引；条件相同也只能观察变化，不能证明因果。</p>
        <el-button v-if="nativeAvailable" :disabled="busy" @click="retest">按原计划开始新一轮复测</el-button>
        <div class="row"><el-select v-model="retestReportId" aria-label="关联复测报告" placeholder="或选择本品牌的其他报告"><el-option v-for="report in matchingRetests" :key="report.id" :value="report.id" :label="`${report.name} · ${date(report.updated_at)}`"/></el-select><el-button :disabled="!retestReportId || busy" @click="attachRetest">关联并比较</el-button></div>
        <article v-for="report in cycle.retests" :key="report.run_id" class="draft">
          <h3>复测 {{ date(report.updated_at) }}</h3><p>{{ report.comparison.conditions_match ? '计划、界面及模型标签匹配，按共同有效题观察' : `条件不匹配：${report.comparison.reasons.join('；')}` }}</p>
          <el-tag>{{ feedbackStages[report.comparison.feedback_stage] || '采集观察' }}</el-tag><p class="hint">{{ report.comparison.publication_context }} 本次样本只能描述变化，不能独立证明有效、无效或因果关系。</p>
          <el-table :data="report.comparison.cohorts"><el-table-column label="类别"><template #default="{row}">{{ category[row.category] }}</template></el-table-column><el-table-column prop="paired_answers" label="共同有效题"/><el-table-column label="之前提及"><template #default="{row}">{{ rate(row.before_brand_rate) }}</template></el-table-column><el-table-column label="复测提及"><template #default="{row}">{{ rate(row.after_brand_rate) }}</template></el-table-column></el-table>
          <p class="hint">官网正文引用：<span v-for="row in report.comparison.cohorts" :key="row.category">{{ category[row.category] }} {{ rate(row.official_citation_before) }} → {{ rate(row.official_citation_after) }}（共同可判断 {{ row.official_citation_paired }} 题）；</span></p>
          <p class="hint">{{ report.comparison.note }}</p><p v-for="link in report.comparison.publication_citations" :key="link.url" class="hint">{{ link.url }} · {{ link.observed_in_citations === true ? '在本次正文引用中观察到' : '暂未观察到引用，是否被索引未知' }}</p>
        </article>
      </el-card>
    </template>
    <el-dialog v-model="publicationVisible" title="登记已在平台提交的公开文章" width="620px" :close-on-click-modal="false">
      <p class="hint">请先在平台确认提交结果。这里保留你的发布声明和时间，不会把它伪装成发布器已核验成功。</p>
      <el-form label-position="top"><el-form-item label="公开文章链接"><el-input v-model="publication.url" aria-label="公开文章链接" placeholder="完整 HTTPS 文章网址"/></el-form-item><el-form-item label="平台显示的发布时间"><el-input v-model="publication.published_at" type="datetime-local" aria-label="平台发布时间"/></el-form-item><el-form-item label="备注，可留空"><el-input v-model="publication.note" maxlength="500"/></el-form-item><el-checkbox v-model="publication.confirmed">我已在平台核对这篇文章的链接和发布时间</el-checkbox></el-form>
      <template #footer><el-button @click="publicationVisible=false">取消</el-button><el-button type="primary" :loading="working" :disabled="!publication.confirmed || !publication.url" @click="savePublication">保存发布声明</el-button></template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { geoContentApi } from '@/api/geoContent'
import { geoTestApi } from '@/api/geoTest'
import GeoDestinations from '@/components/GeoDestinations.vue'
import DoubaoPreparation from '@/components/DoubaoPreparation.vue'
import GeoPublicationReview from '@/components/GeoPublicationReview.vue'
import { destinationContinuationText, destinationPreparationText, destinationSupportsFill, isDestinationTask } from '@/utils/geoDestinationSession'
import { createWritingPreferences } from '@/utils/geoWritingOptions'
import { destinationEnabled, destinationCanPrepare, destinationPublicationGate, writingChannelEnabled, writingChannelLabel, inactiveDestinationMessage } from '@/utils/geoDestinationAvailability'

const route = useRoute(), router = useRouter()
const reports = ref([]), cycles = ref([]), cycle = ref(null), cycleId = ref(''), reportId = ref(''), retestReportId = ref('')
const loading = ref(false), working = ref(false), nativeAvailable = ref(false), task = ref({status:'idle'})
const clientMode = ref('consumer'), model = ref(''), factsText = ref(''), confirmed = ref(false), briefId = ref(''), platform = ref('zhihu')
const writingPreferences = createWritingPreferences({getItem:key=>window.localStorage.getItem(key),setItem:(key,value)=>window.localStorage.setItem(key,value)})
const applyWritingOptions = value => {
  const choice = writingPreferences.get(cycleId.value, value)
  clientMode.value = choice.client_mode; model.value = choice.model_label
}
const rememberWritingOptions = () => writingPreferences.set(cycleId.value,{client_mode:clientMode.value,model_label:model.value})
const selectWritingMode = value => {
  if (!['consumer','work'].includes(value)) return
  if (value !== clientMode.value) model.value = ''
  clientMode.value = value; rememberWritingOptions()
}
const selectWritingModel = value => {model.value = typeof value === 'string' ? value : '';rememberWritingOptions()}
const publicationVisible = ref(false), publication = ref({url:'',published_at:'',confirmed:false,note:''})
const nativeBusy = computed(() => task.value.status === 'running'), busy = computed(() => working.value || nativeBusy.value)
const writingContextPending = computed(() => Boolean(cycleId.value && cycle.value?.id !== cycleId.value))
const loginScope = ref(null)
let loginRequest = 0
const scopedLoginMessage = (current, scope, currentCycleId, isWorking = false) => {
  if (isWorking || !scope?.taskId || scope.cycleId !== currentCycleId || current?.id !== scope.taskId || current.kind !== 'login') return ''
  if (current.status === 'running') return '请在豆包专用窗口正常完成登录或安全验证，随后关闭这个单独登录窗口，返回软件核对状态；关闭不证明认证完成。'
  if (current.status !== 'completed') return '本次登录操作已结束，登录与验证状态仍需核对。原任务记录已保留，请先检查原窗口。'
  const result = current.result || {}, observed = result.login_observation || {}
  const mode = value => ['consumer','work'].includes(value) ? value : null
  const label = value => value === 'consumer' ? '豆包对话' : value === 'work' ? '豆包工作' : '未知'
  const selected = mode(result.requested_client_mode), actual = mode(observed.client_mode)
  const login = observed.login_required === true ? '仍需登录' : observed.login_required === false ? '未见登录提示，尚未核验' : '登录状态未知'
  const challenge = observed.challenge === true ? '仍需安全验证' : observed.challenge === false ? '未见安全验证' : '验证状态未知'
  return `${selected && actual && selected !== actual ? '所选 / 实际界面不一致。' : '本次登录窗口已结束。'}所选：${label(selected)}；最后实际：${label(actual)}；${login}；${challenge}。关闭窗口不证明认证完成，会话保存仍需核对；再次采集前会检查。`
}
const category = { discovery:'行业自然发现', brand_identity:'品牌核验', website_guided:'官网引导核验' }
const status = { draft:'待审草稿', ready:'就绪', publishing:'发布中', published:'已发布' }
const jobStatus = { success:'成功', failed:'失败', queued:'待执行', scheduled:'已计划', processing:'执行中', need_action:'待人工处理' }
const factsValue = () => factsText.value.split('\n').filter(line => line.trim()).map(line => {
  const [text,...url] = line.split('|')
  return {text:text.trim(),source_url:url.join('|').trim(),confirmed:confirmed.value}
})
const factsStoredText = facts => facts.map(fact => `${fact.text} | ${fact.source_url || ''}`).join('\n')
const canDraft = computed(() => Boolean(cycle.value?.facts.length && confirmed.value && factsStoredText(factsValue()) === factsStoredText(cycle.value.facts) && briefId.value && writingChannelEnabled(cycle.value,platform.value)))
const selectedDestination = computed(() => cycle.value?.destinations.find(item => item.key === platform.value))
const journeyProjectId = computed(() => {const value=Number(route.query.journey_project);return Number.isSafeInteger(value)&&value>0?value:null})
const returnToJourney = () => router.push({path:'/journey',query:{journey_project:String(journeyProjectId.value)}})
const matchingRetests = computed(() => reports.value.filter(report => report.name === cycle.value?.profile.name && report.run_id !== cycle.value?.baseline.run_id))
const currentTaskMessage = computed(() => scopedLoginMessage(task.value,loginScope.value,cycleId.value,working.value) || (task.value.cycle_id === cycleId.value ? (isDestinationTask(task.value) ? '平台任务的当前步骤与继续操作见下方对应平台准备卡。' : task.value.message || (nativeBusy.value ? '当前内容任务执行中，验证请在专用窗口完成。' : '本轮任务记录已保存。')) : (nativeBusy.value ? '另一个桌面任务正在执行，请完成后再开始写作。' : '平台登录、填写、写作与曝光采集分开记录。历史失败详情保留在任务记录。')))
const feedbackStages = {collection_only:'尚无发布证据',conditions_mismatch:'复测条件不匹配',retest_precedes_publication:'复测早于发布时间',publication_time_unknown:'发布时间待核对',post_publication_observation:'发布后的采集观察'}
const date = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Hong_Kong',hour12:false}) : '时间未知'
const rate = value => typeof value === 'number' ? `${Math.round(value*100)}%` : '未知'
const safeUrl = value => { try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null } catch { return null } }
let timer, handledTask, cycleEpoch = 0, cycleLoadRequest = 0, reloadRequest = 0, pollRequest = 0
const cycleScope = () => ({id:cycleId.value,epoch:cycleEpoch})
const sameCycleScope = scope => scope.id === cycleId.value && scope.epoch === cycleEpoch
const applyCycle = (value, {select=false,scope=null}={}) => {
  if (!value?.id || (scope && !sameCycleScope(scope)) || (!select && value.id !== cycleId.value)) return false
  cycle.value = value; if (select) cycleId.value = value.id
  applyWritingOptions(value)
  factsText.value = factsStoredText(value.facts); confirmed.value = value.facts.length > 0
  if (!value.analysis.briefs.some(brief => brief.id === briefId.value)) briefId.value = value.analysis.briefs[0]?.id || ''
  if (!value.channels[platform.value]) platform.value = 'zhihu'
  return true
}
const loadCycle = async () => {
  const scope = cycleScope(), request = ++cycleLoadRequest
  if (!scope.id) return
  const value = (await geoContentApi.get(scope.id)).data
  if (request === cycleLoadRequest) applyCycle(value,{scope})
}
const reload = async () => {
  const scope = cycleScope(), request = ++reloadRequest
  loading.value = true
  try { const [r,c] = await Promise.all([geoTestApi.listReports(),geoContentApi.list()]); if(request!==reloadRequest)return;reports.value = r.data; cycles.value = c.data; if(sameCycleScope(scope))await loadCycle() }
  finally { if(request===reloadRequest)loading.value = false }
}
const create = async () => {
  const scope = cycleScope()
  working.value = true
  try { const value=(await geoContentApi.create({report_id:reportId.value})).data;if(!sameCycleScope(scope))return;applyCycle(value,{select:true});const selected=cycleScope(),items=(await geoContentApi.list()).data;if(sameCycleScope(selected)){cycles.value=items;router.replace({path:'/geo-content',query:{cycle:cycleId.value}})} }
  finally { working.value = false }
}
const saveFacts = async () => {
  const scope = cycleScope()
  working.value = true
  try { if(applyCycle((await geoContentApi.facts(scope.id,{facts:factsValue(),revision:cycle.value.revision})).data,{scope}))ElMessage.success('事实资料已保存') }
  finally { working.value = false }
}
const reuseFacts = () => {factsText.value=factsStoredText(cycle.value.fact_candidates.facts);confirmed.value=false;ElMessage.info('已带入候选资料，请重新核对后勾选确认')}
const openPublication = draft => {
  const now=new Date();const local=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,16)
  publication.value={article_id:draft.article_id,target_id:draft.provenance.destination.id,url:'',published_at:local,confirmed:false,note:''};publicationVisible.value=true
}
const savePublication = async () => {
  const scope = cycleScope()
  working.value=true
  try {const timestamp=new Date(publication.value.published_at);if(!Number.isFinite(timestamp.getTime())) throw new Error('请填写平台实际发布时间');if(!applyCycle((await geoContentApi.publication(scope.id,{...publication.value,published_at:timestamp.toISOString()})).data,{scope}))return;publicationVisible.value=false;ElMessage.success('发布声明已保存，证据级别为用户登记')}
  catch(error) {if(!error.response) ElMessage.error(error.message)}
  finally {working.value=false}
}
const options = () => ({client_mode:clientMode.value,model_label:model.value.trim()})
const generate = async (kind, provider) => {
  const scope = cycleScope()
  working.value = true
  try {
    if (provider === 'doubao') {
      const result = await window.pywebview.api.generate_research(scope.id,kind,briefId.value,platform.value,options())
      if (result.error) throw new Error(result.error)
      if (sameCycleScope(scope)) task.value = result
    } else {
      const result = (await geoContentApi.generate(scope.id,{kind,brief_id:briefId.value,platform:platform.value})).data
      if(!applyCycle(result.cycle,{scope}))return
      const items=(await geoContentApi.list()).data;if(!sameCycleScope(scope))return;cycles.value=items
      ElMessage.success(kind === 'draft' ? '草稿已存入内容库，请审稿' : '选题已保存')
    }
  } catch (error) { if (!error.response) ElMessage.error(error.message || '内容任务未完成') }
  finally { working.value = false }
}
const login = async () => {
  if(cycleId.value && cycle.value?.id!==cycleId.value)return
  const currentCycleId = cycleId.value, request = ++loginRequest
  loginScope.value = null; working.value = true
  try {
    const result = await window.pywebview.api.open_login(options())
    if (result.error) throw new Error(result.error)
    if (request === loginRequest && currentCycleId === cycleId.value) {task.value = result;loginScope.value = {taskId:result.id,cycleId:currentCycleId}}
  } catch(error) { ElMessage.error(error.message || '登录窗口未打开') }
  finally { working.value = false }
}
const stop = () => window.pywebview.api.stop_task()
const selectDestination = key => {if(!writingChannelEnabled(cycle.value,key))return;platform.value=key;ElMessage.success('已选择目标平台，请在下方生成平台稿')}
const draftDestination = draft => cycle.value?.destinations.find(item=>item.id===draft.provenance.destination?.id)
const openDestination = async ({target_id,article_id}) => {
  const candidate=cycle.value?.destinations.find(item=>item.id===target_id)
  if(!destinationCanPrepare(candidate)){ElMessage.info(destinationPublicationGate(candidate).reason);return}
  const scope = cycleScope()
  try {const result=await window.pywebview.api.open_destination(scope.id,target_id,article_id || null);if(result.error) throw new Error(result.error);if(sameCycleScope(scope))task.value=result}
  catch(error) {ElMessage.error(error.message || '平台窗口未打开')}
}
const fillDestination = async draft => {
  const scope=cycleScope()
  const target=draftDestination(draft)
  if(!destinationCanPrepare(target)){ElMessage.info(destinationPublicationGate(target).reason);return}
  if(!destinationSupportsFill(target)){ElMessage.info('该目标当前支持登录与导出，辅助填写尚未接入');return}
  try {await ElMessageBox.confirm(`${destinationPreparationText(target)} 将填入已审稿的标题和正文，其他已有内容会保留并暂停，网站可能自动保存。${destinationContinuationText(window.pywebview?.api)} 最后提交由你确认。`,'打开平台填写',{confirmButtonText:'打开并填写',cancelButtonText:'取消'});if(sameCycleScope(scope))await openDestination({target_id:draft.provenance.destination.id,article_id:draft.article_id})}
  catch(error) {if(error instanceof Error) ElMessage.error(error.message)}
}
const retest = async () => {
  const scope = cycleScope()
  try {
    await ElMessageBox.confirm('将按基线问题开启新一轮豆包采集。发布后的索引时滞未知，采集结果只作观察。','开始同题复测',{type:'info'})
    if(!sameCycleScope(scope))return
    const result = await window.pywebview.api.start_research_retest(scope.id)
    if (result.error) throw new Error(result.error)
    if(sameCycleScope(scope))task.value = result
  } catch(error) { if (error instanceof Error) ElMessage.error(error.message) }
}
const attachRetest = async () => { const scope=cycleScope();applyCycle((await geoContentApi.retest(scope.id,retestReportId.value)).data,{scope}) }
const edit = draft => router.push({path:'/content-create',query:{articleId:draft.article_id}})
const legacyChannel = draft => draft.provenance.destination ? draft.provenance.destination.channel : draft.platform
const publish = draft => {if(draft.provenance.destination && !destinationCanPrepare(draftDestination(draft))){ElMessage.info(destinationPublicationGate(draftDestination(draft)).reason);return}router.push({path:'/publish-center',query:{article_id:draft.article_id,platform:legacyChannel(draft)}})}
const downloadFile = (data,name,type) => { const url = URL.createObjectURL(new Blob([data],{type})); const a=document.createElement('a'); a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000) }
const download = (data,name) => downloadFile(JSON.stringify(data,null,2),name,'application/json')
const downloadArticle = draft => downloadFile(`# ${draft.title}\n\n${draft.content}`,`article-${draft.article_id}.md`,'text/markdown')
const poll = async () => {
  if (!nativeAvailable.value) return
  const request=++pollRequest,previousTaskId=task.value.id
  try {
    const current = await window.pywebview.api.get_task()
    if(request!==pollRequest || task.value.id!==previousTaskId)return
    task.value = current
    if (current.status !== 'running' && current.id && handledTask !== current.id) {
      handledTask = current.id
      if (current.cycle_id === cycleId.value) { await reload(); if (current.status === 'failed') ElMessage.error(current.message || '任务未完成') }
    }
  } catch { /* Native shutdown leaves the last durable task untouched. */ }
}
watch(cycleId, () => { loginRequest++; loginScope.value = null },{flush:'sync'})
watch(()=>cycleId.value, () => {
  cycleEpoch++; cycleLoadRequest++
  publicationVisible.value=false
  if(cycle.value?.id!==cycleId.value){cycle.value=null;factsText.value='';confirmed.value=false;briefId.value='';platform.value='zhihu'}
  applyWritingOptions(cycle.value)
},{flush:'sync'})
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
.journey-return{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 20px;border:1px solid var(--geo-border,#dce6e5);border-radius:14px;background:#fff;font-size:13px;color:var(--geo-muted,#52676d)}.fact-reuse{display:flex;align-items:center;gap:16px;margin-bottom:16px;padding:12px;background:#f3f8f7;border-radius:10px;font-size:13px}
.content-loop{max-width:1200px;margin:auto;display:grid;gap:20px}.row,header{display:flex;align-items:center;gap:12px;flex-wrap:wrap}header{justify-content:space-between}h1{margin:0;font-size:28px}h2{font-size:20px;margin:0 0 16px}h3{font-size:17px}.hint,header p{color:#59696e;line-height:1.8}.row{margin:12px 0}.row .el-select{width:300px}.row .el-input{width:210px}.observations,.draft{background:#f4f8f7;padding:16px;margin-top:18px;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;font-family:inherit}li{margin:8px 0}a{color:#0a756e}.el-select{max-width:100%}@media(max-width:600px){.row .el-select,.row .el-input{width:100%}}
</style>
