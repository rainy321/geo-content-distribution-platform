<template>
  <main class="search-tests">
    <header>
      <div><h1>AI 搜索测试</h1><p>按客户业务生成问题，查看 AI 回答中实际出现的品牌与来源。</p></div>
      <el-button :disabled="nativeBusy" @click="importInput?.click()">导入计划或报告</el-button>
      <input ref="importInput" type="file" accept=".json,application/json" hidden @change="importRecord" />
    </header>
    <el-alert v-if="workspaceSaveError" type="error" :title="workspaceSaveError" :closable="false" />

    <el-card v-if="isDesktop" shadow="never">
      <h2>软件自动采集</h2><p class="context">先检查专用会话，再采集回答和来源；需要登录或安全验证时在软件提示的窗口完成，原请求继续等待。实际工作网页与普通对话结果分开保留。</p>
      <el-alert v-if="(collectionPath==='automatic' || sourceRequested) && !automaticReady" type="warning" :closable="false" :title="automaticCollectionUnavailable"/>
      <DoubaoPreparation/>
      <div class="form-grid">
        <el-form-item :label="collectionPath==='automatic' || sourceRequested ? '专用会话（实际界面由软件检查）' : '指定网页模式'"><el-select v-model="desktopClientMode" :disabled="nativeBusy">
          <el-option label="普通对话专用会话" value="consumer" /><el-option label="工作专用会话" value="work" />
        </el-select></el-form-item>
        <el-form-item label="模型选项（可选）"><el-input v-model="desktopModel" :disabled="nativeBusy" placeholder="留空使用当前模型" /></el-form-item>
      </div>
      <el-button :disabled="nativeBusy" @click="startNative('login')">登录豆包</el-button>
      <el-button :disabled="nativeBusy || sourceRequested || (!project && !report) || collectionPath==='automatic' && !automaticReady" @click="startNative('plan',collectionPath==='automatic')">用豆包生成问题</el-button>
      <el-button type="primary" :disabled="nativeBusy || !displayPlan || !displayPlanMatchesProject || (collectionPath==='automatic' || sourceRequested) && (!automaticReady || !(sourceRequested ? sourceConfirmed : autoPlanConfirmed))" @click="startNative('run',collectionPath==='automatic' || sourceRequested)">{{ collectionPath==='automatic' || sourceRequested ? '开始软件自动采集' : '按指定模式开始测试' }}</el-button>
      <el-button v-if="nativeBusy" type="warning" @click="stopNative">停止任务</el-button>
      <DoubaoTaskRecovery v-if="taskRecovery" :recovery="taskRecovery" :busy="nativeBusy" :native-available="nativeReady" @login="startNative('login')"/>
      <p v-if="nativeMessage" class="context">{{ nativeMessage }}</p>
      <p class="context">任务、计划和客户资料会自动保存，重开后恢复。采集中的验证完成后保持窗口打开，继续同一请求；请勿反复开始新任务。</p>
      <p v-if="!project && report" class="context">当前出题客户：{{ report.profile.name }}。也可在下方选择其他品牌项目。</p>
      <details v-if="!sourceRequested" class="context"><summary>高级采集方式</summary><el-radio-group v-model="collectionPath" :disabled="nativeBusy" aria-label="网页采集方式"><el-radio-button value="automatic">软件自动采集（当前可用网页界面）</el-radio-button><el-radio-button value="explicit">指定网页模式</el-radio-button></el-radio-group></details>
    </el-card>

    <el-card v-if="isDesktop" shadow="never" data-testid="desktop-history" :data-history-count="nativeHistory.length">
      <div class="card-heading history-heading">
        <div><h2>任务记录</h2><p class="context">{{ historyExpanded ? '全部记录' : '最近 5 条' }} · {{ nativeHistory.length }} 条</p></div>
        <el-button v-if="nativeHistory.length > 5" text :aria-expanded="historyExpanded" @click="toggleHistory">{{ historyExpanded ? '收起记录' : '查看更多记录' }}</el-button>
      </div>
      <el-table :data="visibleNativeHistory" empty-text="暂无任务记录">
        <el-table-column label="时间" min-width="155"><template #default="{ row }">{{ reportDate(row.created_at) }}</template></el-table-column>
        <el-table-column prop="client_name" label="客户" min-width="140" />
        <el-table-column label="任务" width="100"><template #default="{ row }">{{ taskNames[row.kind] || row.kind }}</template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{ row }">{{ nativeStatusLabel(row) }}</template></el-table-column>
        <el-table-column label="详情" min-width="220"><template #default="{ row }">{{ row.message || '记录已保存' }}</template></el-table-column>
        <el-table-column width="80"><template #default="{ row }"><el-button link :disabled="nativeBusy" @click="showNativeTask(row.id)">查看</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="historyExpanded && nativeHistory.length > historyPageSize" v-model:current-page="historyPage" class="history-pagination"
        :page-size="historyPageSize" :total="nativeHistory.length" layout="prev, pager, next" small />
    </el-card>

    <el-card shadow="never">
      <h2>已保存报告</h2>
      <el-select v-model="selectedReportId" placeholder="选择本机已采集的报告" :loading="reportLoading" :disabled="nativeBusy" @change="loadSavedReport">
        <el-option v-for="item in savedReports" :key="item.id" :value="item.id"
          :label="`${item.name} · ${statusNames[item.status] || item.status} · ${item.case_count} 题 · ${reportDate(item.updated_at)}`" />
      </el-select>
      <p v-if="!savedReports.length" class="context">暂无已保存报告。完成本机采集后刷新页面，或导入报告文件。</p>
    </el-card>

    <el-card shadow="never">
      <el-form label-position="top" @submit.prevent="generate">
        <div class="form-grid">
          <el-form-item label="品牌项目">
            <el-select v-model="projectId" placeholder="选择已建项目" :loading="loading" :disabled="nativeBusy || generating">
              <el-option v-for="item in projects" :key="item.id" :label="`${entityLabel(item)} · ${item.name} · ${item.website || '未填网址'}`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item :label="project?.entity_type==='company' ? '关联公司（可选）' : '运营公司（可选）'"><el-input v-model="company" data-testid="company-input" :disabled="!project || nativeBusy || generating" maxlength="300" placeholder="公司提及与产品提及分别统计" /></el-form-item>
          <el-form-item label="目标客户（可选）"><el-input v-model="audience" :disabled="!project || nativeBusy || generating" maxlength="300" placeholder="例如：企业采购团队" /></el-form-item>
          <el-form-item label="问题数量"><el-input-number v-model="caseCount" :disabled="nativeBusy || generating" :min="3" :max="12" /></el-form-item>
        </div>
        <p v-if="project" class="context">测试对象：{{ entityLabel(project) }} · {{ project.entity_type==='company' ? '公司官网' : '产品介绍或服务网址' }}：{{ project.website || '待补充' }}。切换对象后需重新生成并检查问题。</p>
        <p v-if="project" class="context">{{ project.industry || '请在品牌项目补充行业' }} · {{ project.description || '可在品牌项目补充业务说明' }}</p>
        <el-collapse v-if="project"><el-collapse-item title="当前对象别名与额外官方页面" name="profile"><div class="profile-grid"><el-form-item label="当前对象别名（每行一个）"><el-input v-model="aliasesText" type="textarea" :rows="3" :disabled="nativeBusy || generating" placeholder="仅当前公司或产品的名称，不填其他对象"/></el-form-item><el-form-item label="额外官方页面 / 服务网址（每行一个）"><el-input v-model="officialUrlsText" type="textarea" :rows="3" :disabled="nativeBusy || generating" placeholder="具体产品介绍、官方文档或服务入口"/></el-form-item></div><p class="context">产品按登记的具体网址路径判断官方来源，不把整个公司站点都算作该产品来源。资料在保存或生成计划时写入项目档案。</p></el-collapse-item></el-collapse>
        <el-button type="primary" :loading="generating" :disabled="!project || nativeBusy" @click="generate">AI 生成测试计划</el-button>
        <el-button :disabled="!project || nativeBusy || generating" @click="saveProfile">保存测试资料</el-button>
        <el-button :disabled="!project" @click="downloadProfile">导出客户资料</el-button>
        <router-link to="/projects" class="link">管理品牌项目</router-link>
      </el-form>
    </el-card>

    <el-alert class="notice" type="info" :closable="false" show-icon
      title="行业问题不带品牌，用于观察自然提及；点名品牌和提供官网的核验问题分别统计。提及可能是否定回答或资料不足。" />
    <details v-if="!sourceRequested" class="context"><summary>补充验证（可选）</summary><p v-if="project && externalProfilePending" class="context">补充记录使用已保存的测试资料；请先点击“保存测试资料”。</p><GeoExternalBaseline v-if="project" :project="project" :plan="planMatchesProject ? plan : null" :busy="nativeBusy || generating" :blocked="externalProfilePending" @busy="setExternalBaselineBusy" @created="openExternalCycle"/></details>
    <el-alert v-if="sourceLoading || sourceLoadError" :title="sourceLoadError || '正在只读核对原任务单的固定问题…'" :type="sourceLoadError ? 'error' : 'info'" :closable="false"/>

    <el-card v-if="displayPlan" shadow="never">
      <div class="card-heading"><h2>{{ displayPlan.profile.name }} · 测试计划</h2><el-button @click="download(displayPlan, 'plan.json')">下载计划</el-button></div>
      <p class="context">{{ sourceRequested ? '原任务单的固定问题，只读沿用；另建独立网页基线，不附加到外部观察，也不直接计算同比。' : '运行前请检查问题是否符合客户业务。复测使用同一份计划。' }}</p>
      <p v-if="sourcePreview" class="context">原任务单：{{ sourcePreview.cycleId }} · 原题摘要：{{ displayPlan.plan_sha256 }}</p>
      <p class="context">测试对象：{{ entityLabel(displayPlan.profile) }} · {{ displayPlan.profile.website }}。</p><p v-if="!displayPlanMatchesProject" class="context">这份计划与当前选择的对象不一致，只供查看；请为当前对象重新生成问题。</p>
      <p v-if="reportEntityContext(displayPlan).legacy" class="context">{{ reportEntityContext(displayPlan).note }}</p>
      <el-table :data="displayPlan.cases">
        <el-table-column label="类别" width="130"><template #default="{ row }">{{ categoryNames[row.category] }}</template></el-table-column>
        <el-table-column prop="question" label="测试问题" min-width="260" />
        <el-table-column prop="intent" label="测试目的" min-width="180" />
      </el-table>
      <el-checkbox v-if="sourceRequested" v-model="sourceConfirmed" :disabled="nativeBusy">已核对固定原题，另建独立软件自动测试</el-checkbox><el-checkbox v-else-if="collectionPath==='automatic'" v-model="autoPlanConfirmed" :disabled="nativeBusy">已核对这些问题，可以开始软件自动采集</el-checkbox>
    </el-card>

    <el-card v-if="report" ref="reportCard" shadow="never">
      <div class="card-heading"><h2>{{ report.profile.name }} · 采集报告</h2><el-tag>{{ statusNames[report.status] || report.status }}</el-tag><el-button type="primary" :disabled="nativeBusy" @click="useReport">用本报告制定内容任务</el-button></div>
      <p class="context">{{ actualCollectionMode(report) }}；仅代表本报告实际采到的网页回答，不代表另一界面或独立账号效果。</p>
      <p class="context">历史采集记录 · {{ reportDate(report.cases[0]?.collected_at || report.updated_at) }}。回答原文来自豆包，尚未逐项核实事实；“提及”只说明名称出现在回答里。</p>
      <p class="context">测试对象：{{ reportContext.label }} · {{ report.profile.name }}{{ reportContext.legacy ? '（旧报告，混合统计口径）' : '' }}。</p><p class="context">{{ reportContext.note }}</p>
      <el-alert v-if="report.cases?.some(item=>item.assessment?.answer_error==='private_account_context')" type="warning" :closable="false" show-icon title="部分回答声称使用账号资料或历史对话记录，已排除公开统计；原文保留，请用空白新对话另测。"/>
      <details class="context"><summary>本对象登记的官方页面 / 服务网址</summary><p v-for="url in reportContext.urls" :key="url"><a v-if="safeLink(url)" :href="safeLink(url)" target="_blank" rel="noopener noreferrer">{{ url }}</a><span v-else>{{ url }}</span></p></details>
      <p v-if="report.stop_reason" class="context">中断原因：{{ report.stop_reason }}</p>
      <details v-if="report.evaluation_projection" class="context"><summary>评估说明 · {{ report.evaluation_projection.stored_evaluation_version }} → {{ report.evaluation_projection.applied_evaluation_version }}</summary><p>{{ report.evaluation_projection.note }}</p></details>
      <div class="cohorts">
        <article v-for="category in categories" :key="category">
          <h3>{{ categoryNames[category] }}</h3>
          <strong>{{ rate(report.cohorts[category]?.brand_mention_rate) }}</strong><p>{{ reportContext.mentionLabel }}率</p>
          <small>完成 {{ report.cohorts[category]?.answers_evaluable || 0 }} / {{ report.cohorts[category]?.planned || 0 }} 题</small>
          <p class="context">官网搜索资料命中 {{ report.cohorts[category]?.official_source_hits || 0 }} / 可判断 {{ report.cohorts[category]?.official_source_evaluable || 0 }}</p>
          <p class="context">官网正文引用命中 {{ report.cohorts[category]?.official_citation_hits || 0 }} / 可判断 {{ report.cohorts[category]?.official_citation_evaluable || 0 }}</p>
        </article>
      </div>
      <el-collapse>
        <el-collapse-item v-for="item in report.cases" :key="item.id" :name="item.id" :title="`${categoryNames[item.category]} · ${item.question}`">
          <p>{{ statusNames[item.status] || item.status }} · {{ reportContext.mentionLabel }}：{{ booleanText(item.assessment.brand_mentioned) }}</p>
          <p v-if="item.assessment.answer_error==='private_account_context'" class="context">{{ item.assessment.evaluation_note }}</p>
          <p v-if="report.profile.entity_type==='product' && !reportContext.legacy && relatedCompanyMention(item)!==undefined" class="context">运营公司单独提及：{{ booleanText(relatedCompanyMention(item)) }}<span v-if="item.assessment.brand_mentioned===false && relatedCompanyMention(item)===true">；仅公司出现，未计入当前产品提及</span>。</p>
          <p v-if="item.client_mode" class="context">{{ item.client_mode === 'work' ? '豆包工作界面' : '豆包消费者对话界面' }} · {{ item.model_label || '模型标签未知' }} · {{ item.collected_at }}</p>
          <p v-if="item.failure_reason">{{ item.failure_reason }}</p>
          <el-collapse><el-collapse-item title="查看豆包原始回答（未逐项核验）" :name="`raw-${item.id}`"><pre>{{ item.response || '尚无完整回答' }}</pre></el-collapse-item></el-collapse>
          <div v-for="group in sourceGroups" :key="group.key">
            <h3>{{ group.title }}（{{ item[group.key]?.length ?? '未知' }}）</h3>
            <ul>
              <li v-for="(source, index) in item[group.key] || []" :key="index">
                <a v-if="safeLink(source.url)" :href="safeLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a>
                <span v-else>{{ source.title || '未显示标题' }} · 未暴露网址</span>
              </li>
            </ul>
          </div>
        </el-collapse-item>
      </el-collapse>
      <p class="context">单轮小样本与账户、模型和时间有关；提及不等于推荐。事实正确性仍需结合回答与来源核验。</p>
    </el-card>

    <el-card v-if="!isDesktop" shadow="never">
      <h2>在本机执行采集</h2>
      <p>下载计划后，本机专用浏览器会逐题采集豆包回答与来源；遇到登录或验证会停下。完成后导入 report.json 查看结果。</p>
      <pre>python -m services.geo_test_cli run --plan plan.json --run-dir runtime/geo-tests/run-001 --headed</pre>
      <el-collapse>
        <el-collapse-item title="尚未配置 AI API：使用已登录的豆包生成问题" name="doubao">
          <p>导出客户资料为 client-profile.json，在项目目录执行；计划生成与曝光测试各自保存证据。</p>
          <pre>python -m services.doubao_source_cli login
