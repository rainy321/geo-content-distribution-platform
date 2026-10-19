<template>
  <main class="search-tests">
    <header>
      <div><h1>AI 搜索测试</h1><p>按客户业务生成问题，查看 AI 回答中实际出现的品牌与来源。</p></div>
      <el-button :disabled="nativeBusy" @click="importInput?.click()">导入计划或报告</el-button>
      <input ref="importInput" type="file" accept=".json,application/json" hidden @change="importRecord" />
    </header>
    <el-alert v-if="workspaceSaveError" type="error" :title="workspaceSaveError" :closable="false" />

    <el-card v-if="isDesktop" shadow="never">
      <h2>桌面自动测试</h2>
      <div class="form-grid">
        <el-form-item label="豆包界面"><el-select v-model="desktopClientMode" :disabled="nativeBusy">
          <el-option label="豆包对话" value="consumer" /><el-option label="豆包工作" value="work" />
        </el-select></el-form-item>
        <el-form-item label="模型选项（可选）"><el-input v-model="desktopModel" :disabled="nativeBusy" placeholder="留空使用当前模型" /></el-form-item>
      </div>
      <el-button :disabled="nativeBusy" @click="startNative('login')">登录豆包</el-button>
      <el-button :disabled="nativeBusy || (!project && !report)" @click="startNative('plan')">用豆包生成问题</el-button>
      <el-button type="primary" :disabled="nativeBusy || !plan" @click="startNative('run')">开始测试</el-button>
      <el-button v-if="nativeBusy" type="warning" @click="stopNative">停止任务</el-button>
      <p class="context">{{ nativeMessage }}</p>
      <p class="context">任务、计划和客户资料会自动保存，重开后恢复。登录或验证请在专用豆包窗口正常完成，登录完成后关闭登录窗口保存会话。</p>
      <p v-if="!project && report" class="context">当前出题客户：{{ report.profile.name }}。也可在下方选择其他品牌项目。</p>
    </el-card>

    <el-card v-if="isDesktop" shadow="never" data-testid="desktop-history">
      <h2>任务记录</h2>
      <el-table :data="nativeHistory" empty-text="暂无任务记录">
        <el-table-column label="时间" min-width="155"><template #default="{ row }">{{ reportDate(row.created_at) }}</template></el-table-column>
        <el-table-column prop="client_name" label="客户" min-width="140" />
        <el-table-column label="任务" width="100"><template #default="{ row }">{{ taskNames[row.kind] || row.kind }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }">{{ statusNames[row.status] || row.status }}</template></el-table-column>
        <el-table-column label="详情" min-width="220"><template #default="{ row }">{{ row.message || '记录已保存' }}</template></el-table-column>
        <el-table-column width="80"><template #default="{ row }"><el-button link :disabled="nativeBusy" @click="showNativeTask(row.id)">查看</el-button></template></el-table-column>
      </el-table>
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
              <el-option v-for="item in projects" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="运营公司（可选）"><el-input v-model="company" data-testid="company-input" :disabled="nativeBusy || generating" maxlength="300" placeholder="用于核验公司与产品的关系" /></el-form-item>
          <el-form-item label="目标客户（可选）"><el-input v-model="audience" :disabled="nativeBusy || generating" maxlength="300" placeholder="例如：企业采购团队" /></el-form-item>
          <el-form-item label="问题数量"><el-input-number v-model="caseCount" :disabled="nativeBusy || generating" :min="3" :max="12" /></el-form-item>
        </div>
        <p v-if="project" class="context">{{ project.industry || '请在品牌项目补充行业' }} · {{ project.description || '可在品牌项目补充业务说明' }}</p>
        <el-button type="primary" :loading="generating" :disabled="!project || nativeBusy" @click="generate">AI 生成测试计划</el-button>
        <el-button :disabled="!project" @click="downloadProfile">导出客户资料</el-button>
        <router-link to="/projects" class="link">管理品牌项目</router-link>
      </el-form>
    </el-card>

    <el-alert class="notice" type="info" :closable="false" show-icon
      title="行业问题不带品牌，用于观察自然提及；点名品牌和提供官网的核验问题分别统计。提及可能是否定回答或资料不足。" />

    <el-card v-if="plan" shadow="never">
      <div class="card-heading"><h2>{{ plan.profile.name }} · 测试计划</h2><el-button @click="download(plan, 'plan.json')">下载计划</el-button></div>
      <p class="context">问题由 AI 生成，运行前请检查是否符合客户业务。复测使用同一份计划。</p>
      <el-table :data="plan.cases">
        <el-table-column label="类别" width="130"><template #default="{ row }">{{ categoryNames[row.category] }}</template></el-table-column>
        <el-table-column prop="question" label="测试问题" min-width="260" />
        <el-table-column prop="intent" label="测试目的" min-width="180" />
      </el-table>
    </el-card>

    <el-card v-if="report" ref="reportCard" shadow="never">
      <div class="card-heading"><h2>{{ report.profile.name }} · 采集报告</h2><el-tag>{{ statusNames[report.status] || report.status }}</el-tag><el-button type="primary" :disabled="nativeBusy" @click="useReport">用本报告制定内容任务</el-button></div>
      <p class="context">历史采集记录 · {{ reportDate(report.cases[0]?.collected_at || report.updated_at) }}。回答原文来自豆包，尚未逐项核实事实；“提及”只说明名称出现在回答里。</p>
      <p v-if="report.stop_reason" class="context">中断原因：{{ report.stop_reason }}</p>
      <details v-if="report.evaluation_projection" class="context"><summary>评估说明 · {{ report.evaluation_projection.stored_evaluation_version }} → {{ report.evaluation_projection.applied_evaluation_version }}</summary><p>{{ report.evaluation_projection.note }}</p></details>
      <div class="cohorts">
        <article v-for="category in categories" :key="category">
          <h3>{{ categoryNames[category] }}</h3>
          <strong>{{ rate(report.cohorts[category]?.brand_mention_rate) }}</strong><p>品牌提及率</p>
          <small>完成 {{ report.cohorts[category]?.answers_evaluable || 0 }} / {{ report.cohorts[category]?.planned || 0 }} 题</small>
          <p class="context">官网搜索资料命中 {{ report.cohorts[category]?.official_source_hits || 0 }} / 可判断 {{ report.cohorts[category]?.official_source_evaluable || 0 }}</p>
          <p class="context">官网正文引用命中 {{ report.cohorts[category]?.official_citation_hits || 0 }} / 可判断 {{ report.cohorts[category]?.official_citation_evaluable || 0 }}</p>
        </article>
      </div>
      <el-collapse>
        <el-collapse-item v-for="item in report.cases" :key="item.id" :name="item.id" :title="`${categoryNames[item.category]} · ${item.question}`">
          <p>{{ statusNames[item.status] || item.status }} · 品牌提及：{{ booleanText(item.assessment.brand_mentioned) }}</p>
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
import { ElMessage } from 'element-plus'
import { projectApi } from '@/api/project'
import { geoTestApi } from '@/api/geoTest'
import { geoContentApi } from '@/api/geoContent'

