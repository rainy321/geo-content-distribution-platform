<template>
  <main class="billing-page">
    <section class="ledger-hero">
      <div class="hero-copy">
        <span class="eyebrow">MODEL ECONOMICS / AUDIT LEDGER</span>
        <h1>每一次模型调用，<br>都有凭据。</h1>
        <p>按项目、时间与操作核对 Token 和估算费用。未知用量、缺失单价与不同币种始终分开展示。</p>
      </div>
      <div class="audit-seal" aria-label="账单边界">
        <span>LOCAL LEDGER</span>
        <strong>{{ bill.ledgerVersion || '等待载入' }}</strong>
        <small>模型调用 · 不含平台发布</small>
      </div>
    </section>

    <section class="filter-rail" aria-label="账单筛选">
      <label class="filter-control project-filter">
        <span>品牌项目</span>
        <el-select v-model="filters.projectId" placeholder="全部项目" clearable @change="loadBill">
          <el-option label="全部项目" value="" />
          <el-option
            v-for="project in projects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
      </label>

      <fieldset class="period-switch">
        <legend>账期</legend>
        <button
          v-for="option in periodOptions"
          :key="option.value"
          type="button"
          :class="{ active: filters.days === option.value }"
          @click="setDays(option.value)"
        >
          {{ option.label }}
        </button>
      </fieldset>

      <label class="filter-control">
        <span>模型操作</span>
        <el-select v-model="filters.operation" placeholder="全部操作" clearable @change="loadBill">
          <el-option label="全部操作" value="" />
          <el-option v-for="item in operationOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </label>

      <label class="filter-control">
        <span>核算状态</span>
        <el-select v-model="filters.costStatus" placeholder="全部状态" clearable @change="loadBill">
          <el-option label="全部状态" value="" />
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </label>

      <div class="filter-actions">
        <el-button :loading="loading" @click="loadBill">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button :disabled="!bill.records.length" @click="exportCsv">
          <el-icon><Download /></el-icon>
          导出明细
        </el-button>
      </div>
    </section>

    <section v-if="loadError" class="load-error" role="alert">
      <div>
        <strong>账单暂未载入</strong>
        <p>{{ loadError }}</p>
      </div>
      <el-button @click="loadBill">重新读取</el-button>
    </section>

    <section class="invoice-sheet" :aria-busy="loading">
      <div class="invoice-spine" aria-hidden="true">
        <span>A</span><span>I</span><i /><span>C</span><span>O</span><span>S</span><span>T</span>
      </div>

      <div class="invoice-body">
        <header class="invoice-heading">
          <div>
            <span>ESTIMATED MODEL COST</span>
            <h2>模型估算费用</h2>
            <p>{{ periodLabel }} · {{ selectedProjectName }}</p>
          </div>
          <div class="invoice-meta">
            <small>生成时间</small>
            <strong>{{ formatDateTime(bill.generatedAt) }}</strong>
            <small>凭证范围</small>
            <strong>{{ bill.summary.operationCount }} 次模型调用</strong>
          </div>
        </header>

        <div class="total-band">
          <div class="money-total">
            <template v-if="bill.summary.calculatedCosts.length">
              <strong v-for="cost in bill.summary.calculatedCosts" :key="cost.currency">
                {{ formatMoney(cost.amount, cost.currency) }}
                <small>{{ cost.currency }}</small>
              </strong>
            </template>
            <strong v-else class="pending-cost">待核算</strong>
            <p>仅合计已有用量和单价快照的记录；不同币种不会相加。</p>
          </div>

          <dl class="reconcile-facts">
            <div>
              <dt>已知 Token</dt>
              <dd>{{ formatTokenCount(bill.summary.knownTotalTokens) }}</dd>
              <small>{{ bill.summary.knownTokenRecordCount }} 条有 Token 回执</small>
            </div>
            <div>
              <dt>费用覆盖率</dt>
              <dd>{{ bill.summary.costCoveragePercent.toFixed(1) }}%</dd>
              <small>{{ bill.summary.calculatedCostCount }} / {{ bill.summary.operationCount }} 条已核算</small>
            </div>
            <div :class="{ warning: bill.summary.unknownUsageCount }">
              <dt>用量未知</dt>
              <dd>{{ bill.summary.unknownUsageCount }}</dd>
              <small>供应商未返回 Token</small>
            </div>
            <div :class="{ warning: bill.summary.unpricedCount || bill.summary.insufficientUsageCount }">
              <dt>待补单价 / 用量</dt>
              <dd>{{ bill.summary.unpricedCount + bill.summary.insufficientUsageCount }}</dd>
              <small>不会伪装成零成本</small>
            </div>
          </dl>
        </div>

        <div v-if="hasReconcileWarnings" class="reconcile-alert" role="status">
          <span>RECONCILE</span>
          <div>
            <strong>本账期仍有 {{ bill.summary.unknownCostCount }} 条费用无法计算</strong>
            <p>先核对模型用量回执与单价配置，再用于客户结算；系统不会用 0 填补未知值。</p>
          </div>
          <button type="button" @click="goToSettings">查看模型设置</button>
        </div>

        <section class="operation-strip" aria-labelledby="operation-title">
          <header>
            <div>
              <span>OPERATION SPLIT</span>
              <h3 id="operation-title">钱花在哪一步</h3>
            </div>
            <small>线长代表已知 Token，不代表效果高低</small>
          </header>
          <div v-if="operationRows.length" class="operation-list">
            <article v-for="item in operationRows" :key="item.operation">
              <div class="operation-name">
                <strong>{{ operationLabel(item.operation) }}</strong>
                <small>{{ item.operationCount }} 次调用</small>
              </div>
              <div class="token-line" aria-hidden="true"><i :style="{ width: `${operationWidth(item)}%` }" /></div>
              <div class="operation-amount">
                <strong>{{ operationCost(item) }}</strong>
                <small>{{ formatTokenCount(item.knownTotalTokens) }} Token</small>
              </div>
            </article>
          </div>
          <div v-else class="empty-ledger">当前筛选范围没有模型调用。生成文章、执行 GEO 调优或真实 AI 采样后会形成不可变凭证。</div>
        </section>
      </div>
    </section>

    <section class="breakdown-grid">
      <article class="breakdown-panel">
        <header>
          <div><span>MODEL SPLIT</span><h2>模型分账</h2></div>
          <small>{{ bill.models.length }} 个模型口径</small>
        </header>
        <div v-if="bill.models.length" class="breakdown-list">
          <div v-for="model in bill.models" :key="`${model.provider}/${model.model}`">
            <span class="provider-mark">{{ providerInitial(model.provider) }}</span>
            <div class="breakdown-name">
              <strong>{{ model.provider }} / {{ model.model }}</strong>
              <small>{{ model.operationCount }} 次 · {{ formatTokenCount(model.knownTotalTokens) }} Token</small>
            </div>
            <div class="breakdown-cost">
              <strong>{{ aggregateCost(model) }}</strong>
              <small>{{ model.costCoveragePercent.toFixed(1) }}% 已核算</small>
            </div>
          </div>
        </div>
        <p v-else class="panel-empty">暂无模型分账数据。</p>
      </article>

      <article class="breakdown-panel">
        <header>
          <div><span>PROJECT SPLIT</span><h2>项目分账</h2></div>
          <small>{{ bill.projects.length }} 个项目口径</small>
        </header>
        <div v-if="bill.projects.length" class="breakdown-list">
          <div v-for="project in bill.projects" :key="`${project.project_id}-${project.project_name}`">
            <span class="provider-mark project-mark">{{ projectInitial(project.project_name) }}</span>
            <div class="breakdown-name">
              <strong>{{ project.project_name || '未归属项目' }}</strong>
              <small>{{ project.operationCount }} 次 · {{ formatTokenCount(project.knownTotalTokens) }} Token</small>
            </div>
            <div class="breakdown-cost">
              <strong>{{ aggregateCost(project) }}</strong>
              <small>{{ project.unknownCostCount }} 条待核</small>
            </div>
          </div>
        </div>
        <p v-else class="panel-empty">暂无项目分账数据。</p>
      </article>
    </section>

    <section class="receipt-ledger" aria-labelledby="receipt-title">
      <header class="receipt-heading">
        <div>
          <span>IMMUTABLE RECEIPTS</span>
          <h2 id="receipt-title">逐次调用明细</h2>
          <p>全部筛选结果参与汇总；下表显示最新 {{ bill.recordWindow.returned }} / {{ bill.recordWindow.matched }} 条。</p>
        </div>
        <div v-if="bill.recordWindow.hasMore" class="window-notice">
          当前最多展示并导出 {{ bill.recordWindow.limit }} 条，顶部合计仍包含全部匹配记录。
        </div>
      </header>

      <div v-if="bill.records.length" class="receipt-table-wrap">
        <table class="receipt-table">
          <thead>
            <tr>
              <th>时间 / 凭证</th>
              <th>项目 / 操作</th>
              <th>模型</th>
              <th>输入 / 输出 / 合计</th>
              <th>单价快照</th>
              <th>本次费用</th>
              <th>核算状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="receipt in bill.records" :key="receipt.id || `${receipt.operation}-${receipt.createdAt}`">
              <td>
                <strong>{{ formatDateTime(receipt.createdAt) }}</strong>
                <small>#{{ receipt.id ?? '—' }}</small>
              </td>
              <td>
                <strong>{{ receipt.projectName }}</strong>
                <small>{{ operationLabel(receipt.operation) }}<template v-if="receipt.articleTitle"> · {{ receipt.articleTitle }}</template></small>
              </td>
              <td>
                <strong>{{ receipt.provider }}</strong>
                <small>{{ receipt.model }}</small>
              </td>
              <td class="token-cell">
                <strong>{{ formatTokenCount(receipt.inputTokens) }} / {{ formatTokenCount(receipt.outputTokens) }} / {{ formatTokenCount(receipt.totalTokens) }}</strong>
                <small>{{ usageStatusLabel(receipt.usageStatus) }}</small>
              </td>
              <td>
                <strong>{{ formatPriceSnapshot(receipt) }}</strong>
                <small>每百万 Token</small>
              </td>
              <td class="amount-cell">
                <strong>{{ formatMoney(receipt.estimatedCost, receipt.currency) }}</strong>
                <small>{{ receipt.currency || '币种未知' }}</small>
              </td>
              <td>
                <span :class="['status-chip', receipt.costStatus]">{{ costStatusLabel(receipt.costStatus) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="receipt-empty-state">
        <span>NO RECEIPTS</span>
        <h3>当前筛选范围没有调用凭证</h3>
        <p>这不等于成本为 0，只表示尚无可展示记录。可以扩大账期或清除筛选条件。</p>
        <el-button v-if="hasActiveFilters" @click="resetFilters">查看全部账单</el-button>
      </div>
    </section>

    <section class="billing-boundary">
      <span>NON_MODEL</span>
      <div>
        <strong>登录、上传与平台发布：Token 不适用，不是 0</strong>
        <p>{{ bill.publishingMessage || '只有实际调用 AI 模型的步骤才进入本账单。浏览器自动化、账号验证和平台上传不伪装成模型费用。' }}</p>
      </div>
      <p>{{ bill.pricingMessage || '账单为本机估算凭证，最终以模型供应商账单为准。' }}</p>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Download, Refresh } from '@element-plus/icons-vue'
import { billingApi } from '@/api/billing'
import { projectApi } from '@/api/project'
import {
  buildCostLedgerCsv,
  costStatusLabel,
  formatMoney,
  formatTokenCount,
  normalizeCostBill,
  operationLabel,
  usageStatusLabel
} from '@/utils/costLedger'

const route = useRoute()
const router = useRouter()
const projects = ref([])
const loading = ref(false)
const loadError = ref('')
const bill = ref(normalizeCostBill({}))
const filters = reactive({ projectId: '', days: 30, operation: '', costStatus: '' })
let requestSequence = 0

const periodOptions = [
  { label: '7 天', value: 7 },
  { label: '30 天', value: 30 },
  { label: '90 天', value: 90 },
  { label: '全部', value: 0 }
]
const operationOptions = [
  { label: '文章生成', value: 'generate' },
  { label: 'GEO 调优', value: 'optimize' },
  { label: '平台适配', value: 'platform_adapt' },
  { label: 'AI 可见度采样', value: 'visibility_sample' }
]
const statusOptions = [
  { label: '已核算', value: 'calculated' },
  { label: '未配置单价', value: 'unpriced' },
  { label: '用量未知', value: 'unknown_usage' },
  { label: '用量不完整', value: 'insufficient_usage' }
]

const selectedProjectName = computed(() => {
  if (!filters.projectId) return '全部项目'
  return projects.value.find(item => Number(item.id) === Number(filters.projectId))?.name || '所选项目'
})
const periodLabel = computed(() => periodOptions.find(item => item.value === filters.days)?.label || `${filters.days} 天`)
const hasReconcileWarnings = computed(() => bill.value.summary.unknownCostCount > 0)
const hasActiveFilters = computed(() => Boolean(filters.projectId || filters.operation || filters.costStatus || filters.days !== 0))
const operationRows = computed(() => {
  const rows = new Map(bill.value.operations.map(item => [item.operation, item]))
  return operationOptions.map(option => rows.get(option.value)).filter(Boolean)
})
const maximumOperationTokens = computed(() => Math.max(1, ...operationRows.value.map(item => item.knownTotalTokens || 0)))

const setDays = value => {
  if (filters.days === value) return
  filters.days = value
  loadBill()
}

const loadProjects = async () => {
  try {
    const response = await projectApi.getProjects()
    projects.value = Array.isArray(response.data) ? response.data : []
    const requested = Number(route.query.project_id)
    if (Number.isInteger(requested) && projects.value.some(item => Number(item.id) === requested)) {
      filters.projectId = requested
    }
  } catch (error) {
    projects.value = []
  }
}

const loadBill = async () => {
  const sequence = ++requestSequence
  loading.value = true
  loadError.value = ''
  try {
    const response = await billingApi.getBill({
      project_id: filters.projectId || undefined,
      days: filters.days,
      operation: filters.operation || undefined,
      cost_status: filters.costStatus || undefined,
      limit: 500
    }, { suppressGlobalError: true })
    if (sequence !== requestSequence) return
    bill.value = normalizeCostBill(response)
  } catch (error) {
    if (sequence !== requestSequence) return
    loadError.value = error?.response?.data?.msg || error?.message || '无法读取本机成本台账'
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

const resetFilters = () => {
  Object.assign(filters, { projectId: '', days: 0, operation: '', costStatus: '' })
  loadBill()
}

const operationWidth = item => {
  if (item.knownTotalTokens == null) return 0
  return Math.max(4, Math.round(item.knownTotalTokens * 100 / maximumOperationTokens.value))
}

const aggregateCost = item => {
  if (!item.calculatedCosts?.length) return '待核算'
  return item.calculatedCosts.map(cost => formatMoney(cost.amount, cost.currency)).join(' + ')
}

const operationCost = item => aggregateCost(item)
const providerInitial = value => String(value || '?').trim().slice(0, 1).toUpperCase()
const projectInitial = value => String(value || '未').trim().slice(0, 1)

const formatDateTime = value => {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const formatPriceSnapshot = receipt => {
  if (receipt.inputPricePerMillion == null || receipt.outputPricePerMillion == null) return '待配置'
  return `${formatMoney(receipt.inputPricePerMillion, receipt.currency)} / ${formatMoney(receipt.outputPricePerMillion, receipt.currency)}`
}

const exportCsv = () => {
  const csv = buildCostLedgerCsv(bill.value.records)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const date = new Date().toISOString().slice(0, 10)
  anchor.href = url
  anchor.download = `GEO-AI成本账单-${date}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  ElMessage.success(`已导出当前明细窗口 ${bill.value.records.length} 条`)
}

const goToSettings = () => router.push('/settings')

onMounted(async () => {
  await loadProjects()
  await loadBill()
})
</script>

<style lang="scss" scoped>
.billing-page {
  --bill-ink: #111214;
  --bill-muted: #6e7075;
  --bill-paper: #fff;
  --bill-rule: #d9dad7;
  --bill-soft: #f2f2ef;
  --bill-risk: #a85a25;
  max-width: 1580px;
  margin: 0 auto;
  padding: 30px 34px 54px;
  color: var(--bill-ink);
}

.eyebrow,
.ledger-hero h1,
.invoice-heading h2,
.operation-strip h3,
.breakdown-panel h2,
.receipt-heading h2,
.receipt-empty-state h3,
.reconcile-alert strong,
.billing-boundary strong {
  font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif;
}

.eyebrow,
.audit-seal span,
.invoice-heading span,
.operation-strip header span,
.breakdown-panel header span,
.receipt-heading span,
.billing-boundary > span,
.receipt-empty-state > span {
  font: 700 10px/1.2 "Cascadia Mono", Consolas, monospace;
  letter-spacing: .14em;
}

.ledger-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 34px;
  padding: 8px 4px 30px;
}

.hero-copy {
  max-width: 820px;

  .eyebrow { color: var(--bill-muted); }

  h1 {
    margin: 12px 0 16px;
    font-size: clamp(42px, 5.2vw, 74px);
    font-weight: 790;
    letter-spacing: -.055em;
    line-height: .98;
  }

  p {
    max-width: 670px;
    color: var(--bill-muted);
    font-size: 14px;
    line-height: 1.75;
  }
}

.audit-seal {
  min-width: 260px;
  padding: 18px 20px;
  border: 1px solid var(--bill-ink);
  background: var(--bill-paper);

  span,
  strong,
  small { display: block; }
  strong { margin-top: 12px; font: 700 13px/1.3 "Cascadia Mono", monospace; }
  small { margin-top: 6px; color: var(--bill-muted); font-size: 12px; }
}

.filter-rail {
  display: grid;
  grid-template-columns: minmax(190px, 1.15fr) minmax(250px, 1.35fr) minmax(160px, .9fr) minmax(160px, .9fr) auto;
  align-items: end;
  gap: 12px;
  padding: 17px;
  border: 1px solid var(--bill-rule);
  background: var(--bill-paper);
}

.filter-control,
.period-switch {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;

  > span,
  legend {
    color: var(--bill-muted);
    font: 650 11px/1.2 "Cascadia Mono", monospace;
    letter-spacing: .06em;
  }
}

.period-switch {
  display: grid;
  grid-template-columns: repeat(4, minmax(50px, 1fr));
  margin: 0;
  padding: 0;
  border: 0;

  legend { grid-column: 1 / -1; }

  button {
    height: 32px;
    border: 1px solid var(--bill-rule);
    background: #fff;
    color: var(--bill-muted);
    cursor: pointer;
    font-size: 12px;

    & + button { border-left: 0; }
    &.active { background: var(--bill-ink); color: #fff; }
    &:focus-visible { position: relative; z-index: 1; }
  }
}

.filter-actions {
  display: flex;
  gap: 8px;
}

:deep(.filter-rail .el-select__wrapper) {
  min-height: 32px;
  border-radius: 0;
  box-shadow: 0 0 0 1px var(--bill-rule) inset;
}

.load-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 14px;
  padding: 15px 18px;
  border: 1px solid rgba(150, 62, 55, .42);
  background: #fff6f3;

  strong { font-size: 13px; }
  p { margin-top: 4px; color: #7d4540; font-size: 12px; }
}

.invoice-sheet {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  margin-top: 18px;
  border: 1px solid var(--bill-ink);
  background: var(--bill-paper);
  box-shadow: 0 18px 48px rgba(17, 18, 20, .055);
}

.invoice-spine {
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 7px;
  padding: 22px 0;
  border-right: 1px solid var(--bill-ink);
  background: var(--bill-ink);
  color: #fff;
  font: 700 10px/1 "Cascadia Mono", monospace;

  i { width: 1px; min-height: 28px; flex: 1; background: rgba(255, 255, 255, .45); }
}

.invoice-body { min-width: 0; }

.invoice-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 25px 28px;
  border-bottom: 1px solid var(--bill-rule);

  h2 { margin-top: 6px; font-size: 23px; letter-spacing: -.025em; }
  p { margin-top: 7px; color: var(--bill-muted); font-size: 12px; }
}

.invoice-meta {
  display: grid;
  grid-template-columns: auto auto;
  gap: 5px 18px;
  text-align: right;

  small { color: var(--bill-muted); font-size: 11px; }
  strong { font: 650 11px/1.4 "Cascadia Mono", monospace; }
}

.total-band {
  display: grid;
  grid-template-columns: minmax(260px, .76fr) minmax(620px, 1.6fr);
  border-bottom: 1px solid var(--bill-rule);
}

.money-total {
  display: flex;
  min-height: 185px;
  flex-direction: column;
  justify-content: center;
  padding: 28px;
  border-right: 1px solid var(--bill-rule);

  > strong {
    display: flex;
    align-items: baseline;
    gap: 9px;
    font: 760 clamp(30px, 3.4vw, 48px)/1.05 "Cascadia Mono", monospace;
    letter-spacing: -.05em;

    & + strong { margin-top: 10px; }
    small { color: var(--bill-muted); font-size: 11px; letter-spacing: .05em; }
  }

  .pending-cost { color: var(--bill-muted); }
  p { max-width: 360px; margin-top: 17px; color: var(--bill-muted); font-size: 12px; line-height: 1.55; }
}

.reconcile-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(125px, 1fr));
  margin: 0;

  > div {
    display: flex;
    min-width: 0;
    flex-direction: column;
    justify-content: center;
    padding: 24px 20px;
    border-right: 1px solid var(--bill-rule);

    &:last-child { border-right: 0; }
    &.warning { box-shadow: inset 0 -3px 0 var(--bill-risk); }
  }

  dt { color: var(--bill-muted); font-size: 12px; }
  dd { margin: 10px 0 7px; font: 720 25px/1 "Cascadia Mono", monospace; }
  small { color: var(--bill-muted); font-size: 11px; line-height: 1.45; }
}

.reconcile-alert {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 17px;
  margin: 20px 28px 0;
  padding: 14px 16px;
  border-left: 3px solid var(--bill-risk);
  background: #fbf4eb;

  > span { color: var(--bill-risk); font: 750 10px/1 "Cascadia Mono", monospace; letter-spacing: .12em; }
  strong { font-size: 13px; }
  p { margin-top: 4px; color: #765638; font-size: 12px; line-height: 1.5; }
  button { border: 0; background: transparent; color: var(--bill-ink); cursor: pointer; font-size: 12px; text-decoration: underline; }
}

.operation-strip {
  padding: 27px 28px 30px;

  > header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 18px;
    margin-bottom: 16px;

    h3 { margin-top: 5px; font-size: 20px; }
    > small { color: var(--bill-muted); font-size: 11px; }
  }
}

.operation-list {
  border-top: 1px solid var(--bill-ink);

  article {
    display: grid;
    grid-template-columns: minmax(130px, .7fr) minmax(140px, 1.5fr) minmax(190px, .8fr);
    align-items: center;
    gap: 22px;
    min-height: 68px;
    border-bottom: 1px solid var(--bill-rule);
  }
}

.operation-name,
.operation-amount,
.breakdown-name,
.breakdown-cost {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
  strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
  small { color: var(--bill-muted); font-size: 11px; }
}

.operation-amount { text-align: right; }
.operation-amount strong { font-family: "Cascadia Mono", monospace; }

.token-line {
  height: 5px;
  overflow: hidden;
  background: var(--bill-soft);
  i { display: block; height: 100%; background: var(--bill-ink); }
}

.empty-ledger,
.panel-empty {
  padding: 24px 0;
  color: var(--bill-muted);
  font-size: 12px;
  line-height: 1.6;
}

.breakdown-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}

.breakdown-panel {
  min-width: 0;
  padding: 24px 26px;
  border: 1px solid var(--bill-rule);
  background: var(--bill-paper);

  > header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--bill-ink);

    h2 { margin-top: 5px; font-size: 20px; }
    > small { color: var(--bill-muted); font-size: 11px; }
  }
}

.breakdown-list {
  > div {
    display: grid;
    grid-template-columns: 36px minmax(0, 1fr) minmax(130px, auto);
    align-items: center;
    gap: 13px;
    min-height: 68px;
    border-bottom: 1px solid var(--bill-rule);
  }
}

.provider-mark {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  background: var(--bill-ink);
  color: #fff;
  font: 750 13px/1 "Cascadia Mono", monospace;
}

.project-mark { background: var(--bill-soft); color: var(--bill-ink); }
.breakdown-cost { text-align: right; }
.breakdown-cost strong { font-family: "Cascadia Mono", monospace; }

.receipt-ledger {
  margin-top: 18px;
  border: 1px solid var(--bill-rule);
  background: var(--bill-paper);
}

.receipt-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px;
  border-bottom: 1px solid var(--bill-ink);

  h2 { margin-top: 5px; font-size: 23px; }
  p { margin-top: 7px; color: var(--bill-muted); font-size: 12px; }
}

.window-notice {
  max-width: 410px;
  padding: 9px 12px;
  border: 1px solid rgba(168, 90, 37, .38);
  color: #755033;
  font-size: 11px;
  line-height: 1.5;
}

.receipt-table-wrap { overflow-x: auto; }
.receipt-table {
  width: 100%;
  min-width: 1120px;
  border-collapse: collapse;

  th {
    padding: 12px 14px;
    border-right: 1px solid var(--bill-rule);
    background: var(--bill-soft);
    color: var(--bill-muted);
    font: 650 10px/1.25 "Cascadia Mono", monospace;
    letter-spacing: .035em;
    text-align: left;
  }

  td {
    max-width: 260px;
    padding: 15px 14px;
    border-right: 1px solid var(--bill-rule);
    border-bottom: 1px solid var(--bill-rule);
    vertical-align: top;
    font-size: 12px;

    strong,
    small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    strong { font-size: 12px; font-weight: 650; }
    small { margin-top: 6px; color: var(--bill-muted); font-size: 11px; }
  }

  th:last-child,
  td:last-child { border-right: 0; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover td { background: #fafaf8; }
}

.token-cell strong,
.amount-cell strong { font-family: "Cascadia Mono", monospace; }

.status-chip {
  display: inline-flex;
  padding: 5px 8px;
  border: 1px solid var(--bill-rule);
  font-size: 11px;

  &.calculated { border-color: rgba(50, 103, 77, .4); color: #32674d; }
  &.unpriced,
  &.insufficient_usage { border-color: rgba(168, 90, 37, .42); color: var(--bill-risk); }
  &.unknown_usage { border-color: rgba(150, 62, 55, .38); color: #963e37; }
}

.receipt-empty-state {
  display: grid;
  min-height: 250px;
  place-content: center;
  justify-items: center;
  padding: 36px;
  text-align: center;

  > span { color: var(--bill-muted); }
  h3 { margin-top: 9px; font-size: 20px; }
  p { max-width: 530px; margin: 10px 0 18px; color: var(--bill-muted); font-size: 12px; line-height: 1.6; }
}

.billing-boundary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(260px, .7fr);
  align-items: center;
  gap: 20px;
  margin-top: 18px;
  padding: 19px 22px;
  border: 1px solid var(--bill-ink);
  background: var(--bill-ink);
  color: #fff;

  > span { padding: 7px 9px; border: 1px solid rgba(255, 255, 255, .45); }
  strong { font-size: 13px; }
  div p,
  > p { margin-top: 5px; color: rgba(255, 255, 255, .68); font-size: 11px; line-height: 1.55; }
  > p { margin-top: 0; text-align: right; }
}

@media (max-width: 1200px) {
  .filter-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-actions { justify-content: flex-end; }
  .total-band { grid-template-columns: 1fr; }
  .money-total { border-right: 0; border-bottom: 1px solid var(--bill-rule); }
}

@media (max-width: 880px) {
  .billing-page { padding: 22px 18px 40px; }
  .ledger-hero { align-items: stretch; flex-direction: column; }
  .audit-seal { min-width: 0; }
  .filter-rail,
  .breakdown-grid { grid-template-columns: 1fr; }
  .reconcile-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .reconcile-facts > div:nth-child(2) { border-right: 0; }
  .reconcile-facts > div:nth-child(-n + 2) { border-bottom: 1px solid var(--bill-rule); }
  .operation-list article { grid-template-columns: 1fr; gap: 9px; padding: 14px 0; }
  .operation-amount { text-align: left; }
  .billing-boundary { grid-template-columns: auto 1fr; }
  .billing-boundary > p { grid-column: 1 / -1; text-align: left; }
}

@media (max-width: 560px) {
  .hero-copy h1 { font-size: 40px; }
  .invoice-sheet { grid-template-columns: 28px minmax(0, 1fr); }
  .invoice-heading,
  .receipt-heading { align-items: flex-start; flex-direction: column; }
  .invoice-meta { text-align: left; }
  .reconcile-facts { grid-template-columns: 1fr; }
  .reconcile-facts > div { border-right: 0; border-bottom: 1px solid var(--bill-rule); }
  .reconcile-alert { grid-template-columns: 1fr; }
  .breakdown-list > div { grid-template-columns: 34px minmax(0, 1fr); padding: 12px 0; }
  .breakdown-cost { grid-column: 2; text-align: left; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
</style>