python -m services.geo_test_cli plan --profile client-profile.json --planner doubao-web --output plan.json --headed</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi } from '@/api/project'
import { geoTestApi } from '@/api/geoTest'
import { geoContentApi } from '@/api/geoContent'
import DoubaoPreparation from '@/components/DoubaoPreparation.vue'
import DoubaoTaskRecovery from '@/components/DoubaoTaskRecovery.vue'
import GeoExternalBaseline from '@/components/GeoExternalBaseline.vue'
import {isExternalCycle} from '@/utils/geoExternalBaseline'
import {updateFeedbackBusy} from '@/utils/geoFeedbackBusy'
import { entityLabel, entityProfileFields, profileLines, reportEntityContext, restoreEntityDraftFields, sameBrand } from '@/utils/geoJourney'
import { restoredSearchRecords, sameSearchProject, searchProjectSnapshot, searchTaskMatchesContext, searchTaskMatchesProject, searchTaskRecoverySource } from '@/utils/geoSearchRestore'
import { doubaoTaskRecovery } from '@/utils/geoTaskRecovery'
import {automaticBridgeReady,automaticCollectionOptions,automaticCollectionUnavailable,requireAutomaticBridge,automaticSourceRequest,automaticPlanFromExternalCycle,actualCollectionMode} from '@/utils/geoAutomaticCollection'