const projects = ref([]), projectId = ref(null), company = ref(''), audience = ref(''), caseCount = ref(4)
const loading = ref(false), generating = ref(false), plan = ref(null), report = ref(null), importInput = ref(null)
const route = useRoute(), savedReports = ref([]), selectedReportId = ref(''), reportLoading = ref(false), reportCard = ref(null)
const isDesktop = computed(() => route.query.desktop === '1' || Boolean(window.pywebview?.api))
const router = useRouter()
const useReport = async () => {
  const value = (await geoContentApi.create({ report: report.value })).data
  router.push({path:'/geo-content',query:{cycle:value.id}})
}
const desktopClientMode = ref('consumer'), desktopModel = ref(''), nativeTask = ref({ status: 'idle' })
const nativeHistory = ref([]), nativeDetails = ref(null), restoringWorkspace = ref(false)
const workspaceSaveError = ref('')
const taskNames = { login: '登录豆包', plan: '生成问题', run: '采集测试', research: '采集内容策划', draft: '生成平台稿' }
let workspaceSaveTimer = null, workspaceReady = false
const nativeBusy = computed(() => nativeTask.value.status === 'running')
const nativeMessage = computed(() => `${nativeTask.value.restored_from_disk ? '上次任务：' : ''}${nativeTask.value.message || ({ idle: '先生成并检查问题，再开始测试。', running: `任务执行中${nativeTask.value.progress?.case_id ? ` · ${nativeTask.value.progress.case_id}` : ''}。`, completed: '任务已完成。', stopped: '任务已停止。', failed: '任务未完成。', needs_review: '上次任务未完成，记录已保留，请检查后再执行。' }[nativeTask.value.status] || '')}`)
let nativeTimer = null, handledNativeTask = null
const nativeBridge = () => {
  if (!window.pywebview?.api) throw new Error('桌面采集服务尚未就绪，请稍后重试')
  return window.pywebview.api
}
const startNative = async kind => {
  try {
    const options = { client_mode: desktopClientMode.value, model_label: desktopModel.value.trim() }
    const api = nativeBridge()
    const source = project.value || report.value?.profile
    const result = kind === 'login' ? await api.open_login(options)
      : kind === 'plan' ? await api.plan_with_doubao({ ...source, company: company.value || source.company || '', audience: audience.value || source.audience || '' }, caseCount.value, options)
        : await api.start_run(plan.value, options)
    if (result.error) throw new Error(result.error)
    nativeTask.value = { ...result, kind }
    nativeHistory.value = await api.list_tasks()
    if (kind === 'run') report.value = null
  } catch (error) { ElMessage.error(error.message || '任务无法启动') }
}
const stopNative = async () => {
  try { await nativeBridge().stop_task() }
  catch (error) { ElMessage.error(error.message || '停止失败') }
}
const pollNative = async () => {
  if (!nativeBusy.value) return
  try {
    const task = await nativeBridge().get_task()
    nativeTask.value = task
    if (task.status !== 'running' && handledNativeTask !== task.id) {
      handledNativeTask = task.id
      if (task.result?.plan) setRecord(task.result.plan)
      if (task.result?.report) setRecord(task.result.report)
      if (task.kind === 'run') {
        savedReports.value = (await geoTestApi.listReports()).data
        const saved = savedReports.value.find(item => item.run_id === task.result?.report?.run_id)
        if (saved) selectedReportId.value = saved.id
      }
      nativeHistory.value = await nativeBridge().list_tasks()
      if (task.status === 'failed') ElMessage.error(task.message || '采集未完成')
    }
  } catch { /* Keep the last task state if the desktop window is closing. */ }
}
const showNativeTask = async id => {
  try {
    const task = await nativeBridge().get_task(id)
    nativeDetails.value = task
    if (task.result?.plan) setRecord(task.result.plan)
    if (task.result?.report) setRecord(task.result.report)
    if (task.message) ElMessage.info(task.message)
  } catch (error) { ElMessage.error(error.message || '读取任务记录失败') }
}
const project = computed(() => projects.value.find(item => item.id === projectId.value))
const categories = ['discovery', 'brand_identity', 'website_guided']
const categoryNames = { discovery: '行业自然发现', brand_identity: '品牌核验', website_guided: '官网引导核验' }
const statusNames = { completed: '全部完成', completed_with_gaps: '完成（有缺口）', running: '采集中', pending: '待采集', stopped: '已停止', needs_review: '需要检查', success: '完成', partial: '来源不完整', failed: '失败', need_action: '等待人工处理', service_unavailable: '服务容量不足' }
const sourceGroups = [{ key: 'citations', title: '正文引用' }, { key: 'displayed_sources', title: '界面搜索资料' }]
watch(projectId, () => { if (!restoringWorkspace.value) { company.value = ''; audience.value = ''; plan.value = null } })
watch([company, audience, caseCount], () => { if (!restoringWorkspace.value) plan.value = null })
watch(desktopClientMode, () => { if (!restoringWorkspace.value) desktopModel.value = '' })
const rate = value => typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '未知'
const booleanText = value => value === true ? '出现' : value === false ? '未出现' : '未知'
const reportDate = value => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour12: false })
}
const workspaceSnapshot = () => ({ project_id: projectId.value, company: company.value, audience: audience.value,
  case_count: caseCount.value, client_mode: desktopClientMode.value, model_label: desktopModel.value,
  selected_report_id: selectedReportId.value, plan: plan.value, report: report.value })
