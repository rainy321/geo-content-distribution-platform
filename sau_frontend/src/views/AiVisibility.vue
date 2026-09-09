<template>
  <main class="visibility-workbench">
    <header class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">AI VISIBILITY / EVIDENCE AUDIT</p>
        <h1>AI 可见度</h1>
        <p>把外部 AI 回答作为样本，检查品牌是否被看见、如何被推荐，以及引用证据来自哪里。</p>
      </div>
      <div class="local-engine-seal" :class="{ unsafe: result?.methodology.externalModelUsed }">
        <span class="seal-pulse" aria-hidden="true" />
        <div>
          <strong>{{ result?.methodology.externalModelUsed ? '检测到外部模型调用' : '本地规则分析' }}</strong>
          <small>{{ result?.methodology.externalModelUsed ? '请核对后端分析路径' : '回答与历史只留在本机' }}</small>
        </div>
      </div>
    </header>

    <section class="analysis-console" aria-labelledby="input-title">
      <div class="signal-rail" aria-hidden="true">
        <span class="rail-node active">1</span>
        <i />
        <span class="rail-node" :class="{ active: sampleCount > 0 }">2</span>
        <i />
        <span class="rail-node" :class="{ active: Boolean(result) }">3</span>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        class="analysis-form"
        @submit.prevent="submitAnalysis"
      >
        <div class="console-heading">
          <div>
            <span id="input-title">建立分析批次</span>
            <h2>问题、品牌与回答样本</h2>
          </div>
          <button type="button" class="text-action" :disabled="projectsLoading" @click="loadProjects">
            <el-icon :class="{ spinning: projectsLoading }"><Refresh /></el-icon>
            刷新项目
          </button>
        </div>

        <div v-if="projectError" class="inline-alert" role="alert">
          <span>!</span>
          <div>
            <strong>品牌项目没有载入</strong>
            <p>{{ projectError }}</p>
          </div>
          <el-button size="small" @click="loadProjects">重新加载</el-button>
        </div>

        <div v-else-if="!projectsLoading && projects.length === 0" class="inline-alert is-empty">
          <span>0</span>
          <div>
            <strong>还没有品牌项目</strong>
            <p>可见度分析必须归属一个项目，先建立品牌底稿。</p>
          </div>
          <el-button size="small" type="primary" @click="router.push('/projects')">建立项目</el-button>
        </div>

        <div class="form-grid">
          <el-form-item label="品牌项目" prop="projectId" class="project-field">
            <el-select
              v-model="form.projectId"
              filterable
              :loading="projectsLoading"
              :disabled="projectsLoading || projects.length === 0"
              placeholder="选择需要监测的品牌"
              @change="handleProjectChange"
            >
              <el-option
                v-for="project in projects"
                :key="project.id"
                :label="project.name"
                :value="project.id"
              >
                <div class="project-option">
                  <strong>{{ project.name }}</strong>
                  <span>{{ [project.product, project.industry].filter(Boolean).join(' · ') || '品牌项目' }}</span>
                </div>
              </el-option>
            </el-select>
          </el-form-item>

          <div class="brand-readout" :class="{ muted: !selectedProject }">
            <span>分析主体</span>
            <strong>{{ selectedProject?.name || '等待选择项目' }}</strong>
            <small>品牌主名来自项目底稿，避免同批次口径漂移</small>
          </div>

          <el-form-item label="监测问题" prop="prompt" class="prompt-field">
            <el-input
              v-model="form.prompt"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              resize="vertical"
              placeholder="例如：适合中小企业的 GEO 内容工具有哪些？"
            />
          </el-form-item>

          <el-form-item label="品牌别名" class="alias-field">
            <el-select
              v-model="form.aliases"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入别名后按 Enter，可添加多个"
            >
              <el-option v-for="alias in form.aliases" :key="alias" :label="alias" :value="alias" />
            </el-select>
            <p class="field-help">用于识别简称、英文名或常见写法；品牌主名始终自动参与匹配。</p>
          </el-form-item>

          <el-form-item label="对比竞品" class="competitor-field">
            <el-select
              v-model="form.competitors"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入竞品后按 Enter，可添加多个"
            >
              <el-option
                v-for="competitor in form.competitors"
                :key="competitor"
                :label="competitor"
                :value="competitor"
              />
            </el-select>
            <p class="field-help">默认读取项目中的竞争品牌，本次分析可增删，不会改动项目底稿。</p>
          </el-form-item>

          <el-form-item label="回答来源（可选）" class="provider-field">
            <el-input
              v-model="form.provider"
              maxlength="80"
              clearable
              placeholder="例如：千问网页、ChatGPT"
            />
            <p class="field-help">应用于本批全部样本，用于以后区分回答采集渠道。</p>
          </el-form-item>

          <el-form-item label="模型标识（可选）" class="model-field">
            <el-input
              v-model="form.model"
              maxlength="120"
              clearable
              placeholder="例如：qwen-plus、GPT-5"
            />
            <p class="field-help">只记录你填写的标签，不会连接或调用该模型。</p>
          </el-form-item>

          <el-form-item class="responses-field" :error="responseError">
            <template #label>
              <div class="answer-label">
                <span>AI 回答样本</span>
                <strong :class="{ invalid: sampleCount > 20 }">{{ sampleCount }} / 20</strong>
              </div>
            </template>
            <el-input
              v-model="form.responsesText"
              type="textarea"
              :rows="12"
              resize="vertical"
              aria-describedby="answer-format answer-privacy"
              :placeholder="'粘贴第一条完整回答\n\n---\n\n粘贴第二条完整回答'"
              @keydown.ctrl.enter.prevent="submitAnalysis"
              @keydown.meta.enter.prevent="submitAnalysis"
            />
            <div class="answer-guidance">
              <p id="answer-format"><kbd>---</kbd> 单独占一行分隔两条回答，回答内部可保留空行。</p>
              <p id="answer-privacy"><span class="privacy-dot" />回答与历史仅保存在本机，不向千问或其他外部模型发送。</p>
            </div>
          </el-form-item>
        </div>

        <footer class="console-footer">
          <div class="save-preference">
            <el-checkbox v-model="form.persist">保存到本机分析历史</el-checkbox>
            <p><strong>样本口径：</strong>每一段分隔后的完整回答计为 1 个样本；样本越少，置信等级越低。</p>
          </div>
          <el-button
            native-type="submit"
            type="primary"
            size="large"
            :loading="analyzing"
            :disabled="!canAnalyze"
          >
            {{ analyzing ? '正在分析证据' : '开始本地分析' }}
            <el-icon v-if="!analyzing"><ArrowRight /></el-icon>
          </el-button>
        </footer>
      </el-form>
    </section>

    <section v-if="form.projectId" class="history-panel" aria-labelledby="history-title">
      <header class="history-heading">
        <div>
          <p class="eyebrow">LOCAL HISTORY / LAST 10 RUNS</p>
          <h2 id="history-title">{{ selectedProject?.name || '当前项目' }}的最近分析</h2>
          <p>历史记录与回答原文只存放在这台设备上，点击记录可恢复完整证据。</p>
        </div>
        <button
          type="button"
          class="text-action"
          :disabled="historyLoading"
          aria-label="刷新最近分析"
          @click="loadHistory(form.projectId)"
        >
          <el-icon :class="{ spinning: historyLoading }"><Refresh /></el-icon>
          刷新历史
        </button>
      </header>

      <div v-if="historyLoading" class="history-loading" aria-live="polite" aria-busy="true">
        <el-skeleton :rows="2" animated />
      </div>
      <div v-else-if="historyError" class="history-state is-error" role="status">
        <span>!</span>
        <div>
          <strong>最近分析暂时不可用</strong>
          <p>{{ historyError }}。这不会影响创建新分析。</p>
        </div>
        <el-button size="small" @click="loadHistory(form.projectId)">重试历史</el-button>
      </div>
      <div v-else-if="historyRuns.length === 0" class="history-state">
        <span>0</span>
        <div>
          <strong>这个项目还没有本机历史</strong>
          <p>完成并保存第一次分析后，记录会显示在这里。</p>
        </div>
      </div>
      <div v-else class="history-runs" aria-label="最近分析记录">
        <button
          v-for="run in historyRuns"
          :key="run.id"
          type="button"
          class="history-run"
          :class="{ active: String(result?.runId) === String(run.id) }"
          :disabled="historyRunLoading === run.id"
          :aria-current="String(result?.runId) === String(run.id) ? 'true' : undefined"
          @click="restoreRun(run)"
        >
          <span class="run-date">{{ run.createdAt ? formatCompactDate(run.createdAt) : '时间未知' }}</span>
          <strong>{{ run.prompt }}</strong>
          <span class="run-metrics">
            <i>提及 {{ formatPercent(run.mentionRate) }}</i>
            <i>SOV {{ formatPercent(run.shareOfVoice) }}</i>
            <i>{{ run.sampleCount }} 样本</i>
          </span>
          <small>{{ historyRunLoading === run.id ? '正在恢复完整证据…' : `${run.confidence.label}置信 · 查看证据 →` }}</small>
        </button>
      </div>
      <p v-if="historyRestoreError" class="history-restore-error" role="alert">{{ historyRestoreError }}</p>
    </section>

    <section v-if="analysisError" class="analysis-error" role="alert">
      <span class="error-symbol">!</span>
      <div>
        <h2>本次分析没有完成</h2>
        <p>{{ analysisError }}</p>
      </div>
      <el-button type="primary" :loading="analyzing" @click="submitAnalysis">使用当前样本重试</el-button>
    </section>

    <section v-else-if="analyzing" class="result-loading" aria-live="polite" aria-busy="true">
      <div class="loading-radar" aria-hidden="true"><i /><i /><i /></div>
      <div>
        <h2>正在建立回答证据图谱</h2>
        <p>逐条匹配品牌、竞品、推荐位置与引用地址。</p>
      </div>
      <el-skeleton :rows="4" animated />
    </section>

    <section v-else-if="result" ref="resultHeading" class="result-suite" tabindex="-1" aria-labelledby="result-title">
      <header class="result-header">
        <div>
          <p class="eyebrow">BATCH RESULT / {{ result.summary.sampleCount }} SAMPLES</p>
          <h2 id="result-title">“{{ result.prompt || form.prompt }}”的可见度证据</h2>
          <p>{{ result.methodology.sampleSemantics || '每条粘贴回答作为一个独立观察样本，结果仅代表当前批次。' }}</p>
        </div>
        <div class="result-meta">
          <span v-if="result.runId">本机记录 #{{ result.runId }}</span>
          <span>{{ result.methodology.engine }}</span>
          <span v-if="result.methodology.version">v{{ result.methodology.version }}</span>
          <time v-if="result.analyzedAt">{{ formatDate(result.analyzedAt) }}</time>
        </div>
      </header>

      <div class="metric-track" aria-label="可见度核心指标">
        <article class="metric-card signal">
          <span>BRAND MENTION</span>
          <strong>{{ formatPercent(result.summary.mentionRate) }}</strong>
          <p>{{ result.summary.mentionedCount }} / {{ result.summary.sampleCount }} 条回答提及品牌</p>
        </article>
        <article class="metric-card">
          <span>SHARE OF VOICE</span>
          <strong>{{ formatPercent(result.summary.shareOfVoice) }}</strong>
          <p>品牌在全部品牌提及中的份额</p>
        </article>
        <article class="metric-card">
          <span>AVG. RECOMMENDATION</span>
          <strong>{{ formatRank(result.summary.averageRank) }}</strong>
          <p>仅统计进入推荐序列的样本</p>
        </article>
        <article class="metric-card">
          <span>CITATION</span>
          <strong>{{ result.summary.citationCount }}</strong>
          <p>{{ formatPercent(result.summary.citationRate) }} 的样本包含可识别引用</p>
        </article>
        <article class="metric-card">
          <span>OWNED CITATION</span>
          <strong>{{ result.summary.ownedCitationCount }}</strong>
          <p>{{ formatPercent(result.summary.ownedCitationRate) }} 的样本引用自有域名</p>
        </article>
        <article class="metric-card confidence-card" :class="`confidence-${result.summary.confidence.level}`">
          <span>CONFIDENCE</span>
          <strong>{{ result.summary.confidence.label }}</strong>
          <p>{{ result.summary.confidence.reason || confidenceHint(result.summary.sampleCount) }}</p>
        </article>
      </div>

      <div v-if="result.warnings.length || result.methodology.externalModelUsed" class="warning-strip" role="status">
        <strong>口径提醒</strong>
        <ul>
          <li v-if="result.methodology.externalModelUsed">后端回执显示使用了外部模型，本批次不符合“本地规则分析”预期。</li>
          <li v-for="warning in result.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </div>

      <details class="methodology-disclosure">
        <summary>
          <span>分析口径与算法溯源</span>
          <small>{{ result.methodology.version || '版本未声明' }}</small>
        </summary>
        <dl>
          <div>
            <dt>分析引擎</dt>
            <dd>{{ result.methodology.engine }}</dd>
          </div>
          <div>
            <dt>样本口径</dt>
            <dd>{{ result.methodology.sampleSemantics }}</dd>
          </div>
          <div>
            <dt>引用口径</dt>
            <dd>{{ result.methodology.citationSemantics || '回执未声明引用口径。' }}</dd>
          </div>
          <div>
            <dt>可比性</dt>
            <dd>{{ result.methodology.metricComparability || '仅建议在相同采样方式与算法版本下比较。' }}</dd>
          </div>
          <div>
            <dt>交接快照 SHA-256</dt>
            <dd><code>{{ result.methodology.sourceTrace?.handoff_archive_sha256 || '未提供' }}</code></dd>
          </div>
        </dl>
      </details>

      <div class="result-grid">
        <article class="result-panel entity-panel">
          <div class="panel-heading">
            <div>
              <span>ENTITY COMPARISON</span>
              <h3>品牌与竞品声量</h3>
            </div>
            <small>{{ result.entities.length }} 个实体</small>
          </div>
          <div v-if="result.entities.length" class="entity-table-wrap">
            <table class="entity-table">
              <thead>
                <tr>
                  <th scope="col">实体</th>
                  <th scope="col">提及</th>
                  <th scope="col">提及率</th>
                  <th scope="col">SOV</th>
                  <th scope="col">平均位次</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="entity in result.entities" :key="entity.id">
                  <th scope="row">
                    <span class="entity-mark" :class="{ brand: isBrandEntity(entity) }" />
                    <div><strong>{{ entity.name }}</strong><small>{{ isBrandEntity(entity) ? '目标品牌' : '对比实体' }}</small></div>
                  </th>
                  <td>{{ entity.mentions }}</td>
                  <td>{{ formatPercent(entity.mentionRate) }}</td>
                  <td>
                    <span>{{ formatPercent(entity.shareOfVoice) }}</span>
                    <i class="sov-track"><b :style="{ width: `${boundedPercent(entity.shareOfVoice)}%` }" /></i>
                  </td>
                  <td>{{ formatRank(entity.averageRank) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="panel-empty">
            <span>—</span>
            <p>当前回执没有实体对比数据。样本证据仍可逐条核查。</p>
          </div>
        </article>

        <article class="result-panel recommendation-panel">
          <div class="panel-heading inverse">
            <div>
              <span>NEXT ACTIONS</span>
              <h3>可执行建议</h3>
            </div>
          </div>
          <ol v-if="result.recommendations.length" class="recommendation-list">
            <li v-for="(recommendation, index) in result.recommendations" :key="recommendation">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <p>{{ recommendation }}</p>
            </li>
          </ol>
          <div v-else class="recommendation-empty">
            <strong>本批次没有额外建议</strong>
            <p>可先扩充样本，再比较不同问题下的品牌表现。</p>
          </div>
        </article>
      </div>

      <article class="result-panel samples-panel">
        <div class="panel-heading">
          <div>
            <span>SAMPLE EVIDENCE</span>
            <h3>逐样本证据</h3>
            <p>展开样本核对规则命中位置；引文只展示原文片段，不做生成式解释。</p>
          </div>
          <small>{{ result.samples.length }} 条回执</small>
        </div>

        <el-collapse v-if="result.samples.length" class="sample-collapse">
          <el-collapse-item v-for="sample in result.samples" :key="sample.id" :name="sample.id">
            <template #title>
              <div class="sample-title">
                <span class="sample-index">{{ String(sample.index).padStart(2, '0') }}</span>
                <span class="mention-state" :class="{ found: sample.brandMentioned }">
                  {{ sample.brandMentioned ? '已提及品牌' : '未提及品牌' }}
                </span>
                <span>{{ formatRank(sample.recommendationRank) }}</span>
                <span>{{ sample.evidence.length }} 条证据</span>
                <small v-if="sample.provider || sample.model">{{ [sample.provider, sample.model].filter(Boolean).join(' / ') }}</small>
              </div>
            </template>

            <div class="sample-body">
              <div class="sample-facts">
                <div>
                  <span>匹配别名</span>
                  <p>{{ sample.matchedAliases.join('、') || '没有命中别名' }}</p>
                </div>
                <div>
                  <span>竞品提及</span>
                  <p>{{ competitorSummary(sample.competitorMentions) }}</p>
                </div>
                <div>
                  <span>引用</span>
                  <p>{{ sample.citations.length }} 条</p>
                </div>
              </div>

              <div v-if="sample.evidence.length" class="evidence-list">
                <blockquote v-for="evidence in sample.evidence" :key="evidence.id">
                  <span>{{ evidenceTypeLabel(evidence.type) }}</span>
                  <p>“{{ evidence.quote }}”</p>
                </blockquote>
              </div>
              <div v-else class="sample-empty">此样本没有返回可定位的品牌证据。</div>

              <details v-if="sample.text" class="source-response">
                <summary>查看完整回答原文</summary>
                <p>{{ sample.text }}</p>
              </details>

              <ul v-if="sample.warnings.length" class="sample-warnings">
                <li v-for="warning in sample.warnings" :key="warning">{{ warning }}</li>
              </ul>
            </div>
          </el-collapse-item>
        </el-collapse>
        <div v-else class="panel-empty large">
          <span>0</span>
          <p>后端没有返回逐样本明细，请依据批次警告核对分析配置。</p>
        </div>
      </article>

      <article class="result-panel citation-panel">
        <div class="panel-heading">
          <div>
            <span>NORMALIZED CITATIONS</span>
            <h3>规范化引用</h3>
            <p>相同地址合并计数；仅 http / https 地址可打开。</p>
          </div>
          <small>{{ result.citations.length }} 个规范地址</small>
        </div>
        <div v-if="result.citations.length" class="citation-list">
          <div v-for="citation in result.citations" :key="citation.id" class="citation-row">
            <span class="ownership-mark" :class="{ owned: citation.isOwned, competitor: citation.isCompetitor }">
              {{ citation.isOwned ? '自有' : citation.isCompetitor ? '竞品' : '外部' }}
            </span>
            <div>
              <a
                v-if="citation.url"
                :href="citation.url"
                target="_blank"
                rel="noopener noreferrer"
              >{{ citation.domain || citation.url }} <span aria-hidden="true">↗</span></a>
              <strong v-else>{{ citation.domain || citation.raw || '无法识别的引用' }}</strong>
              <small>{{ citation.raw && citation.raw !== citation.url ? citation.raw : citation.url }}</small>
            </div>
            <span>{{ citation.count }} 次</span>
          </div>
        </div>
        <div v-else class="panel-empty">
          <span>∅</span>
          <p>当前样本没有提取到可规范化引用。</p>
        </div>
      </article>
    </section>

    <section v-else class="result-empty">
      <div class="empty-orbit" aria-hidden="true"><i /><span>G</span></div>
      <div>
        <p class="eyebrow">WAITING FOR EVIDENCE</p>
        <h2>结果将从证据开始，而不是从一个分数开始</h2>
        <p>选择项目并粘贴 1–20 条回答。分析完成后，可从汇总指标一路回看至每条原始证据。</p>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Refresh } from '@element-plus/icons-vue'

import { projectApi } from '@/api/project'
import { visibilityApi } from '@/api/visibility'
import {
  buildVisibilityPayload,
  normalizeVisibilityHistory,
  normalizeVisibilityResult,
  parseVisibilityResponses
} from '@/utils/visibilityResult'

const router = useRouter()
const formRef = ref(null)
const resultHeading = ref(null)
const projects = ref([])
const projectsLoading = ref(false)
const projectError = ref('')
const analyzing = ref(false)
const analysisError = ref('')
const result = ref(null)
const historyRuns = ref([])
const historyLoading = ref(false)
const historyError = ref('')
const historyRestoreError = ref('')
const historyRunLoading = ref(null)
let historyRequestSequence = 0

const form = reactive({
  projectId: '',
  prompt: '',
  aliases: [],
  competitors: [],
  provider: '',
  model: '',
  persist: true,
  responsesText: ''
})

const formRules = {
  projectId: [{ required: true, message: '请选择品牌项目', trigger: 'change' }],
  prompt: [
    { required: true, message: '请输入监测问题', trigger: 'blur' },
    { min: 2, max: 500, message: '监测问题需为 2–500 个字符', trigger: 'blur' }
  ]
}

const selectedProject = computed(() => (
  projects.value.find(project => String(project.id) === String(form.projectId)) || null
))
const parsedResponses = computed(() => parseVisibilityResponses(form.responsesText))
const sampleCount = computed(() => parsedResponses.value.length)
const responseError = computed(() => {
  if (!form.responsesText.trim()) return ''
  if (sampleCount.value === 0) return '至少需要 1 条完整回答。'
  if (sampleCount.value > 20) return `当前识别到 ${sampleCount.value} 条回答，最多分析 20 条。`
  return ''
})
const canAnalyze = computed(() => (
  !analyzing.value &&
  Boolean(form.projectId) &&
  form.prompt.trim().length >= 2 &&
  sampleCount.value >= 1 &&
  sampleCount.value <= 20
))

const normalizeStringList = value => {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map(item => typeof item === 'string' ? item.trim() : item?.name?.trim()).filter(Boolean))]
}