const projects = ref([]), projectId = ref(null), company = ref(''), audience = ref(''), aliasesText=ref(''), officialUrlsText=ref(''), caseCount = ref(4)
const loading = ref(false), generating = ref(false), plan = ref(null), report = ref(null), importInput = ref(null)
const route = useRoute(), savedReports = ref([]), selectedReportId = ref(''), reportLoading = ref(false), reportCard = ref(null)
const isDesktop = computed(() => route.query.desktop === '1' || Boolean(window.pywebview?.api))
const router = useRouter()
const useReport = async () => {
  const value = (await geoContentApi.create({ report: report.value,...(sameBrand(project.value,report.value?.profile)?{project_id:projectId.value}:{}) })).data
  router.push({path:'/geo-content',query:{cycle:value.id}})
}
const desktopClientMode = ref('consumer'), desktopModel = ref(''), nativeTask = ref({ status: 'idle' }), collectionPath=ref('automatic'), autoPlanConfirmed=ref(false)
const sourcePreview=ref(null),sourceConfirmed=ref(false),sourceLoading=ref(false),sourceLoadError=ref('')
const sourceRequested=computed(()=>route.query.source_cycle!==undefined)
const automaticReady=computed(()=>nativeReady.value&&automaticBridgeReady(window.pywebview?.api))
const nativeHistory = ref([]), nativeDetails = ref(null), restoringWorkspace = ref(false)
const historyExpanded = ref(false), historyPage = ref(1), historyPageSize = 20
const visibleNativeHistory = computed(() => {
  const start = historyExpanded.value ? (historyPage.value - 1) * historyPageSize : 0
  return nativeHistory.value.slice(start, start + (historyExpanded.value ? historyPageSize : 5))
})
const toggleHistory = () => { historyExpanded.value = !historyExpanded.value; historyPage.value = 1 }
watch(() => nativeHistory.value.length, total => {
  historyPage.value = Math.min(historyPage.value, Math.max(1, Math.ceil(total / historyPageSize)))
})
const workspaceSaveError = ref('')
const nativeStarting=ref(false),nativeReady=ref(Boolean(window.pywebview?.api))
const taskBinding=ref(null),recoveryRecord=ref(null),reportChoice=ref('automatic')
const taskNames = { login: '登录豆包', plan: '生成问题', run: '采集测试', research: '采集内容策划', draft: '生成平台稿' }
let workspaceSaveTimer = null, workspaceReady = false
const externalBaselineOwners=ref(new Set()),externalBaselineBusy=computed(()=>externalBaselineOwners.value.size>0)
const setExternalBaselineBusy=event=>{externalBaselineOwners.value=updateFeedbackBusy(externalBaselineOwners.value,event)}
const nativeBusy = computed(() => restoringWorkspace.value || nativeStarting.value || nativeTask.value.status === 'running' || externalBaselineBusy.value)
const externalProfilePending=computed(()=>!project.value||company.value!==String(project.value.company||'')||audience.value!==String(project.value.audience||'')
  ||JSON.stringify(profileLines(aliasesText.value))!==JSON.stringify(profileLines(project.value.aliases))
  ||JSON.stringify(profileLines(officialUrlsText.value))!==JSON.stringify(profileLines(project.value.official_urls)))