const saveWorkspace = () => {
  if (!isDesktop.value || !workspaceReady || restoringWorkspace.value) return
  clearTimeout(workspaceSaveTimer)
  workspaceSaveTimer = setTimeout(async () => {
    try { await nativeBridge().save_workspace_state(workspaceSnapshot()); workspaceSaveError.value = '' }
    catch { workspaceSaveError.value = '自动保存未成功，请先保留当前计划或报告文件，再重试。' }
  }, 200)
}
watch([projectId, company, audience, caseCount, desktopClientMode, desktopModel, selectedReportId, plan, report], saveWorkspace, { deep: true })
const restoreWorkspace = async () => {
  if (!isDesktop.value) return
  for (let attempt = 0; attempt < 20 && !window.pywebview?.api; attempt++) await new Promise(resolve => setTimeout(resolve, 100))
  if (!window.pywebview?.api) return
  const api = nativeBridge()
  try {
    const saved = await api.get_workspace_state()
    restoringWorkspace.value = true
    if (saved.project_id != null && projects.value.some(item => item.id === saved.project_id)) projectId.value = saved.project_id
    else if (saved.client_name) projectId.value = projects.value.find(item => item.name === saved.client_name)?.id || null
    if (typeof saved.company === 'string') company.value = saved.company
    if (typeof saved.audience === 'string') audience.value = saved.audience
    if (typeof saved.case_count === 'number') caseCount.value = saved.case_count
    if (saved.plan) setRecord(saved.plan)
    if (saved.report) setRecord(saved.report)
    else if ('report' in saved) report.value = null
    if (typeof saved.selected_report_id === 'string') selectedReportId.value = saved.selected_report_id
    if (['consumer', 'work'].includes(saved.client_mode)) desktopClientMode.value = saved.client_mode
    if (typeof saved.model_label === 'string') desktopModel.value = saved.model_label
    await nextTick()
    restoringWorkspace.value = false
    nativeTask.value = await api.get_task()
    if (!('plan' in saved) && nativeTask.value.result?.plan) setRecord(nativeTask.value.result.plan)
    if (!('report' in saved) && nativeTask.value.result?.report) setRecord(nativeTask.value.result.report)
    nativeHistory.value = await api.list_tasks()
    workspaceReady = true
  } catch { workspaceSaveError.value = '未能恢复上次工作区。已保存报告仍可从列表查看，请检查软件日志。' }
  finally { restoringWorkspace.value = false }
}
const loadSavedReport = async () => {
  const id = selectedReportId.value
  if (!id) return
  reportLoading.value = true
  try {
    const response = await geoTestApi.getReport(id)
    if (id === selectedReportId.value) { setRecord(response.data); plan.value = null }
  } catch { /* Request interceptor presents the error. */ }
  finally { reportLoading.value = false }
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
const downloadProfile = () => download({ ...project.value, company: company.value, audience: audience.value, aliases: [] }, 'client-profile.json')
const generate = async () => {
  const request = { project_id: projectId.value, case_count: caseCount.value, company: company.value, audience: audience.value }
  generating.value = true
  try {
    const response = await geoTestApi.generatePlan(request)
    if (request.project_id === projectId.value && request.case_count === caseCount.value && request.company === company.value && request.audience === audience.value) plan.value = response.data
    else ElMessage.info('客户资料已变更，请重新生成计划')
  } catch { /* Request interceptor presents the error. */ }
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
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('文件不能超过 5 MB')
    setRecord(JSON.parse(await file.text()))
    ElMessage.success('已导入')
  } catch (error) { ElMessage.error(error.message || '导入失败') }
  finally { event.target.value = '' }
}
onMounted(async () => {
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
      await loadSavedReport()
      if (requested && !isDesktop.value) { await nextTick(); reportCard.value?.$el.scrollIntoView({ block: 'start' }) }
    }
  } catch { /* Request interceptor presents the error. */ }
  await restoreWorkspace()
})
onUnmounted(() => { if (nativeTimer) clearInterval(nativeTimer); if (workspaceSaveTimer) clearTimeout(workspaceSaveTimer) })
</script>

<style scoped>
.search-tests { max-width: 1200px; margin: auto; display: grid; gap: 20px; }
header, .card-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h1 { margin: 0; font-size: 28px; } h2 { margin: 0 0 16px; font-size: 20px; }
header p, .context { color: #59696e; line-height: 1.7; }
.form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.el-select { width: 100%; } .link { margin-left: 16px; color: #0a756e; }
.cohorts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 24px; }
.cohorts article { background: #f3f8f7; padding: 18px; border-radius: 8px; }
.cohorts h3 { margin-top: 0; font-size: 16px; } .cohorts strong { font-size: 30px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; background: #f5f7f8; padding: 16px; border-radius: 6px; font-family: inherit; }
li { margin: 8px 0; overflow-wrap: anywhere; } a { color: #0a756e; }
@media (max-width: 900px) { .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { header, .card-heading { align-items: flex-start; flex-direction: column; } .form-grid, .cohorts { grid-template-columns: 1fr; } }
</style>