const applyProjectDefaults = () => {
  const project = selectedProject.value
  form.aliases = normalizeStringList(project?.aliases || project?.brand_aliases)
  form.competitors = normalizeStringList(project?.competitors)
}

const handleProjectChange = () => {
  applyProjectDefaults()
  result.value = null
  analysisError.value = ''
  historyRestoreError.value = ''
  void loadHistory(form.projectId)
}

const loadHistory = async (projectId) => {
  const requestSequence = ++historyRequestSequence
  historyError.value = ''
  historyRestoreError.value = ''
  if (!projectId) {
    historyRuns.value = []
    historyLoading.value = false
    return
  }

  historyLoading.value = true
  try {
    const response = await visibilityApi.getRuns(projectId, 10, { suppressGlobalError: true })
    if (requestSequence !== historyRequestSequence || String(form.projectId) !== String(projectId)) return
    historyRuns.value = normalizeVisibilityHistory(response)
  } catch (error) {
    if (requestSequence !== historyRequestSequence || String(form.projectId) !== String(projectId)) return
    historyRuns.value = []
    historyError.value = readableError(error)
  } finally {
    if (requestSequence === historyRequestSequence) historyLoading.value = false
  }
}

const loadProjects = async () => {
  projectsLoading.value = true
  projectError.value = ''
  try {
    const response = await projectApi.getProjects()
    projects.value = Array.isArray(response.data) ? response.data : []
    if (form.projectId && !selectedProject.value) form.projectId = ''
    if (!form.projectId && projects.value.length === 1) {
      form.projectId = projects.value[0].id
      applyProjectDefaults()
    }
    if (form.projectId) void loadHistory(form.projectId)
  } catch (error) {
    projectError.value = error?.response?.data?.msg || error?.message || '检查后端服务后重试。'
  } finally {
    projectsLoading.value = false
  }
}