const openExternalCycle=value=>{if(!destroyed&&!externalProfilePending.value&&isExternalCycle(value)&&value.project_id===projectId.value&&sameBrand(value.profile,project.value))router.push({path:'/journey',query:{journey_project:String(projectId.value),cycle:value.id}})}
const taskRecovery=computed(()=>{
  if(taskMatchesCurrent(nativeTask.value)) {
    const current=doubaoTaskRecovery(searchTaskRecoverySource(nativeTask.value));if(current)return current
  }
  const prior=recoveryRecord.value
  return (sameSearchProject(prior?.project,project.value) || !project.value && prior?.project===null
    && prior.intent===recordIntent.value && sameBrand(prior.profile,prior.profile))?prior.recovery:null
})
const nativeMessage = computed(() => {
  if(taskRecovery.value?.taskId===nativeTask.value.id)return ''
  const current=nativeTask.value,foreign=current.id && !taskMatchesCurrent(current)
  const interrupted=current.status==='completed' && ['stopped','failed','need_action','needs_review'].includes(current.result?.report?.status)
  return `${foreign?'其他或未绑定对象的桌面任务：':current.restored_from_disk?'上次任务：':''}${interrupted?'采集已中断，完整原因及有效结果见原报告。':current.message || ({idle:'先生成并检查问题，再开始测试。',running:'任务执行中，请等待原任务。',completed:'任务已结束，请核对结果。',stopped:'任务已停止。',failed:'任务未完成。',needs_review:'上次任务未完成，记录已保留，请检查后再执行。'}[current.status] || '')}`
})
const nativeStatusLabel=row=>row.kind==='run' && row.status==='completed'
  ? statusNames[row.id===nativeTask.value.id?nativeTask.value.result?.report?.status:''] || '已结束，查看报告'
  : statusNames[row.status] || row.status
const recordIntent=ref(0)
let nativeTimer = null, handledNativeTask = null,searchEpoch=0,nativeRequest=0,reportRequest=0,detailRequest=0,restoreRequest=0,sourceRead=0,destroyed=false
const searchScope=()=>({epoch:searchEpoch,project:searchProjectSnapshot(project.value),sourceCycle:route.query.source_cycle})
const sameSearchScope=scope=>!destroyed && scope.epoch===searchEpoch && scope.sourceCycle===route.query.source_cycle && (scope.project?sameSearchProject(scope.project,project.value):!project.value)
const taskMatchesCurrent=task=>searchTaskMatchesContext(task,project.value,taskBinding.value,recordIntent.value)
const rememberRecovery=task=>{
  if(!taskMatchesCurrent(task))return
  const recovery=doubaoTaskRecovery(searchTaskRecoverySource(task))
  if(recovery)recoveryRecord.value={project:searchProjectSnapshot(project.value),profile:taskBinding.value?.profile,intent:recordIntent.value,recovery}
}
const nativeBridge = () => {
  if (!window.pywebview?.api) throw new Error('桌面采集服务尚未就绪，请稍后重试')
  return window.pywebview.api
}
const startNative = async (kind,automatic=false) => {
  if(nativeBusy.value || generating.value)return
  const scope=searchScope(),request=++nativeRequest,intent=recordIntent.value
  nativeStarting.value=true
  try {
    if(kind!=='login' && taskRecovery.value?.requiresConfirmation) {
      try {await ElMessageBox.confirm(`${taskRecovery.value.submissionText} 此操作会新建一轮，不是继续原请求。`,'重新开始前核对原会话',{confirmButtonText:'确认新开一轮',cancelButtonText:'保留原任务'})}catch{return}
      if(!sameSearchScope(scope))return
    }
    rememberRecovery(nativeTask.value)
    const api = nativeBridge()
    const isAutomatic=kind!=='login'&&(automatic||sourceRequested.value)
    if(isAutomatic)requireAutomaticBridge(api)
    if(sourceRequested.value&&(kind==='plan'||kind==='run'&&(!sourceConfirmed.value||!sourcePreview.value)))throw new Error('请先核对原任务单固定问题，再确认另建独立自动测试。')
    if(isAutomatic&&kind==='run'&&!sourceRequested.value&&!autoPlanConfirmed.value)throw new Error('请先预览并确认这份问题计划。')
    const options=isAutomatic?automaticCollectionOptions(desktopClientMode.value,desktopModel.value):{ client_mode: desktopClientMode.value, model_label: desktopModel.value.trim() }
    const runPlan=sourceRequested.value?sourcePreview.value?.plan:plan.value
    const source = kind==='plan' ? await persistProfile() : kind==='run'?runPlan?.profile:project.value || report.value?.profile || runPlan?.profile
    if(!sameSearchScope(scope) || request!==nativeRequest || intent!==recordIntent.value)return
    if(kind==='run'&&!displayPlanMatchesProject.value)throw new Error('测试计划与当前对象不一致，请重新生成问题')
    const result = kind === 'login' ? await api.open_login(options)
      : kind === 'plan' ? isAutomatic?await api.plan_automatic({ ...source,...entityProfileFields(source) }, caseCount.value, options):await api.plan_with_doubao({ ...source,...entityProfileFields(source) }, caseCount.value, options)
        : isAutomatic?await api.start_automatic_run(runPlan, options):await api.start_run(runPlan, options)
    if (result.error) throw new Error(result.error)
    if(!sameSearchScope(scope) || request!==nativeRequest || intent!==recordIntent.value)return
    nativeTask.value = { ...result, kind }
    if(kind!=='login'){recordIntent.value++;reportRequest++;detailRequest++;reportChoice.value='task';recoveryRecord.value=null}
    taskBinding.value={taskId:result.id,project:searchProjectSnapshot(project.value),profile:searchProjectSnapshot(source),intent:recordIntent.value};handledNativeTask=null
    if (kind === 'run') {report.value = null;selectedReportId.value='';sourceConfirmed.value=false;autoPlanConfirmed.value=false}
    const history=await api.list_tasks()
    if(sameSearchScope(scope) && request===nativeRequest)nativeHistory.value=history
  } catch (error) { ElMessage.error(error.response?.data?.msg || error.message || '任务无法启动') }
  finally {if(request===nativeRequest)nativeStarting.value=false}
}
const stopNative = async () => {
  try { await nativeBridge().stop_task() }
  catch (error) { ElMessage.error(error.message || '停止失败') }
}
const pollNative = async () => {
  if(restoringWorkspace.value || nativeStarting.value || !nativeTask.value.id || nativeTask.value.status!=='running' && handledNativeTask===nativeTask.value.id)return
  const scope=searchScope(),request=++nativeRequest,previousTaskId=nativeTask.value.id,intent=recordIntent.value
  const current=()=>sameSearchScope(scope) && request===nativeRequest && nativeTask.value.id===previousTaskId
  try {
    const task = await nativeBridge().get_task()
    if(!current())return
    nativeTask.value = task
    rememberRecovery(task)
    if (task.status !== 'running' && handledNativeTask !== task.id) {
      const belongs=taskMatchesCurrent(task)
      if(belongs && intent===recordIntent.value && reportChoice.value!=='explicit') {
        if(task.result?.plan)setRecord(task.result.plan)
        if(task.result?.report){setRecord(task.result.report);reportChoice.value='task'}
      }
      if (task.kind === 'run') {
        const reports=(await geoTestApi.listReports()).data
        if(!sameSearchScope(scope) || request!==nativeRequest || nativeTask.value.id!==task.id)return
        savedReports.value=reports
        const saved = savedReports.value.find(item => item.run_id === task.result?.report?.run_id)
        if (belongs && intent===recordIntent.value && reportChoice.value!=='explicit' && saved) selectedReportId.value = saved.id
      }
      const history=await nativeBridge().list_tasks()
      if(!sameSearchScope(scope) || request!==nativeRequest || nativeTask.value.id!==task.id)return
      nativeHistory.value=history;handledNativeTask=task.id
      if (belongs && task.status === 'failed' && !taskRecovery.value) ElMessage.error(task.message || '采集未完成')
    }
  } catch { /* Keep the last task state if the desktop window is closing. */ }
}
const showNativeTask = async id => {
  if(nativeBusy.value)return
  const scope=searchScope(),request=++detailRequest,intent=++recordIntent.value
  try {
    const task = await nativeBridge().get_task(id)
    if(!sameSearchScope(scope) || request!==detailRequest || intent!==recordIntent.value || task.id!==id)return
    nativeDetails.value = task
    reportChoice.value='explicit'
    if (task.result?.plan) setRecord(task.result.plan)
    if (task.result?.report) {setRecord(task.result.report);selectedReportId.value=savedReports.value.find(item=>item.run_id===task.result.report.run_id)?.id || ''}
    if (task.message) ElMessage.info(task.message)
  } catch (error) { ElMessage.error(error.message || '读取任务记录失败') }
}
const project = computed(() => projects.value.find(item => item.id === projectId.value))
const planMatchesProject=computed(()=>Boolean(plan.value&&(!project.value||sameBrand(plan.value.profile,project.value))))
const displayPlan=computed(()=>sourceRequested.value?sourcePreview.value?.plan||null:plan.value)
const displayPlanMatchesProject=computed(()=>Boolean(displayPlan.value&&(!project.value||sameBrand(displayPlan.value.profile,project.value))))
const reportContext=computed(()=>reportEntityContext(report.value))
const relatedCompanyMention=item=>Object.hasOwn(item.assessment,'related_company_mentioned')?item.assessment.related_company_mentioned:item.assessment.company_mentioned
const editableProfile=()=>({...project.value,...entityProfileFields({...project.value,company:company.value,audience:audience.value,aliases:aliasesText.value,official_urls:officialUrlsText.value})})
const persistProfile=async()=>{
  if(!project.value){if(!report.value?.profile)throw new Error('请先选择测试对象');return report.value.profile}
  const id=projectId.value,fields=entityProfileFields(editableProfile())
  const saved=(await projectApi.updateProject(id,fields)).data
  if(projectId.value!==id)throw new Error('测试对象已切换，请重新生成问题')
  projects.value=projects.value.map(item=>item.id===id?saved:item)
  return {...saved,...fields}
}
const saveProfile=async()=>{if(nativeBusy.value||generating.value)return;generating.value=true;try{await persistProfile();ElMessage.success('测试资料已保存到项目档案')}catch(error){ElMessage.error(error.response?.data?.msg||error.message||'保存资料失败')}finally{generating.value=false}}
const categories = ['discovery', 'brand_identity', 'website_guided']
const categoryNames = { discovery: '行业自然发现', brand_identity: '品牌核验', website_guided: '官网引导核验' }
const statusNames = { completed: '全部完成', completed_with_gaps: '完成（有缺口）', running: '采集中', pending: '待采集', stopped: '已停止', needs_review: '需要检查', success: '完成', partial: '来源不完整', failed: '失败', need_action: '等待人工处理', service_unavailable: '服务容量不足', private_account_context: '声称使用账号资料或历史记录' }
const sourceGroups = [{ key: 'citations', title: '正文引用' }, { key: 'displayed_sources', title: '界面搜索资料' }]
watch([projectId,()=>project.value?.name,()=>project.value?.website,()=>project.value?.entity_type], () => {
  searchEpoch++;nativeRequest++;reportRequest++;detailRequest++;recordIntent.value++
  if(!restoringWorkspace.value){nativeStarting.value=false;reportLoading.value=false;reportChoice.value='automatic';const selected=project.value;company.value=selected?.company||'';audience.value=selected?.audience||'';aliasesText.value=profileLines(selected?.aliases).join('\n');officialUrlsText.value=profileLines(selected?.official_urls).join('\n');plan.value=null;if(report.value&&!sameBrand(report.value.profile,selected)){report.value=null;selectedReportId.value=''}}
},{flush:'sync'})
watch([company, audience, aliasesText, officialUrlsText, caseCount], () => { if (!restoringWorkspace.value) plan.value = null })
watch(desktopClientMode, () => { if (!restoringWorkspace.value) desktopModel.value = '' })
watch([plan,collectionPath,desktopClientMode,desktopModel],()=>{autoPlanConfirmed.value=false;sourceConfirmed.value=false},{flush:'sync'})
const rate = value => typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '未知'
const booleanText = value => value === true ? '出现' : value === false ? '未出现' : '未知'
const reportDate = value => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour12: false })
}
const workspaceSnapshot = () => ({ project_id: projectId.value, company: company.value, audience: audience.value,aliases:profileLines(aliasesText.value),official_urls:profileLines(officialUrlsText.value),
  case_count: caseCount.value, client_mode: desktopClientMode.value, model_label: desktopModel.value,
  selected_report_id: selectedReportId.value, plan: plan.value, report: report.value })