const readableError = error => {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(item => item.msg).filter(Boolean).join('；')
  return error?.response?.data?.msg || error?.response?.data?.message || detail || error?.message || '检查输入与后端服务后重试。'
}

const revealResult = async () => {
  await nextTick()
  resultHeading.value?.focus({ preventScroll: true })
  resultHeading.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const restoreRun = async (run) => {
  if (historyRunLoading.value != null) return
  const expectedProjectId = String(form.projectId)
  historyRunLoading.value = run.id
  historyRestoreError.value = ''
  try {
    const response = await visibilityApi.getRun(run.id, { suppressGlobalError: true })
    if (String(form.projectId) !== expectedProjectId) return
    const restored = normalizeVisibilityResult(response)
    if (restored.runId == null) restored.runId = run.id
    if (!restored.analyzedAt) restored.analyzedAt = run.createdAt
    result.value = restored
    analysisError.value = ''
    await revealResult()
  } catch (error) {
    if (String(form.projectId) === expectedProjectId) {
      historyRestoreError.value = `无法恢复记录 #${run.id}：${readableError(error)}`
    }
  } finally {
    historyRunLoading.value = null
  }
}

const submitAnalysis = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || !canAnalyze.value) {
    if (!form.responsesText.trim()) ElMessage.warning('请粘贴至少 1 条 AI 回答。')
    else if (sampleCount.value > 20) ElMessage.warning('单次最多分析 20 条回答。')
    return
  }

  analyzing.value = true
  analysisError.value = ''
  result.value = null
  try {
    const payload = buildVisibilityPayload({
      projectId: form.projectId,
      prompt: form.prompt,
      responses: parsedResponses.value,
      aliases: form.aliases,
      competitors: form.competitors,
      provider: form.provider,
      model: form.model,
      persist: form.persist
    })
    const response = await visibilityApi.analyze(payload, { suppressGlobalError: true })
    result.value = normalizeVisibilityResult(response)
    ElMessage.success(
      form.persist
        ? `已分析 ${result.value.summary.sampleCount} 条回答并保存到本机。`
        : `已完成 ${result.value.summary.sampleCount} 条回答的临时分析。`
    )
    if (form.persist) void loadHistory(form.projectId)
    await revealResult()
  } catch (error) {
    analysisError.value = readableError(error)
  } finally {
    analyzing.value = false
  }
}