const saveWorkspace = () => {
  if (!isDesktop.value || !workspaceReady || restoringWorkspace.value || sourceRequested.value) return
  clearTimeout(workspaceSaveTimer)
  workspaceSaveTimer = setTimeout(async () => {
    if(sourceRequested.value)return
    try { await nativeBridge().save_workspace_state(workspaceSnapshot()); workspaceSaveError.value = '' }
    catch { workspaceSaveError.value = '自动保存未成功，请先保留当前计划或报告文件，再重试。' }
  }, 200)
}
watch([projectId, company, audience, aliasesText, officialUrlsText, caseCount, desktopClientMode, desktopModel, selectedReportId, plan, report], saveWorkspace, { deep: true })
const restoreWorkspace = async () => {
  if (!isDesktop.value) return
  const request=++restoreRequest,initialEpoch=searchEpoch
  restoringWorkspace.value=true
  for (let attempt = 0; attempt < 20 && !window.pywebview?.api; attempt++) await new Promise(resolve => setTimeout(resolve, 100))
  if (!window.pywebview?.api || destroyed) {if(request===restoreRequest)restoringWorkspace.value=false;return}
  nativeReady.value=true
  const api = nativeBridge()
  try {
    const saved = await api.get_workspace_state()
    if(destroyed || request!==restoreRequest || initialEpoch!==searchEpoch)return
    if (saved.project_id != null && projects.value.some(item => item.id === saved.project_id)) projectId.value = saved.project_id
    else {const prior=saved.plan?.profile||saved.report?.profile;projectId.value=prior?projects.value.find(item=>sameBrand(item,prior))?.id||null:null}
    const scope=searchScope(),intent=recordIntent.value
    const draft=restoreEntityDraftFields(project.value,saved)
    company.value=draft.company
    audience.value=draft.audience
    aliasesText.value=profileLines(draft.aliases).join('\n')
    officialUrlsText.value=profileLines(draft.official_urls).join('\n')
    if (typeof saved.case_count === 'number') caseCount.value = saved.case_count
    if (['consumer', 'work'].includes(saved.client_mode)) desktopClientMode.value = saved.client_mode
    if (typeof saved.model_label === 'string') desktopModel.value = saved.model_label
    const [task,history]=await Promise.all([api.get_task(),api.list_tasks()])
    if(!sameSearchScope(scope) || request!==restoreRequest || intent!==recordIntent.value)return
    const matches=searchTaskMatchesProject(task,project.value)
    const records=restoredSearchRecords({saved,project:project.value,currentReport:report.value,
      explicitReport:reportChoice.value==='explicit',task:matches?task:null})
    plan.value=null;if(records.plan)setRecord(records.plan)
    if(!records.report)report.value=null
    if(records.report) {
      setRecord(records.report)
      if(reportChoice.value!=='explicit') {
        reportChoice.value=records.report===saved.report && saved.selected_report_id?'explicit':records.report===task.result?.report?'task':'workspace'
        selectedReportId.value=savedReports.value.find(item=>item.run_id===records.report.run_id)?.id || (records.report===saved.report?saved.selected_report_id || '':'')
      }
    } else selectedReportId.value=''
    nativeTask.value=task;nativeHistory.value=history;handledNativeTask=null
    if(matches)taskBinding.value={taskId:task.id,project:searchProjectSnapshot(project.value)}
    rememberRecovery(task)
    await nextTick()
    workspaceReady = true
  } catch { workspaceSaveError.value = '未能恢复上次工作区。已保存报告仍可从列表查看，请检查软件日志。' }
  finally {if(request===restoreRequest)restoringWorkspace.value = false}
}
const loadSavedReport = async (mode='explicit') => {
  const id = selectedReportId.value
  if (!id) return
  const scope=searchScope(),request=++reportRequest,intent=++recordIntent.value
  reportChoice.value=mode==='automatic'?'automatic':'explicit'
  reportLoading.value = true
  try {
    const response = await geoTestApi.getReport(id)
    if (sameSearchScope(scope) && request===reportRequest && intent===recordIntent.value && id === selectedReportId.value) { setRecord(response.data); plan.value = null }
  } catch { /* Request interceptor presents the error. */ }
  finally {if(request===reportRequest)reportLoading.value = false}
}
const safeLink = value => {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}
const download = (value, filename) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const downloadProfile = () => {try{download(editableProfile(),'client-profile.json')}catch(error){ElMessage.error(error.message)}}
const generate = async () => {
  if(generating.value||nativeBusy.value||!project.value)return
  const id=projectId.value
  generating.value = true
  try {
    const saved=await persistProfile()
    const request = { project_id:id,case_count:caseCount.value,...entityProfileFields(saved) }
    const response = await geoTestApi.generatePlan(request)
    if (request.project_id === projectId.value && request.case_count === caseCount.value && sameBrand(response.data.profile,project.value)) plan.value = response.data
    else ElMessage.info('客户资料已变更，请重新生成计划')
  } catch(error) { ElMessage.error(error.response?.data?.msg||error.message||'生成计划失败') }
  finally { generating.value = false }
}
const setRecord = value => {
    if (value.schema_version !== '1.0' || typeof value.profile?.name !== 'string' || !Array.isArray(value.cases)
      || !value.cases.length || value.cases.length > 12 || !value.cases.every(item => item && categories.includes(item.category) && typeof item.question === 'string')) throw new Error('计划或报告格式无效')
    if (value.kind === 'geo_search_test_report') {
      if (!value.cohorts || !value.cases.every(item => item.assessment && ['citations', 'displayed_sources'].every(key => item[key] == null || (Array.isArray(item[key]) && item[key].every(source => source && typeof source === 'object' && (source.url == null || typeof source.url === 'string') && (source.title == null || typeof source.title === 'string')))))) throw new Error('报告缺少有效的评估或来源数据')
      report.value = value
    } else if (value.platform === 'doubao_web' && typeof value.plan_sha256 === 'string') plan.value = value
    else throw new Error('文件类型不支持')
}
const importRecord = async event => {
  const file = event.target.files?.[0]
  if (!file) return
  const scope=searchScope(),intent=++recordIntent.value
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('文件不能超过 5 MB')
    const value=JSON.parse(await file.text());if(!sameSearchScope(scope) || intent!==recordIntent.value)return
    setRecord(value);reportChoice.value='explicit'
    if(value.kind==='geo_search_test_report')selectedReportId.value=savedReports.value.find(item=>item.run_id===value.run_id)?.id || ''
    ElMessage.success('已导入')
  } catch (error) { ElMessage.error(error.message || '导入失败') }
  finally { event.target.value = '' }
}
const loadAutomaticSource=async()=>{
  const sequence=++sourceRead,source=route.query.source_cycle,projectQuery=route.query.journey_project
  sourcePreview.value=null;sourceConfirmed.value=false;sourceLoadError.value='';sourceLoading.value=true;clearTimeout(workspaceSaveTimer)
  const current=()=>!destroyed&&sequence===sourceRead&&source===route.query.source_cycle&&projectQuery===route.query.journey_project
  try{
    const request=automaticSourceRequest(route.query)
    if(!request)return
    const response=await geoContentApi.get(request.cycleId)
    if(!current())return
    const owner=projects.value.find(item=>item.id===request.projectId),cycle=response.data
    if(!owner||cycle?.id!==request.cycleId)throw new Error('原任务单或对应品牌未找到，未载入其他记录。')
    const originalPlan=await automaticPlanFromExternalCycle(cycle,owner)
    if(!current())return
    projectId.value=owner.id
    sourcePreview.value={cycleId:request.cycleId,projectId:owner.id,plan:originalPlan}
    collectionPath.value='automatic';desktopClientMode.value='consumer';desktopModel.value=''
  }catch(error){if(current())sourceLoadError.value=error.message||'原任务单暂未读取到；不会使用其他计划替代。'}
  finally{if(current())sourceLoading.value=false}
}
watch(()=>[route.query.source_cycle,route.query.journey_project],()=>{
  if(sourceRequested.value)void loadAutomaticSource()
  else{sourceRead++;sourcePreview.value=null;sourceConfirmed.value=false;sourceLoadError.value='';sourceLoading.value=false}
},{flush:'sync'})
onMounted(async () => {
  restoringWorkspace.value=isDesktop.value
  if (isDesktop.value) nativeTimer = setInterval(pollNative, 1000)
  loading.value = true
  try { projects.value = (await projectApi.getProjects()).data }
  catch { /* Request interceptor presents the error. */ }
  finally { loading.value = false }
  try {
    savedReports.value = (await geoTestApi.listReports()).data
    const requested = typeof route.query.report === 'string' ? route.query.report : ''
    const selected = savedReports.value.find(item => item.id === requested)
      || savedReports.value.find(item => item.status === 'completed') || savedReports.value[0]
    if (selected) {
      selectedReportId.value = selected.id
      await loadSavedReport(requested?'explicit':'automatic')
      if (requested && !isDesktop.value) { await nextTick(); reportCard.value?.$el.scrollIntoView({ block: 'start' }) }
    }
  } catch { /* Request interceptor presents the error. */ }
  await restoreWorkspace()
  if(sourceRequested.value)await loadAutomaticSource()
})
onUnmounted(() => {destroyed=true;searchEpoch++;nativeRequest++;restoreRequest++;reportRequest++;detailRequest++;sourceRead++;if (nativeTimer) clearInterval(nativeTimer); if (workspaceSaveTimer) clearTimeout(workspaceSaveTimer) })
</script>

<style scoped>
.search-tests { max-width: 1200px; margin: auto; display: grid; gap: 20px; }
header, .card-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h1 { margin: 0; font-size: 28px; } h2 { margin: 0 0 16px; font-size: 20px; }
header p, .context { color: #59696e; line-height: 1.7; }
.history-heading h2 { margin-bottom: 4px; }
.history-heading .context { margin: 0 0 12px; font-size: 13px; }
.history-pagination { margin-top: 16px; justify-content: flex-end; }
.form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.profile-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px; }
.el-select { width: 100%; } .link { margin-left: 16px; color: #0a756e; }
.cohorts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 24px; }
.cohorts article { background: #f3f8f7; padding: 18px; border-radius: 8px; }
.cohorts h3 { margin-top: 0; font-size: 16px; } .cohorts strong { font-size: 30px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; background: #f5f7f8; padding: 16px; border-radius: 6px; font-family: inherit; }
li { margin: 8px 0; overflow-wrap: anywhere; } a { color: #0a756e; }
@media (max-width: 900px) { .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { header, .card-heading { align-items: flex-start; flex-direction: column; } .form-grid, .profile-grid, .cohorts { grid-template-columns: 1fr; } }
</style>