const formatPercent = value => {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number)}%`
}

const boundedPercent = value => {
  const number = Number(value)
  return Number.isFinite(number) ? Math.min(100, Math.max(0, number)) : 0
}

const formatRank = value => {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return '未进入'
  return `第 ${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number)} 位`
}

const formatDate = value => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(date)
}

const formatCompactDate = value => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  }).format(date)
}

const confidenceHint = count => count >= 10 ? '样本数量支持本批次比较。' : '样本较少，建议继续补充回答。'
const isBrandEntity = entity => ['brand', 'target', 'primary', 'self'].includes(String(entity.type).toLowerCase())
const evidenceTypeLabel = type => ({
  MENTION: '实体提及',
  RANK: '推荐位次',
  CITATION: '引用证据',
  ALIAS: '别名命中'
}[String(type).toUpperCase()] || type)
const competitorSummary = mentions => {
  if (!mentions.length) return '没有识别到竞品'
  return mentions.map(item => `${item.name}${item.mentions > 1 ? ` × ${item.mentions}` : ''}`).join('、')
}

onMounted(loadProjects)
</script>

<style lang="scss" scoped>
.visibility-workbench {
  --harbor: #102b31;
  --harbor-soft: #193a41;
  --signal: #39b8b2;
  --signal-soft: #e5f6f3;
  --ink: #173038;
  --muted: #6d7d82;
  --rule: #d5dfdc;
  --paper: #ffffff;
  --amber: #bd7d2c;
  --red: #b85151;
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

.page-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  padding: 8px 4px 22px;
  border-bottom: 1px solid #cad6d2;
}

.heading-copy {
  max-width: 720px;
  .eyebrow { margin: 0 0 9px; color: #318f8b; font: 700 11px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.13em; }
  h1 { margin: 0; color: var(--harbor); font: 700 clamp(36px, 5vw, 56px)/0.96 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; letter-spacing: -0.04em; }
  > p:last-child { margin: 14px 0 0; color: var(--muted); font-size: 14px; line-height: 1.75; }
}

.local-engine-seal {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 250px;
  padding: 14px 16px;
  border: 1px solid #a9d4cf;
  background: rgba(229, 246, 243, 0.55);
  .seal-pulse { width: 10px; height: 10px; flex: 0 0 auto; border: 2px solid #fff; border-radius: 50%; background: var(--signal); box-shadow: 0 0 0 4px rgba(57, 184, 178, 0.16); }
  div { display: flex; flex-direction: column; gap: 4px; }
  strong { font-size: 12px; }
  small { color: var(--muted); font-size: 9px; }
  &.unsafe { border-color: #dcb7b5; background: #fbefee; .seal-pulse { background: var(--red); box-shadow: 0 0 0 4px rgba(184, 81, 81, 0.13); } }
}

.analysis-console {
  position: relative;
  display: grid;
  grid-template-columns: 54px 1fr;
  margin-top: 22px;
  border: 1px solid var(--rule);
  background: var(--paper);
  box-shadow: 0 16px 42px rgba(16, 43, 49, 0.055);
}

.signal-rail {
  display: flex;
  align-items: center;
  flex-direction: column;
  padding: 28px 0;
  border-right: 1px solid var(--rule);
  background: #f1f6f4;
  i { width: 1px; min-height: 96px; flex: 1; background: #c7d8d5; }
}

.rail-node {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border: 1px solid #9eb7b3;
  border-radius: 50%;
  background: #fff;
  color: #78918e;
  font: 700 9px/1 "Cascadia Mono", monospace;
  transition: color 180ms ease, background 180ms ease, border-color 180ms ease;
  &.active { border-color: var(--signal); background: var(--signal); color: #09272b; }
}

.analysis-form { min-width: 0; padding: 27px 30px 0; }
.console-heading, .panel-heading, .result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}
.console-heading {
  padding-bottom: 22px;
  border-bottom: 1px solid var(--rule);
  > div > span { color: #318f8b; font: 700 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 8px 0 0; color: var(--harbor); font: 700 24px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
}
.text-action {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 0;
  border: 0;
  background: transparent;
  color: #3e7774;
  cursor: pointer;
  font-size: 11px;
  &:disabled { cursor: not-allowed; opacity: 0.5; }
}
.spinning { animation: spin 800ms linear infinite; }

.inline-alert {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 13px;
  align-items: center;
  margin-top: 18px;
  padding: 13px 14px;
  border: 1px solid #dec0bd;
  background: #fff8f7;
  > span { display: grid; width: 30px; height: 30px; place-items: center; border: 1px solid var(--red); color: var(--red); font: 700 12px/1 "Cascadia Mono", monospace; }
  strong { font-size: 12px; }
  p { margin: 3px 0 0; color: var(--muted); font-size: 10px; }
  &.is-empty { border-color: var(--rule); background: #f7faf9; > span { border-color: #89aaa6; color: #57827f; } }
}

.form-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 0.7fr);
  gap: 20px 26px;
  padding-top: 24px;
  :deep(.el-form-item) { margin-bottom: 0; }
  :deep(.el-form-item__label) { padding-bottom: 8px; color: var(--ink); font-size: 11px; font-weight: 700; }
  :deep(.el-select), :deep(.el-input), :deep(.el-textarea) { width: 100%; }
  :deep(.el-select__wrapper), :deep(.el-input__wrapper), :deep(.el-textarea__inner) { border-radius: 3px; box-shadow: 0 0 0 1px #cdd8d5 inset; }
  :deep(.el-select__wrapper.is-focused), :deep(.el-input__wrapper.is-focus), :deep(.el-textarea__inner:focus) { box-shadow: 0 0 0 2px rgba(57, 184, 178, 0.35) inset; }
}
.project-field { align-self: center; }
.project-option { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; strong { color: var(--ink); font-size: 12px; } span { color: var(--muted); font-size: 9px; } }
.brand-readout {
  position: relative;
  display: flex;
  min-height: 58px;
  flex-direction: column;
  justify-content: center;
  padding-left: 18px;
  border-left: 3px solid var(--signal);
  background: #f5f9f8;
  > span { color: #4f8582; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
  strong { margin-top: 5px; font-size: 14px; }
  small { margin-top: 3px; color: var(--muted); font-size: 9px; }
  &.muted { border-left-color: #bfcac8; }
}
.prompt-field, .responses-field { grid-column: 1 / -1; }
.field-help { margin: 7px 0 0; color: var(--muted); font-size: 9px; line-height: 1.5; }
.answer-label { display: flex; width: 100%; align-items: center; justify-content: space-between; strong { color: #4c8582; font: 700 10px/1 "Cascadia Mono", monospace; } strong.invalid { color: var(--red); } }
.responses-field :deep(.el-textarea__inner) { min-height: 230px !important; padding: 15px 16px; color: #233b42; font: 12px/1.75 "Cascadia Mono", "Microsoft YaHei", monospace; }
.answer-guidance { display: flex; width: 100%; justify-content: space-between; gap: 20px; p { margin: 8px 0 0; color: var(--muted); font-size: 9px; } kbd { padding: 2px 5px; border: 1px solid #b8c8c5; background: #f1f6f4; color: #3b7773; font: 700 9px/1 "Cascadia Mono", monospace; } }
.privacy-dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: var(--signal); }

.console-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-top: 26px;
  padding: 19px 0 22px;
  border-top: 1px solid var(--rule);
  p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
  p strong { color: var(--ink); }
  :deep(.el-button) { min-width: 175px; border-radius: 3px; background: var(--harbor); border-color: var(--harbor); }
  :deep(.el-button:not(.is-disabled):hover) { background: var(--harbor-soft); border-color: var(--harbor-soft); }
}
.save-preference {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
  :deep(.el-checkbox) { width: fit-content; height: auto; color: var(--ink); }
  :deep(.el-checkbox__label) { font-size: 11px; font-weight: 700; }
  :deep(.el-checkbox__input.is-checked .el-checkbox__inner) { border-color: var(--signal); background: var(--signal); }
  :deep(.el-checkbox__input.is-checked + .el-checkbox__label) { color: var(--ink); }
}

.history-panel {
  margin-top: 22px;
  padding: 22px 26px 24px;
  border: 1px solid var(--rule);
  background: #fff;
}
.history-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--rule);
  .eyebrow { margin: 0; color: #318f8b; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 8px 0 0; color: var(--harbor); font: 700 20px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p:last-child { margin: 7px 0 0; color: var(--muted); font-size: 9px; }
}
.history-loading { padding: 20px 0 0; }
.history-state {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 13px;
  align-items: center;
  min-height: 72px;
  padding-top: 16px;
  > span { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid #9eb7b3; color: #5a8581; font: 700 9px/1 "Cascadia Mono", monospace; }
  strong { font-size: 11px; }
  p { margin: 4px 0 0; color: var(--muted); font-size: 9px; }
  &.is-error > span { border-color: var(--amber); color: #9a6721; }
}
.history-runs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
  gap: 10px;
  padding-top: 16px;
}
.history-run {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 126px;
  flex-direction: column;
  padding: 14px 15px;
  border: 1px solid #d9e2e0;
  background: #fbfcfc;
  color: var(--ink);
  cursor: pointer;
  text-align: left;
  transition: border-color 160ms ease, transform 160ms ease, background 160ms ease;
  &::after { position: absolute; right: 15px; bottom: 0; left: 15px; height: 2px; background: #9fb4b1; content: ''; }
  &:hover:not(:disabled) { border-color: #75bcb7; background: #fff; transform: translateY(-2px); }
  &.active { border-color: #64b8b2; background: #f1faf8; &::after { background: var(--signal); } }
  &:disabled { cursor: wait; opacity: 0.65; }
  .run-date { color: #568783; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.06em; }
  > strong { display: -webkit-box; min-height: 34px; overflow: hidden; margin-top: 9px; font-size: 11px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  > small { margin-top: auto; color: #66817e; font-size: 8px; }
}
.run-metrics { display: flex; gap: 5px 9px; flex-wrap: wrap; margin: 10px 0; i { color: var(--muted); font: 600 8px/1 "Cascadia Mono", monospace; font-style: normal; } }
.history-restore-error { margin: 13px 0 0; padding: 9px 11px; border-left: 3px solid var(--red); background: #fff5f4; color: #8e4a45; font-size: 9px; line-height: 1.5; }

.analysis-error, .result-loading, .result-empty {
  display: flex;
  align-items: center;
  gap: 22px;
  min-height: 210px;
  margin-top: 22px;
  padding: 30px;
  border: 1px solid var(--rule);
  background: #fff;
  h2 { margin: 0; color: var(--harbor); font-size: 18px; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 11px; line-height: 1.65; }
}
.analysis-error { border-color: #dfc4c3; .error-symbol { display: grid; width: 48px; height: 48px; flex: 0 0 auto; place-items: center; border: 1px solid var(--red); color: var(--red); font: 700 20px/1 "Cascadia Mono", monospace; } > div { flex: 1; } }
.result-loading { display: grid; grid-template-columns: auto minmax(240px, 0.65fr) 1fr; }
.loading-radar { position: relative; width: 62px; height: 62px; border: 1px solid #9bc9c5; border-radius: 50%; i { position: absolute; inset: 8px; border: 1px solid #bddbd8; border-radius: 50%; } i:nth-child(2) { inset: 19px; background: var(--signal); border: 0; } i:nth-child(3) { inset: 30px 7px 30px 30px; border: 0; border-top: 1px solid var(--signal); border-radius: 0; transform-origin: left; animation: radar 1.4s linear infinite; } }

.result-suite { margin-top: 22px; outline: 0; scroll-margin-top: 20px; }
.result-header {
  padding: 26px 28px;
  background: var(--harbor);
  color: #eaf5f3;
  .eyebrow { margin: 0; color: #78d7d0; font: 700 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.13em; }
  h2 { margin: 10px 0 0; max-width: 760px; color: #f5fbfa; font: 700 clamp(23px, 3vw, 33px)/1.15 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { margin: 10px 0 0; color: rgba(226, 242, 239, 0.62); font-size: 10px; }
}
.result-meta { display: flex; align-items: flex-end; flex-direction: column; gap: 7px; color: rgba(226, 242, 239, 0.55); font: 600 9px/1.2 "Cascadia Mono", monospace; span:first-child { color: #8ee1da; } }

.metric-track { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border: 1px solid var(--rule); border-top: 0; background: #fff; }
.metric-card {
  position: relative;
  min-height: 132px;
  padding: 20px 18px;
  border-right: 1px solid var(--rule);
  &:last-child { border-right: 0; }
  &::after { position: absolute; right: 18px; bottom: 0; left: 18px; height: 3px; background: #90aaa7; content: ''; }
  &.signal::after { background: var(--signal); }
  > span { color: #66817e; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  strong { display: block; margin-top: 18px; color: var(--harbor); font: 700 clamp(24px, 2.7vw, 35px)/0.9 "Bahnschrift SemiCondensed", sans-serif; }
  p { margin: 12px 0 0; color: var(--muted); font-size: 9px; line-height: 1.5; }
}
.confidence-card.confidence-high::after { background: var(--signal); }
.confidence-card.confidence-medium::after { background: var(--amber); }
.confidence-card.confidence-low::after, .confidence-card.confidence-unknown::after { background: var(--red); }

.methodology-disclosure {
  margin-top: 14px;
  border: 1px solid var(--rule);
  background: rgba(255, 255, 255, 0.72);
  summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 16px;
    color: var(--harbor);
    cursor: pointer;
    font-size: 12px;
    font-weight: 700;
  }
  summary small { color: #568582; font: 700 9px/1 "Cascadia Mono", monospace; }
  dl { display: grid; gap: 0; margin: 0; padding: 0 16px 16px; }
  dl > div { display: grid; grid-template-columns: 126px 1fr; gap: 16px; padding: 10px 0; border-top: 1px solid #e3e9e7; }
  dt { color: #668083; font-size: 10px; }
  dd { min-width: 0; margin: 0; color: var(--ink); font-size: 10px; line-height: 1.65; overflow-wrap: anywhere; }
  code { font: 600 9px/1.5 "Cascadia Mono", monospace; }
}

.warning-strip { display: grid; grid-template-columns: 120px 1fr; gap: 20px; padding: 15px 20px; border: 1px solid #ddc69e; border-top: 0; background: #fff9ee; strong { color: #8a5f22; font-size: 11px; } ul { margin: 0; padding-left: 17px; color: #755f3e; font-size: 10px; line-height: 1.65; } }
.result-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.65fr); gap: 22px; margin-top: 22px; }
.result-panel { border: 1px solid var(--rule); background: #fff; }
.entity-panel, .recommendation-panel, .samples-panel, .citation-panel { padding: 25px 27px; }
.panel-heading {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--rule);
  > div > span { color: #318f8b; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h3 { margin: 8px 0 0; color: var(--harbor); font: 700 21px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 9px; line-height: 1.5; }
  > small { color: var(--muted); font: 600 9px/1 "Cascadia Mono", monospace; }
}
.entity-table-wrap { overflow-x: auto; }
.entity-table { width: 100%; border-collapse: collapse; th, td { padding: 15px 10px; border-bottom: 1px solid #e4eae8; color: var(--muted); font-size: 10px; text-align: left; white-space: nowrap; } thead th { color: #758885; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; } tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; } tbody th { display: flex; align-items: center; gap: 9px; color: var(--ink); } tbody th div { display: flex; flex-direction: column; gap: 3px; } tbody th small { color: var(--muted); font-size: 8px; font-weight: 400; } }
.entity-mark { width: 8px; height: 22px; background: #9aafac; &.brand { background: var(--signal); } }
.sov-track { display: block; width: 76px; height: 3px; margin-top: 6px; background: #e3eae8; b { display: block; height: 100%; background: var(--signal); } }

.recommendation-panel { display: flex; flex-direction: column; min-height: 320px; border-color: var(--harbor); background: var(--harbor); color: #ecf7f5; }
.panel-heading.inverse { border-color: rgba(255,255,255,0.13); > div > span { color: #78d7d0; } h3 { color: #f3f9f8; } }
.recommendation-list { margin: 8px 0 0; padding: 0; list-style: none; li { display: grid; grid-template-columns: 28px 1fr; gap: 11px; padding: 14px 0; border-bottom: 1px solid rgba(255,255,255,0.1); } span { color: #76d3cd; font: 700 9px/1.6 "Cascadia Mono", monospace; } p { margin: 0; color: rgba(232,246,244,0.75); font-size: 10px; line-height: 1.65; } }
.recommendation-empty { margin: auto 0; strong { font-size: 13px; } p { color: rgba(232,246,244,0.58); } }

.samples-panel, .citation-panel { margin-top: 22px; }
.sample-collapse { border: 0; :deep(.el-collapse-item__header) { min-height: 59px; height: auto; border-bottom-color: #e0e8e6; color: var(--ink); } :deep(.el-collapse-item__wrap) { border-bottom-color: #d7e1df; } :deep(.el-collapse-item__content) { padding: 0 0 20px; } }
.sample-title { display: grid; width: calc(100% - 24px); grid-template-columns: 40px 112px 105px 90px 1fr; gap: 12px; align-items: center; font-size: 10px; > span:not(.sample-index):not(.mention-state) { color: var(--muted); } > small { color: #81918f; font: 600 8px/1 "Cascadia Mono", monospace; text-align: right; } }
.sample-index { color: #538a86; font: 700 9px/1 "Cascadia Mono", monospace; }
.mention-state { display: inline-flex; width: fit-content; align-items: center; gap: 6px; color: #8c5653; &::before { width: 14px; height: 3px; background: var(--red); content: ''; } &.found { color: #34756f; &::before { background: var(--signal); } } }
.sample-body { padding: 18px 18px 0 40px; }
.sample-facts { display: grid; grid-template-columns: 1fr 1fr 120px; gap: 12px; padding: 13px 15px; background: #f4f8f7; div { min-width: 0; } span { color: #66827f; font: 700 8px/1 "Cascadia Mono", monospace; } p { margin: 5px 0 0; overflow-wrap: anywhere; font-size: 10px; } }
.evidence-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 13px; blockquote { margin: 0; padding: 13px 15px; border-left: 3px solid var(--signal); background: #fbfcfc; } span { color: #4f8582; font: 700 8px/1 "Cascadia Mono", monospace; text-transform: uppercase; } p { margin: 8px 0 0; color: #40575d; font-size: 10px; line-height: 1.7; } }
.sample-empty { margin-top: 13px; padding: 16px; border: 1px dashed #cbd7d5; color: var(--muted); font-size: 10px; }
.source-response { margin-top: 13px; border: 1px solid #dbe4e2; summary { padding: 11px 13px; color: #437b77; cursor: pointer; font-size: 10px; font-weight: 700; } > p { max-height: 260px; overflow: auto; margin: 0; padding: 14px; border-top: 1px solid #e3eae8; color: #52666b; font-size: 10px; line-height: 1.75; white-space: pre-wrap; } }
.sample-warnings { margin: 12px 0 0; padding: 10px 12px 10px 28px; background: #fff8ed; color: #856331; font-size: 9px; line-height: 1.6; }

.citation-list { margin-top: 3px; }
.citation-row { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; gap: 14px; align-items: center; padding: 14px 0; border-bottom: 1px solid #e4eae8; &:last-child { border-bottom: 0; } > div { display: flex; min-width: 0; flex-direction: column; gap: 4px; } a, strong { overflow: hidden; color: #255f5c; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; } a:hover { color: #16867f; } small { overflow: hidden; color: var(--muted); font: 8px/1.3 "Cascadia Mono", monospace; text-overflow: ellipsis; white-space: nowrap; } > span:last-child { color: var(--muted); font: 700 9px/1 "Cascadia Mono", monospace; } }
.ownership-mark { padding: 5px 4px; border: 1px solid #b8c4c2; color: #647572; font: 700 8px/1 "Cascadia Mono", monospace; text-align: center; &.owned { border-color: #70bbb6; background: var(--signal-soft); color: #27716c; } &.competitor { border-color: #d9bd8f; background: #fff8ec; color: #946829; } }
.panel-empty { display: flex; min-height: 150px; align-items: center; justify-content: center; gap: 15px; color: var(--muted); > span { display: grid; width: 38px; height: 38px; place-items: center; border: 1px solid #a9bab7; font: 700 11px/1 "Cascadia Mono", monospace; } p { max-width: 330px; font-size: 10px; line-height: 1.6; } &.large { min-height: 220px; } }

.result-empty { justify-content: center; min-height: 260px; .eyebrow { margin: 0 0 9px; color: #4e8884; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; } h2 { max-width: 560px; font: 700 21px/1.2 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; } > div:last-child > p:last-child { max-width: 590px; } }
.empty-orbit { position: relative; display: grid; width: 82px; height: 82px; flex: 0 0 auto; place-items: center; border: 1px solid #b8d4d0; border-radius: 50%; i { position: absolute; inset: 10px; border: 1px dashed #83bbb7; border-radius: 50%; } span { display: grid; width: 26px; height: 26px; place-items: center; background: var(--harbor); color: #fff; font: 700 11px/1 "Cascadia Mono", monospace; } }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes radar { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .spinning, .loading-radar i:nth-child(3) { animation: none; }
  .rail-node, .history-run { transition: none; }
}

@media (max-width: 1180px) {
  .metric-track { grid-template-columns: repeat(3, 1fr); }
  .metric-card:nth-child(3) { border-right: 0; }
  .metric-card:nth-child(-n + 3) { border-bottom: 1px solid var(--rule); }
}

@media (max-width: 900px) {
  .page-heading { align-items: flex-start; flex-direction: column; }
  .local-engine-seal { width: 100%; box-sizing: border-box; }
  .result-grid { grid-template-columns: 1fr; }
  .result-loading { grid-template-columns: auto 1fr; }
  .result-loading :deep(.el-skeleton) { grid-column: 1 / -1; }
}

@media (max-width: 760px) {
  .analysis-console { grid-template-columns: 36px minmax(0, 1fr); }
  .signal-rail { padding-top: 24px; i { min-height: 150px; } }
  .rail-node { width: 20px; height: 20px; }
  .analysis-form { padding: 22px 16px 0; }
  .form-grid { grid-template-columns: 1fr; gap: 18px; }
  .prompt-field, .responses-field { grid-column: auto; }
  .answer-guidance, .console-footer { align-items: flex-start; flex-direction: column; }
  .console-footer :deep(.el-button) { width: 100%; }
  .history-panel { padding: 20px 16px; }
  .history-heading { flex-direction: column; }
  .history-runs { grid-template-columns: 1fr; }
  .history-state { grid-template-columns: auto 1fr; }
  .history-state :deep(.el-button) { grid-column: 2; justify-self: start; }
  .inline-alert { grid-template-columns: auto 1fr; }
  .inline-alert :deep(.el-button) { grid-column: 2; justify-self: start; }
  .analysis-error, .result-loading, .result-empty { align-items: flex-start; flex-direction: column; padding: 22px; }
  .result-loading { display: flex; }
  .result-header { flex-direction: column; padding: 22px; }
  .result-meta { align-items: flex-start; }
  .metric-track { grid-template-columns: repeat(2, 1fr); }
  .metric-card, .metric-card:nth-child(3) { border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
  .metric-card:nth-child(even) { border-right: 0; }
  .metric-card:nth-last-child(-n + 2) { border-bottom: 0; }
  .warning-strip { grid-template-columns: 1fr; gap: 7px; }
  .entity-panel, .recommendation-panel, .samples-panel, .citation-panel { padding: 20px 16px; }
  .sample-title { grid-template-columns: 31px 1fr 85px; gap: 8px; }
  .sample-title > span:nth-child(4), .sample-title > small { display: none; }
  .sample-body { padding-left: 0; }
  .sample-facts, .evidence-list { grid-template-columns: 1fr; }
  .citation-row { grid-template-columns: 46px minmax(0, 1fr); }
  .citation-row > span:last-child { grid-column: 2; }
}

@media (max-width: 460px) {
  .metric-track { grid-template-columns: 1fr; }
  .metric-card, .metric-card:nth-child(3), .metric-card:nth-child(even), .metric-card:nth-last-child(-n + 2) { border-right: 0; border-bottom: 1px solid var(--rule); }
  .metric-card:last-child { border-bottom: 0; }
  .console-heading { align-items: flex-start; flex-direction: column; }
}
</style>
