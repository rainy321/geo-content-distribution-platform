<template>
  <main class="visibility-workbench">
    <header class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">AI VISIBILITY / EVIDENCE AUDIT</p>
        <h1>诊断品牌，<br>找到被引用的缺口。</h1>
        <p>把外部 AI 回答作为样本，量化品牌是否被看见、如何被推荐，以及下一步先优化什么。</p>
      </div>
      <div class="local-engine-seal" :class="{ unsafe: result?.methodology.externalModelUsed }">
        <span class="seal-pulse" aria-hidden="true" />
        <div>
          <strong>{{ result?.methodology.externalModelUsed ? '真实回答 · 本地量化' : '本地规则分析' }}</strong>
          <small>{{ result?.methodology.externalModelUsed ? '仅 prompt 外发，指标在本机计算' : '回答与历史只留在本机' }}</small>
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

          <section class="live-sampling-panel" aria-labelledby="live-sampling-title">
            <header class="sampling-heading">
              <div>
                <span>CONTROLLED LIVE SAMPLE / 真实模型</span>
                <h3 id="live-sampling-title">从真实 AI 取得回答，再在本机量化</h3>
                <p>每次 1–5 条，后端逐条调用并在首次失败时停下；软件不会自动重试或隐藏部分结果。</p>
              </div>
              <span class="session-badge">SESSION ONLY</span>
            </header>

            <div class="provider-switch" role="radiogroup" aria-label="AI 服务预设">
              <button
                v-for="preset in providerPresets"
                :key="preset.id"
                type="button"
                role="radio"
                :aria-checked="sampling.providerId === preset.id"
                :class="{ active: sampling.providerId === preset.id }"
                @click="selectSamplingProvider(preset.id)"
              >
                <strong>{{ preset.label }}</strong>
                <small>{{ preset.id === 'custom' ? 'OPENAI COMPATIBLE' : preset.id.toUpperCase() }}</small>
              </button>
            </div>

            <div class="sampling-config-grid">
              <label class="sampling-field wide">
                <span>BASE URL</span>
                <el-input
                  v-model="sampling.baseUrl"
                  autocomplete="off"
                  placeholder="https://provider.example/v1"
                  @keydown.enter.prevent
                />
              </label>
              <label class="sampling-field">
                <span>MODEL ID</span>
                <el-input
                  v-model="sampling.model"
                  autocomplete="off"
                  :placeholder="activeProviderPreset.modelHint"
                  @keydown.enter.prevent
                />
              </label>
              <label class="sampling-field">
                <span>API KEY</span>
                <el-input
                  v-model="sampling.apiKey"
                  type="password"
                  show-password
                  autocomplete="new-password"
                  placeholder="仅用于本次页面会话"
                  @keydown.enter.prevent
                />
                <small>API Key 只保存在当前页面内存，切换页面或退出软件即清除。</small>
              </label>
              <label class="sampling-field compact">
                <span>样本数</span>
                <el-input-number v-model="sampling.sampleCount" :min="1" :max="5" controls-position="right" />
                <small>按顺序执行，不并发突发。</small>
              </label>
            </div>

            <div class="dispatch-slip">
              <div class="dispatch-index" aria-hidden="true">OUT<br>01</div>
              <div class="dispatch-main">
                <div class="dispatch-title">
                  <div>
                    <span>调用前核对</span>
                    <h4>仅发送下面这一段问题</h4>
                  </div>
                  <dl>
                    <div><dt>服务</dt><dd>{{ activeProviderPreset.label }}</dd></div>
                    <div><dt>地址</dt><dd>{{ sampling.baseUrl || '未填写' }}</dd></div>
                    <div><dt>模型</dt><dd>{{ sampling.model || '未填写' }}</dd></div>
                  </dl>
                </div>
                <pre>{{ samplingPromptPreview || '请先填写上方“监测问题”。' }}</pre>
                <div class="privacy-boundary">
                  <strong>项目、文章、账号、Cookie 均不发送</strong>
                  <span>品牌别名和竞品只在回答返回后参与本地规则分析；鉴权头、模型 ID 与生成参数只作为传输元数据。</span>
                </div>
                <div v-if="samplingIssues.length" class="sampling-issues" role="status">
                  <span v-for="issue in samplingIssues" :key="issue">{{ issue }}</span>
                </div>
                <div class="dispatch-confirm">
                  <el-checkbox v-model="sampling.confirmed">
                    我已核对 prompt、服务地址和模型，同意本次 {{ sampling.sampleCount }} 次顺序调用
                  </el-checkbox>
                  <el-button
                    type="primary"
                    :loading="samplingLoading"
                    :disabled="!canStartSampling"
                    @click="startLiveSampling"
                  >
                    {{ samplingLoading ? '正在顺序采样' : '确认并开始真实采样' }}
                    <el-icon v-if="!samplingLoading"><ArrowRight /></el-icon>
                  </el-button>
                </div>
              </div>
            </div>

            <article
              v-if="liveSamplingReceipt || samplingError"
              class="sampling-receipt"
              :class="`status-${liveSamplingReceipt?.status || 'need_action'}`"
              aria-live="polite"
            >
              <header>
                <div>
                  <span>LIVE SAMPLE RECEIPT</span>
                  <h4>{{ liveSamplingReceipt ? samplingStatusLabel(liveSamplingReceipt.status) : '采样未完成' }}</h4>
                </div>
                <strong v-if="liveSamplingReceipt">
                  {{ liveSamplingReceipt.completedSampleCount }} / {{ liveSamplingReceipt.requestedSampleCount }} 条
                </strong>
              </header>

              <p v-if="samplingError" class="sampling-failure">{{ samplingError }}</p>
              <div v-if="liveSamplingReceipt?.failure" class="sampling-failure">
                <strong>{{ liveSamplingReceipt.failure.code }}</strong>
                <p>{{ liveSamplingReceipt.failure.message }}</p>
                <small v-if="liveSamplingReceipt.failure.outcomeUnknown">远端结果未知：先查模型账单，不要直接重发。</small>
              </div>

              <div v-if="liveSamplingReceipt?.executions.length" class="execution-receipts">
                <article v-for="execution in liveSamplingReceipt.executions" :key="execution.index">
                  <span>#{{ String(execution.index).padStart(2, '0') }}</span>
                  <div>
                    <strong>{{ execution.provider }} · {{ execution.model }}</strong>
                    <small>{{ execution.elapsedMs }} ms{{ execution.providerRequestId ? ` · ${execution.providerRequestId}` : '' }}</small>
                  </div>
                  <dl>
                    <div><dt>Token</dt><dd>{{ formatSamplingTokens(execution.receipt) }}</dd></div>
                    <div><dt>成本</dt><dd>{{ formatSamplingCost(execution.receipt) }}</dd></div>
                  </dl>
                </article>
              </div>

              <div v-if="liveSamplingReceipt" class="receipt-boundary">
                <p><strong>发送凭据：</strong>{{ liveSamplingReceipt.disclosure.sentToProvider.join('、') || '后端未返回' }} · prompt SHA-256 {{ shortHash(liveSamplingReceipt.disclosure.promptSha256) }}</p>
                <p><strong>计价口径：</strong>不硬编码易变的模型价格；未配置单价或供应商未回传用量时记为“未知”，绝不显示成 0 成本。</p>
                <p><strong>不可比性：</strong>不同模型、模型版本、prompt、样本数或采样时间的结果不能直接归因比较；模型回答没有联网核验保证。</p>
                <p><strong>重试规则：</strong>本次不会自动重试。失败或结果未知时先核对供应商账单，再由你明确发起一个新请求。</p>
              </div>

              <details v-if="liveSamplingReceipt?.capturedResponses.length" class="captured-response-list">
                <summary>查看已保留的 {{ liveSamplingReceipt.capturedResponses.length }} 条回答</summary>
                <article v-for="(response, index) in liveSamplingReceipt.capturedResponses" :key="index">
                  <strong>#{{ index + 1 }} · {{ response.provider }} · {{ response.model }}</strong>
                  <p>{{ response.text }}</p>
                </article>
              </details>

              <footer v-if="liveSamplingReceipt?.capturedResponses.length">
                <el-button @click="importLiveSamplesToManual">导入下方手工分析框</el-button>
                <el-button v-if="manualImportSnapshot" @click="rollbackManualImport">撤销导入</el-button>
                <small>导入只修改本页文本，可随时撤销；不会再次调用模型。</small>
              </footer>
            </article>
          </section>

          <div class="manual-divider">
            <span>MANUAL IMPORT / 手工回答</span>
            <p>也可完全不调用模型，粘贴已有回答做本地分析。</p>
          </div>

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

    <section v-if="form.projectId" class="commercial-proof" aria-labelledby="diagnostic-title">
      <header class="proof-heading">
        <div>
          <p class="eyebrow">CLIENT PROOF / READINESS + COST</p>
          <h2 id="diagnostic-title">客户诊断与 AI 成本凭证</h2>
          <p>先量化缺口，再按同一版本复测；成本只统计真实模型调用，不把登录、上传或发布伪装成 Token。</p>
        </div>
        <div class="proof-actions">
          <button
            type="button"
            class="text-action"
            :disabled="commercialLoading"
            @click="loadCommercialOverview(form.projectId)"
          >
            <el-icon :class="{ spinning: commercialLoading }"><Refresh /></el-icon>
            刷新凭证
          </button>
          <el-button
            type="primary"
            :loading="diagnosticLoading"
            :disabled="commercialLoading"
            @click="runCommercialDiagnostic()"
          >
            {{ diagnostic ? '重新诊断并留档' : '建立客户诊断基线' }}
          </el-button>
        </div>
      </header>

      <div v-if="commercialLoading && !diagnostic && !costSummary" class="proof-loading" aria-live="polite">
        <el-skeleton :rows="4" animated />
      </div>
      <div v-else class="proof-grid">
        <article class="diagnostic-board">
          <div v-if="diagnostic" class="diagnostic-score">
            <span>READINESS / 100</span>
            <strong>{{ diagnostic.score }}</strong>
            <em>{{ diagnostic.grade }}</em>
            <p class="diagnostic-delta" :class="{ negative: diagnosticDelta != null && diagnosticDelta < 0 }">
              <template v-if="diagnosticDelta != null">
                {{ diagnosticDelta >= 0 ? '↑' : '↓' }} {{ Math.abs(diagnosticDelta).toFixed(1) }} 分 · 较上次诊断
              </template>
              <template v-else>完成下一次同口径诊断后显示复测变化</template>
            </p>
            <small>
              {{ diagnostic.runId ? `本机记录 #${diagnostic.runId}` : '临时结果' }}
              <template v-if="diagnostic.createdAt"> · {{ formatCompactDate(diagnostic.createdAt) }}</template>
            </small>
          </div>
          <div v-else class="diagnostic-empty">
            <span>BASELINE</span>
            <h3>还没有可对比的客户诊断</h3>
            <p>点击“建立客户诊断基线”，系统会读取项目配置、内容分数和最近一次可见度结果；整个过程只在本机完成。</p>
          </div>

          <div v-if="diagnostic" class="diagnostic-body">
            <div class="dimension-ledger" aria-label="诊断维度">
              <div v-for="dimension in diagnostic.dimensions" :key="dimension.key" class="dimension-row">
                <div>
                  <strong>{{ dimension.label }}</strong>
                  <span>权重 {{ dimension.weight }}%</span>
                </div>
                <i aria-hidden="true"><b :class="dimension.status" :style="{ width: `${dimension.score}%` }" /></i>
                <em>{{ dimension.score }}</em>
              </div>
            </div>

            <div class="gap-ledger">
              <header>
                <div>
                  <span>PRIORITY QUEUE</span>
                  <h3>优先补这些缺口</h3>
                </div>
                <p>
                  <b class="p0">P0 {{ diagnostic.gapCounts.P0 }}</b>
                  <b class="p1">P1 {{ diagnostic.gapCounts.P1 }}</b>
                  <b class="p2">P2 {{ diagnostic.gapCounts.P2 }}</b>
                </p>
              </header>
              <ol v-if="diagnostic.gaps.length">
                <li v-for="gap in diagnostic.gaps.slice(0, 6)" :key="gap.key">
                  <span :class="gap.priority.toLowerCase()">{{ gap.priority }}</span>
                  <div>
                    <strong>{{ gap.title }}</strong>
                    <p>{{ gap.action }}</p>
                    <small>验收：{{ gap.metricTarget }}</small>
                  </div>
                </li>
              </ol>
              <div v-else class="gap-empty">当前没有 P0/P1 缺口；保持同一口径定期复测。</div>
            </div>
          </div>

          <footer v-if="diagnostic" class="diagnostic-boundary">
            <span>{{ diagnostic.externalModelUsed || diagnostic.contentSentExternally ? '需要复核数据边界' : '本地确定性诊断 · 未发送客户内容' }}</span>
            <p>{{ diagnostic.claimBoundary || '诊断分衡量当前项目与样本口径，不等同于平台官方排名。' }}</p>
          </footer>
        </article>

        <aside class="cost-receipt">
          <header>
            <div>
              <span>AI USAGE RECEIPT</span>
              <h3>模型用量台账</h3>
            </div>
            <small>{{ costSummary?.operationCount || 0 }} 次调用</small>
          </header>

          <div class="receipt-total">
            <span>已知 Token 合计</span>
            <strong>{{ formatTokenCount(costSummary?.knownTotalTokens, costSummary?.knownTokenRecordCount, costSummary?.unknownUsageCount) }}</strong>
            <small>{{ costSummary?.knownTokenRecordCount || 0 }} 条有用量回执</small>
          </div>

          <dl class="receipt-facts">
            <div>
              <dt>估算成本</dt>
              <dd v-if="costSummary?.calculatedCosts.length">
                {{ costSummary.calculatedCosts.map(formatCost).join(' + ') }}
              </dd>
              <dd v-else>尚无可计算金额</dd>
            </div>
            <div>
              <dt>用量未知</dt>
              <dd :class="{ warning: costSummary?.unknownUsageCount }">{{ costSummary?.unknownUsageCount || 0 }} 条</dd>
            </div>
            <div>
              <dt>尚未定价</dt>
              <dd :class="{ warning: costSummary?.unpricedCount }">{{ costSummary?.unpricedCount || 0 }} 条</dd>
            </div>
          </dl>

          <div v-if="costSummary?.operations.length" class="operation-ledger">
            <div v-for="operation in costSummary.operations" :key="operation.operation">
              <span>{{ operationLabel(operation.operation) }}</span>
              <strong>{{ formatTokenCount(operation.knownTotalTokens, operation.operationCount - operation.unknownUsageCount, operation.unknownUsageCount) }}</strong>
              <small>{{ operation.operationCount }} 次<template v-if="operation.unknownUsageCount"> · {{ operation.unknownUsageCount }} 未知</template></small>
            </div>
          </div>
          <div v-else class="receipt-empty">完成一次文章生成或 GEO 调优后，这里会出现 provider 返回的 Token 回执。</div>

          <section v-if="costSummary?.recent.length" class="call-receipts" aria-label="最近模型调用明细">
            <header>
              <span>RECENT MODEL CALLS</span>
              <strong>逐次调用明细</strong>
            </header>
            <div class="call-receipt-list">
              <article v-for="receipt in costSummary.recent" :key="receipt.id || `${receipt.operation}-${receipt.createdAt}`" class="call-receipt">
                <header>
                  <div>
                    <span>{{ operationLabel(receipt.operation) }}</span>
                    <strong>{{ receipt.provider }} / {{ receipt.model }}</strong>
                  </div>
                  <time>{{ receipt.createdAt ? formatCompactDate(receipt.createdAt) : '时间未知' }}</time>
                </header>
                <div class="receipt-tokens">
                  <span>输入 / 输出 / 合计</span>
                  <strong>
                    {{ formatReceiptToken(receipt.inputTokens) }} /
                    {{ formatReceiptToken(receipt.outputTokens) }} /
                    {{ formatReceiptToken(receipt.totalTokens) }}
                  </strong>
                </div>
                <dl>
                  <div>
                    <dt>用量口径</dt>
                    <dd :class="{ warning: receipt.usageStatus !== 'reported' }">{{ usageStatusLabel(receipt.usageStatus) }}</dd>
                  </div>
                  <div>
                    <dt>单价快照</dt>
                    <dd :class="{ warning: receipt.inputPricePerMillion == null || receipt.outputPricePerMillion == null }">{{ formatReceiptPrice(receipt) }}</dd>
                  </div>
                  <div>
                    <dt>本次估算成本</dt>
                    <dd :class="{ warning: receipt.costStatus !== 'calculated' }">{{ formatReceiptCost(receipt) }}</dd>
                  </div>
                </dl>
              </article>
            </div>
          </section>

          <div class="non-model-stamp">
            <span>NON_MODEL</span>
            <div>
              <strong>发布自动化不产生模型 Token</strong>
              <p>登录、浏览器上传和点击发布若未调用模型，就显示“不适用”，不是 0 成本模型调用。</p>
            </div>
          </div>
          <p class="unknown-rule">未提供 Token ≠ 0 Token；未配置单价 ≠ 0 成本。最终费用以供应商账单为准。</p>
        </aside>
      </div>

      <p v-if="commercialError" class="commercial-error" role="alert">{{ commercialError }}</p>
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
      <el-icon class="error-symbol"><WarningFilled /></el-icon>
      <div>
        <h2>本次分析没有完成</h2>
        <p>{{ analysisError }}</p>
      </div>
      <el-button type="primary" :loading="analyzing" @click="submitAnalysis">使用当前样本重试</el-button>
    </section>

    <section v-else-if="analyzing" class="result-loading" aria-live="polite" aria-busy="true">
      <el-icon class="loading-radar spinning" aria-hidden="true"><Loading /></el-icon>
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
          <li v-if="result.methodology.externalModelUsed">回答来自真实外部模型；仅 prompt 作为自然语言内容外发，所有品牌识别与指标计算仍在本机完成。</li>
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
            <el-icon><Grid /></el-icon>
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
          <el-icon><Document /></el-icon>
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
          <el-icon><Link /></el-icon>
          <p>当前样本没有提取到可规范化引用。</p>
        </div>
      </article>
    </section>

    <section v-else class="result-empty">
      <div class="empty-orbit" aria-hidden="true"><el-icon><View /></el-icon></div>
      <div>
        <p class="eyebrow">WAITING FOR EVIDENCE</p>
        <h2>结果将从证据开始，而不是从一个分数开始</h2>
        <p>选择项目并粘贴 1–20 条回答。分析完成后，可从汇总指标一路回看至每条原始证据。</p>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Document, Grid, Link, Loading, Refresh, View, WarningFilled } from '@element-plus/icons-vue'

import { projectApi } from '@/api/project'
import { visibilityApi } from '@/api/visibility'
import { diagnosticsApi } from '@/api/diagnostics'
import {
  normalizeCommercialDiagnostic,
  normalizeCommercialDiagnosticHistory,
  normalizeProjectCostSummary,
  operationLabel
} from '@/utils/commercialDiagnostic'
import {
  buildVisibilityPayload,
  normalizeVisibilityHistory,
  normalizeVisibilityResult,
  parseVisibilityResponses
} from '@/utils/visibilityResult'
import {
  VISIBILITY_PROVIDER_PRESETS,
  applyVisibilityProviderPreset,
  buildManualSampleImport,
  buildVisibilitySamplingRequest,
  normalizeVisibilitySamplingResponse,
  samplingStatusLabel,
  validateVisibilitySamplingDraft,
  visibilityProviderPreset
} from '@/utils/visibilitySampling'
import { diagnosticRetestDelta } from '@/utils/dashboardPresentation'

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
const diagnostic = ref(null)
const commercialDiagnosticRuns = ref([])
const costSummary = ref(null)
const diagnosticLoading = ref(false)
const commercialLoading = ref(false)
const commercialError = ref('')
const samplingLoading = ref(false)
const samplingError = ref('')
const liveSamplingReceipt = ref(null)
const manualImportSnapshot = ref(null)
let historyRequestSequence = 0
let commercialRequestSequence = 0

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

const providerPresets = VISIBILITY_PROVIDER_PRESETS
const sampling = reactive({
  providerId: 'qwen',
  baseUrl: visibilityProviderPreset('qwen').baseUrl,
  model: visibilityProviderPreset('qwen').model,
  apiKey: '',
  sampleCount: 3,
  confirmed: false
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
const activeProviderPreset = computed(() => visibilityProviderPreset(sampling.providerId))
const samplingPromptPreview = computed(() => form.prompt.trim())
const samplingDraft = computed(() => ({
  projectId: form.projectId,
  prompt: samplingPromptPreview.value,
  sampleCount: sampling.sampleCount,
  aliases: form.aliases,
  competitors: form.competitors,
  persist: form.persist,
  confirmed: sampling.confirmed,
  baseUrl: sampling.baseUrl,
  model: sampling.model,
  apiKey: sampling.apiKey
}))
const samplingIssues = computed(() => validateVisibilitySamplingDraft(samplingDraft.value))
const canStartSampling = computed(() => !samplingLoading.value && samplingIssues.value.length === 0)
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
const diagnosticDelta = computed(() => diagnosticRetestDelta(commercialDiagnosticRuns.value))

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
  liveSamplingReceipt.value = null
  samplingError.value = ''
  sampling.confirmed = false
  manualImportSnapshot.value = null
  diagnostic.value = null
  commercialDiagnosticRuns.value = []
  costSummary.value = null
  analysisError.value = ''
  historyRestoreError.value = ''
  void loadHistory(form.projectId)
  void loadCommercialOverview(form.projectId)
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
    if (form.projectId) {
      void loadHistory(form.projectId)
      void loadCommercialOverview(form.projectId)
    }
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

const loadLatestDiagnostic = async projectId => {
  const historyResponse = await diagnosticsApi.getRuns(projectId, 10, { suppressGlobalError: true })
  const runs = normalizeCommercialDiagnosticHistory(historyResponse)
  commercialDiagnosticRuns.value = runs
  if (!runs.length) return null
  const detailResponse = await diagnosticsApi.getRun(runs[0].id, { suppressGlobalError: true })
  return normalizeCommercialDiagnostic(detailResponse)
}

const loadCommercialOverview = async projectId => {
  const requestSequence = ++commercialRequestSequence
  commercialError.value = ''
  if (!projectId) {
    diagnostic.value = null
    commercialDiagnosticRuns.value = []
    costSummary.value = null
    commercialLoading.value = false
    return
  }
  commercialLoading.value = true
  const [diagnosticOutcome, costOutcome] = await Promise.allSettled([
    loadLatestDiagnostic(projectId),
    diagnosticsApi.getProjectCostSummary(projectId, 30, { suppressGlobalError: true })
  ])
  if (requestSequence !== commercialRequestSequence || String(form.projectId) !== String(projectId)) return
  const errors = []
  if (diagnosticOutcome.status === 'fulfilled') {
    diagnostic.value = diagnosticOutcome.value
  } else {
    diagnostic.value = null
    errors.push(`诊断历史：${readableError(diagnosticOutcome.reason)}`)
  }
  if (costOutcome.status === 'fulfilled') {
    costSummary.value = normalizeProjectCostSummary(costOutcome.value)
  } else {
    costSummary.value = null
    errors.push(`成本台账：${readableError(costOutcome.reason)}`)
  }
  commercialError.value = errors.join('；')
  commercialLoading.value = false
}

const runCommercialDiagnostic = async ({ silent = false } = {}) => {
  if (!form.projectId || diagnosticLoading.value) return
  const expectedProjectId = String(form.projectId)
  diagnosticLoading.value = true
  commercialError.value = ''
  try {
    const response = await diagnosticsApi.diagnoseProject(
      form.projectId,
      { persist: true },
      { suppressGlobalError: true }
    )
    if (String(form.projectId) !== expectedProjectId) return
    diagnostic.value = normalizeCommercialDiagnostic(response)
    try {
      const historyResponse = await diagnosticsApi.getRuns(form.projectId, 10, { suppressGlobalError: true })
      if (String(form.projectId) === expectedProjectId) {
        commercialDiagnosticRuns.value = normalizeCommercialDiagnosticHistory(historyResponse)
      }
    } catch {
      // The persisted result remains valid even when the lightweight history refresh is unavailable.
    }
    if (!silent) ElMessage.success('客户诊断已在本机留档，可按 P0/P1 清单优化后复测。')
  } catch (error) {
    if (String(form.projectId) === expectedProjectId) {
      commercialError.value = `客户诊断未完成：${readableError(error)}`
    }
  } finally {
    diagnosticLoading.value = false
  }
}

const formatTokenCount = (value, knownRecords = 0, unknownRecords = 0) => {
  if (value == null) return '—'
  if (Number(knownRecords) === 0 && Number(unknownRecords) > 0) return '未知'
  return new Intl.NumberFormat('zh-CN').format(Number(value) || 0)
}

const formatCost = item => `${item.currency} ${item.amount}`

const formatReceiptToken = value => value == null
  ? '未知'
  : new Intl.NumberFormat('zh-CN').format(Number(value) || 0)

const usageStatusLabel = value => ({
  reported: '供应商回执',
  estimated: '软件估算',
  unknown: '用量未知'
}[String(value)] || '用量未知')

const formatReceiptPrice = receipt => {
  if (receipt.inputPricePerMillion == null || receipt.outputPricePerMillion == null) return '未配置单价'
  const currency = receipt.currency || '币种未知'
  return `${currency} ${receipt.inputPricePerMillion} / ${receipt.outputPricePerMillion}（输入 / 输出，每百万 Token）`
}

const formatReceiptCost = receipt => {
  if (receipt.costStatus === 'calculated' && receipt.estimatedCost != null) {
    return `${receipt.currency || '币种未知'} ${receipt.estimatedCost}`
  }
  return ({
    unpriced: '未计价',
    unknown_usage: '用量未知，无法计价',
    insufficient_usage: '用量字段不足，无法计价'
  })[receipt.costStatus] || '无法计价'
}

const selectSamplingProvider = providerId => {
  Object.assign(sampling, applyVisibilityProviderPreset(sampling, providerId))
  sampling.confirmed = false
}

const createSamplingIdentity = () => {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return {
    requestId: `visibility-ui-${randomPart}`,
    idempotencyKey: `visibility-sample-${randomPart}`
  }
}

const shortHash = value => {
  const text = String(value || '')
  if (!text) return '未返回'
  return text.length > 16 ? `${text.slice(0, 12)}…${text.slice(-4)}` : text
}

const formatSamplingTokens = receipt => {
  if (!receipt || receipt.usageStatus === 'unknown' || receipt.totalTokens == null) {
    return '未知（不等于 0）'
  }
  const total = new Intl.NumberFormat('zh-CN').format(receipt.totalTokens)
  if (receipt.inputTokens == null || receipt.outputTokens == null) return `${total} 合计`
  return `${total}（${receipt.inputTokens} 入 / ${receipt.outputTokens} 出）`
}

const formatSamplingCost = receipt => {
  if (receipt?.costStatus === 'calculated' && receipt.estimatedCost != null) {
    return `${receipt.currency || '币种未知'} ${receipt.estimatedCost}`
  }
  return ({
    unpriced: '未配置单价',
    unknown_usage: '用量未知，无法计价',
    insufficient_usage: '用量字段不足，无法计价'
  })[receipt?.costStatus] || '成本未知'
}

const applyLiveSamplingOutcome = async receipt => {
  liveSamplingReceipt.value = receipt
  if (receipt.analysis) {
    const normalized = normalizeVisibilityResult(receipt.analysis)
    if (receipt.runId != null) normalized.runId = receipt.runId
    if (receipt.createdAt) normalized.analyzedAt = receipt.createdAt
    result.value = normalized
    analysisError.value = ''
    await revealResult()
  }
  if (receipt.persisted) void loadHistory(form.projectId)
  if (receipt.executions.length) {
    void loadCommercialOverview(form.projectId)
    void runCommercialDiagnostic({ silent: true })
  }
}

const startLiveSampling = async () => {
  if (samplingLoading.value) return
  const issues = validateVisibilitySamplingDraft(samplingDraft.value)
  if (issues.length) {
    ElMessage.warning(issues[0])
    return
  }

  const payload = buildVisibilitySamplingRequest(samplingDraft.value)
  const identity = createSamplingIdentity()
  samplingLoading.value = true
  samplingError.value = ''
  liveSamplingReceipt.value = null
  result.value = null
  sampling.confirmed = false
  try {
    const response = await diagnosticsApi.sampleVisibility(payload, {
      ...identity,
      suppressGlobalError: true
    })
    const receipt = normalizeVisibilitySamplingResponse(response)
    await applyLiveSamplingOutcome(receipt)
    if (receipt.status === 'completed') {
      ElMessage.success(`真实采样完成：${receipt.completedSampleCount} 条回答已在本机分析。`)
    } else {
      ElMessage.warning(`真实采样部分完成：已保留 ${receipt.completedSampleCount} 条，后续调用已停止。`)
    }
  } catch (error) {
    const responseData = error?.response?.data
    if (responseData?.data && typeof responseData.data === 'object') {
      await applyLiveSamplingOutcome(normalizeVisibilitySamplingResponse(responseData))
    }
    samplingError.value = readableError(error)
  } finally {
    samplingLoading.value = false
  }
}

const importLiveSamplesToManual = () => {
  const imported = buildManualSampleImport(liveSamplingReceipt.value)
  if (!imported) return
  if (!manualImportSnapshot.value) {
    manualImportSnapshot.value = {
      responsesText: form.responsesText,
      provider: form.provider,
      model: form.model
    }
  }
  form.responsesText = imported
  form.provider = liveSamplingReceipt.value.capturedResponses[0]?.provider || activeProviderPreset.value.label
  form.model = liveSamplingReceipt.value.capturedResponses[0]?.model || sampling.model
  ElMessage.success('真实回答已导入手工分析框；这一步没有再次调用模型。')
}

const rollbackManualImport = () => {
  if (!manualImportSnapshot.value) return
  Object.assign(form, manualImportSnapshot.value)
  manualImportSnapshot.value = null
  ElMessage.success('已撤销导入，恢复导入前的手工回答。')
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
    if (form.persist) void runCommercialDiagnostic({ silent: true })
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
onBeforeUnmount(() => {
  sampling.apiKey = ''
})
</script>

<style lang="scss" scoped>
.visibility-workbench {
  --harbor: #111214;
  --harbor-soft: #2c2d30;
  --signal: #111214;
  --signal-soft: #f0f0ed;
  --ink: #18191b;
  --muted: #73757a;
  --rule: #deded9;
  --paper: #ffffff;
  --amber: #bd7d2c;
  --red: #b85151;
  width: min(100%, 1420px);
  margin: 0 auto;
  padding: 34px 40px 54px;
  color: var(--ink);
  font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", "PingFang SC", sans-serif;
}

.page-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  min-height: 230px;
  padding: 8px 0 30px;
  border-bottom: 1px solid var(--rule);
}

.heading-copy {
  max-width: 850px;
  .eyebrow { margin: 0 0 15px; color: #77797e; font: 600 11px/1.2 "Cascadia Mono", monospace; letter-spacing: 0.16em; }
  h1 { margin: 0; color: var(--harbor); font: 900 clamp(44px, 5vw, 72px)/1.05 "Microsoft YaHei UI", "PingFang SC", sans-serif; letter-spacing: -0.065em; }
  > p:last-child { max-width: 690px; margin: 18px 0 0; color: var(--muted); font-size: 15px; line-height: 1.75; }
}

.local-engine-seal {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 250px;
  padding: 14px 16px;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: #fff;
  .seal-pulse { width: 10px; height: 10px; flex: 0 0 auto; border: 2px solid #fff; border-radius: 50%; background: var(--signal); box-shadow: 0 0 0 4px rgba(17, 18, 20, 0.1); }
  div { display: flex; flex-direction: column; gap: 4px; }
  strong { font-size: 12px; }
  small { color: var(--muted); font-size: 9px; }
  &.unsafe { border-color: #aeb0b3; background: #f5f5f2; .seal-pulse { background: var(--ink); box-shadow: 0 0 0 4px rgba(17, 18, 20, 0.12); } }
}

.analysis-console {
  position: relative;
  display: grid;
  grid-template-columns: 54px 1fr;
  margin-top: 22px;
  border: 1px solid var(--rule);
  background: var(--paper);
  border-radius: 9px;
  box-shadow: 0 18px 46px rgba(17, 18, 20, 0.045);
  overflow: hidden;
}

.signal-rail {
  display: flex;
  align-items: center;
  flex-direction: column;
  padding: 28px 0;
  border-right: 1px solid var(--rule);
  background: #f4f4f1;
  i { width: 1px; min-height: 96px; flex: 1; background: #d4d4d0; }
}

.rail-node {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border: 1px solid #b8b9b6;
  border-radius: 50%;
  background: #fff;
  color: #77797d;
  font: 700 9px/1 "Cascadia Mono", monospace;
  transition: color 180ms ease, background 180ms ease, border-color 180ms ease;
  &.active { border-color: var(--signal); background: var(--signal); color: #fff; }
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
  > div > span { color: #77797e; font: 600 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 8px 0 0; color: var(--harbor); font: 800 24px/1.05 "Microsoft YaHei UI", sans-serif; letter-spacing: -0.04em; }
}
.text-action {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 0;
  border: 0;
  background: transparent;
  color: #45474b;
  cursor: pointer;
  font-size: 11px;
  &:disabled { cursor: not-allowed; opacity: 0.5; }
  &:focus-visible { outline: 2px solid var(--signal); outline-offset: 3px; }
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
  :deep(.el-select__wrapper), :deep(.el-input__wrapper), :deep(.el-textarea__inner) { border-radius: 7px; box-shadow: 0 0 0 1px #d5d5d1 inset; }
  :deep(.el-select__wrapper.is-focused), :deep(.el-input__wrapper.is-focus), :deep(.el-textarea__inner:focus) { box-shadow: 0 0 0 2px rgba(17, 18, 20, 0.18) inset; }
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
  background: #f5f5f2;
  > span { color: #717378; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
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

.live-sampling-panel {
  grid-column: 1 / -1;
  margin-top: 3px;
  padding: 22px;
  border: 1px solid #c9cac7;
  border-radius: 8px;
  background: #f8f8f5;
}
.sampling-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--rule);
  > div > span { color: #66686d; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h3 { margin: 8px 0 0; color: var(--ink); font: 800 20px/1.15 "Microsoft YaHei UI", sans-serif; letter-spacing: -0.035em; }
  p { max-width: 720px; margin: 7px 0 0; color: var(--muted); font-size: 10px; line-height: 1.65; }
}
.session-badge { padding: 6px 8px; border: 1px solid #b7b8b5; background: #fff; color: #55575b; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; white-space: nowrap; }
.provider-switch {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
  button {
    display: flex;
    min-height: 54px;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    padding: 12px;
    border: 1px solid #d2d3d0;
    border-radius: 6px;
    background: #fff;
    color: var(--ink);
    cursor: pointer;
    text-align: left;
  }
  button:hover { border-color: #8c8e92; }
  button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  button.active { border-color: var(--ink); background: var(--ink); color: #fff; }
  strong { font-size: 11px; }
  small { color: #85878b; font: 700 7px/1.3 "Cascadia Mono", monospace; }
  button.active small { color: #c7c8cb; }
}
.sampling-config-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1.4fr) minmax(190px, 0.8fr) minmax(220px, 0.9fr) 120px;
  gap: 11px;
  margin-top: 12px;
}
.sampling-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  > span { color: #5d5f64; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  > small { color: var(--muted); font-size: 8px; line-height: 1.45; }
  :deep(.el-input__wrapper), :deep(.el-input-number) { border-radius: 6px; }
  :deep(.el-input-number) { width: 100%; }
}
.dispatch-slip {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  margin-top: 16px;
  border: 1px solid #babbb8;
  border-radius: 7px;
  background: #fff;
  overflow: hidden;
}
.dispatch-index { padding: 19px 10px; background: var(--ink); color: #fff; font: 800 13px/1.35 "Cascadia Mono", monospace; letter-spacing: 0.08em; text-align: center; }
.dispatch-main { min-width: 0; padding: 18px; }
.dispatch-title {
  display: grid;
  grid-template-columns: minmax(180px, 0.6fr) minmax(0, 1.4fr);
  gap: 22px;
  align-items: start;
  > div > span { color: #6b6d72; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  h4 { margin: 6px 0 0; font-size: 15px; }
  dl { display: grid; gap: 6px; margin: 0; }
  dl > div { display: grid; grid-template-columns: 46px minmax(0, 1fr); gap: 8px; }
  dt { color: #808287; font-size: 8px; }
  dd { min-width: 0; margin: 0; overflow-wrap: anywhere; color: #323337; font: 600 8px/1.4 "Cascadia Mono", monospace; }
}
.dispatch-main > pre { max-height: 180px; overflow: auto; margin: 15px 0 0; padding: 14px 15px; border: 1px solid #dedfdb; border-radius: 5px; background: #f5f5f2; color: #1d1e20; font: 11px/1.7 "Cascadia Mono", "Microsoft YaHei UI", monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.privacy-boundary { display: flex; align-items: baseline; gap: 14px; margin-top: 11px; strong { flex: 0 0 auto; color: var(--ink); font-size: 10px; } span { color: var(--muted); font-size: 9px; line-height: 1.55; } }
.sampling-issues { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 11px; span { padding: 5px 7px; border: 1px solid #e0c9a4; border-radius: 4px; background: #fff8ec; color: #8b6226; font-size: 8px; } }
.dispatch-confirm {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #dedfdb;
  :deep(.el-checkbox) { height: auto; white-space: normal; }
  :deep(.el-checkbox__label) { color: #37383b; font-size: 10px; line-height: 1.5; white-space: normal; }
  :deep(.el-checkbox__input.is-checked .el-checkbox__inner) { border-color: var(--ink); background: var(--ink); }
  :deep(.el-button) { min-width: 188px; border-color: var(--ink); border-radius: 6px; background: var(--ink); }
}
.sampling-receipt {
  margin-top: 14px;
  padding: 16px 17px;
  border: 1px solid #c8c9c6;
  border-left: 4px solid var(--ink);
  border-radius: 6px;
  background: #fff;
  &.status-partial, &.status-need_action { border-left-color: var(--amber); }
  > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  > header span { color: #6d6f74; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.09em; }
  > header h4 { margin: 6px 0 0; font-size: 16px; }
  > header > strong { font: 800 20px/1 "Cascadia Mono", monospace; }
  > footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 13px; padding-top: 13px; border-top: 1px solid var(--rule); }
  > footer small { color: var(--muted); font-size: 8px; }
}
.sampling-failure { margin: 12px 0 0; padding: 11px 12px; border: 1px solid #dfb6b2; background: #fff4f3; color: #8f4640; font-size: 10px; line-height: 1.55; p { margin: 4px 0 0; } small { display: block; margin-top: 6px; font-weight: 700; } }
.execution-receipts { display: grid; gap: 7px; margin-top: 13px; > article { display: grid; grid-template-columns: 32px minmax(0, 1fr) minmax(220px, auto); gap: 10px; align-items: center; padding: 10px 11px; border: 1px solid #e0e1de; border-radius: 5px; background: #fafaf8; } > article > span { color: #67696d; font: 700 9px/1 "Cascadia Mono", monospace; } > article > div { display: flex; min-width: 0; flex-direction: column; gap: 4px; } > article > div strong { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; } > article > div small { overflow: hidden; color: var(--muted); font: 8px/1.3 "Cascadia Mono", monospace; text-overflow: ellipsis; white-space: nowrap; } dl { display: grid; grid-template-columns: repeat(2, minmax(100px, 1fr)); gap: 10px; margin: 0; } dl > div { min-width: 0; } dt { color: #85878b; font-size: 7px; } dd { margin: 4px 0 0; color: #38393d; font-size: 8px; line-height: 1.35; } }
.receipt-boundary { margin-top: 12px; padding: 11px 12px; border: 1px solid #e0e1de; background: #f6f6f3; p { margin: 0; color: #686a6f; font-size: 8px; line-height: 1.65; } p + p { margin-top: 4px; } strong { color: #303135; } }
.captured-response-list { margin-top: 11px; border: 1px solid #dedfdb; border-radius: 5px; summary { padding: 10px 12px; cursor: pointer; color: #404145; font-size: 9px; font-weight: 700; } > article { padding: 12px; border-top: 1px solid #e3e4e1; } > article strong { font: 700 8px/1.3 "Cascadia Mono", monospace; } > article p { max-height: 180px; overflow: auto; margin: 8px 0 0; color: #55575b; font-size: 9px; line-height: 1.65; white-space: pre-wrap; } }
.manual-divider { display: flex; grid-column: 1 / -1; align-items: baseline; gap: 14px; padding-top: 4px; border-bottom: 1px solid var(--rule); span { color: #5f6166; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.09em; } p { margin: 0 0 9px; color: var(--muted); font-size: 9px; } }

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
  :deep(.el-button) { min-width: 175px; border-radius: 7px; background: var(--harbor); border-color: var(--harbor); }
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

.commercial-proof {
  margin-top: 22px;
  padding: 24px 26px 26px;
  border: 1px solid var(--rule);
  border-radius: 9px;
  background: #f8f8f6;
}
.proof-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 19px;
  border-bottom: 1px solid #cddbd8;
  .eyebrow { margin: 0; color: #77797e; font: 600 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 8px 0 0; color: var(--harbor); font: 800 23px/1.05 "Microsoft YaHei UI", sans-serif; letter-spacing: -0.04em; }
  p:last-child { max-width: 720px; margin: 8px 0 0; color: var(--muted); font-size: 10px; line-height: 1.65; }
}
.proof-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  :deep(.el-button) { border-radius: 7px; background: var(--harbor); border-color: var(--harbor); }
  :deep(.el-button:not(.is-disabled):hover) { background: var(--harbor-soft); border-color: var(--harbor-soft); }
}
.proof-loading { padding: 28px 4px 4px; }
.proof-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.72fr);
  gap: 16px;
  margin-top: 18px;
}
.diagnostic-board {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  border: 1px solid #d7d7d3;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.94);
  overflow: hidden;
}
.diagnostic-score {
  display: flex;
  min-height: 360px;
  flex-direction: column;
  padding: 22px 17px;
  border-right: 1px solid #2e2f32;
  background: var(--harbor);
  color: #eff9f7;
  > span { color: #bfc0c3; font: 700 8px/1.3 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  > strong { margin-top: 34px; font: 700 58px/0.82 "Bahnschrift SemiCondensed", sans-serif; letter-spacing: -0.06em; }
  > em { display: grid; width: 38px; height: 38px; margin-top: 14px; place-items: center; border: 1px solid #737477; color: #fff; font: 700 15px/1 "Cascadia Mono", monospace; font-style: normal; }
  .diagnostic-delta { margin: 15px 0 0; color: #c7c8ca; font-size: 8px; line-height: 1.55; }
  .diagnostic-delta.negative { color: #f0aaa5; }
  > small { margin-top: auto; color: rgba(232, 246, 243, 0.52); font: 600 8px/1.55 "Cascadia Mono", monospace; }
}
.diagnostic-empty {
  grid-column: 1 / -1;
  min-height: 230px;
  padding: 35px;
  background: rgba(255,255,255,0.86);
  > span { color: #318f8b; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h3 { margin: 13px 0 0; color: var(--harbor); font: 700 22px/1.1 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { max-width: 620px; margin: 10px 0 0; color: var(--muted); font-size: 10px; line-height: 1.7; }
}
.diagnostic-body {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(220px, 0.8fr) minmax(260px, 1.2fr);
}
.dimension-ledger { padding: 18px; border-right: 1px solid #dbe4e2; }
.dimension-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 52px;
  gap: 8px 10px;
  align-items: end;
  padding: 12px 0;
  border-bottom: 1px solid #e4ebe9;
  &:last-child { border-bottom: 0; }
  > div { display: flex; min-width: 0; justify-content: space-between; gap: 8px; }
  > div strong { overflow: hidden; color: var(--ink); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  > div span { flex: 0 0 auto; color: #80908e; font: 600 7px/1 "Cascadia Mono", monospace; }
  > i { display: block; height: 3px; grid-column: 1; background: #e2eae8; }
  > i b { display: block; height: 100%; background: var(--signal); }
  > i b.attention { background: var(--amber); }
  > i b.critical { background: var(--red); }
  > em { grid-row: 1 / 3; grid-column: 2; align-self: center; color: var(--harbor); font: 700 19px/1 "Bahnschrift SemiCondensed", sans-serif; font-style: normal; text-align: right; }
}
.gap-ledger { min-width: 0; padding: 18px; }
.gap-ledger > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid #dfe7e5;
  span { color: #4e8682; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.09em; }
  h3 { margin: 7px 0 0; color: var(--harbor); font-size: 13px; }
  p { display: flex; gap: 5px; margin: 0; }
  b { padding: 4px 5px; border: 1px solid #b8c7c5; color: #677a78; font: 700 7px/1 "Cascadia Mono", monospace; }
  b.p0 { border-color: #d5a6a2; background: #fff2f1; color: #a44d47; }
  b.p1 { border-color: #d9c196; background: #fff8eb; color: #916626; }
}
.gap-ledger ol { max-height: 285px; overflow: auto; margin: 0; padding: 0; list-style: none; }
.gap-ledger li {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid #e7eceb;
  &:last-child { border-bottom: 0; }
  > span { width: fit-content; height: fit-content; padding: 5px 4px; border: 1px solid #b7c5c3; color: #687b78; font: 700 7px/1 "Cascadia Mono", monospace; }
  > span.p0 { border-color: #cf8f89; color: #a94842; }
  > span.p1 { border-color: #d2b276; color: #8f631f; }
  strong { color: var(--ink); font-size: 10px; }
  p { margin: 5px 0 0; color: var(--muted); font-size: 8px; line-height: 1.55; }
  small { display: block; margin-top: 5px; color: #4f827f; font: 600 7px/1.4 "Cascadia Mono", monospace; }
}
.gap-empty { padding: 24px 0; color: #4c7f7b; font-size: 10px; line-height: 1.6; }
.diagnostic-boundary {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 11px 14px;
  border-top: 1px solid #d7e2df;
  background: #f3f8f6;
  span { color: #2f7772; font: 700 8px/1 "Cascadia Mono", monospace; }
  p { margin: 0; color: #71817f; font-size: 8px; line-height: 1.5; text-align: right; }
}
.cost-receipt {
  position: relative;
  min-width: 0;
  padding: 20px 19px 24px;
  border: 1px solid #d2d2ce;
  border-top: 4px solid var(--harbor);
  border-radius: 7px;
  background: #fff;
  &::after { content: none; }
  > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; padding-bottom: 15px; border-bottom: 1px dashed #b9c9c7; }
  > header span { color: #40817d; font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
  > header h3 { margin: 7px 0 0; color: var(--harbor); font: 700 18px/1 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  > header small { color: #6b807d; font: 600 8px/1.3 "Cascadia Mono", monospace; }
}
.receipt-total {
  padding: 21px 0 17px;
  border-bottom: 1px solid #dfe7e5;
  > span { display: block; color: #728481; font-size: 8px; }
  > strong { display: block; margin-top: 9px; color: var(--harbor); font: 700 35px/0.9 "Bahnschrift SemiCondensed", sans-serif; letter-spacing: -0.03em; }
  > small { display: block; margin-top: 8px; color: #718480; font: 600 8px/1 "Cascadia Mono", monospace; }
}
.receipt-facts { margin: 0; padding: 7px 0; }
.receipt-facts > div { display: flex; justify-content: space-between; gap: 14px; padding: 7px 0; }
.receipt-facts dt { color: #748582; font-size: 8px; }
.receipt-facts dd { margin: 0; color: var(--ink); font: 700 9px/1.3 "Cascadia Mono", monospace; text-align: right; }
.receipt-facts dd.warning { color: #9b6924; }
.operation-ledger { border-top: 1px dashed #becdca; border-bottom: 1px dashed #becdca; }
.operation-ledger > div { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 10px 0; border-bottom: 1px solid #e4ebe9; &:last-child { border-bottom: 0; } }
.operation-ledger span { color: var(--ink); font-size: 9px; }
.operation-ledger strong { color: var(--harbor); font: 700 10px/1 "Cascadia Mono", monospace; }
.operation-ledger small { grid-column: 1 / -1; color: #788986; font-size: 7px; }
.receipt-empty { padding: 17px 0; border-top: 1px dashed #becdca; border-bottom: 1px dashed #becdca; color: var(--muted); font-size: 8px; line-height: 1.6; }
.call-receipts { margin-top: 16px; border-top: 1px solid #d9e4e2; }
.call-receipts > header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 13px 0 10px; }
.call-receipts > header span { color: #40817d; font: 700 7px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
.call-receipts > header strong { color: var(--harbor); font-size: 9px; }
.call-receipt-list { max-height: 430px; padding-right: 3px; overflow: auto; scrollbar-color: #9cb9b5 transparent; scrollbar-width: thin; }
.call-receipt {
  margin-bottom: 8px;
  padding: 11px 12px;
  border: 1px solid #d7e2df;
  border-left: 3px solid #6caaa5;
  background: #f8fbfa;
  &:last-child { margin-bottom: 0; }
  > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
  > header div { min-width: 0; }
  > header span { display: block; color: #337a76; font-size: 8px; }
  > header strong { display: block; margin-top: 4px; overflow-wrap: anywhere; color: var(--ink); font: 700 8px/1.35 "Cascadia Mono", monospace; }
  > header time { flex: 0 0 auto; color: #7a8a87; font: 600 7px/1.3 "Cascadia Mono", monospace; }
  dl { margin: 7px 0 0; padding-top: 6px; border-top: 1px dashed #cfddda; }
  dl > div { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; padding: 3px 0; }
  dt { color: #758582; font-size: 7px; }
  dd { max-width: 68%; margin: 0; color: var(--ink); font: 600 7px/1.45 "Cascadia Mono", monospace; text-align: right; overflow-wrap: anywhere; }
  dd.warning { color: #96651e; }
}
.receipt-tokens { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-top: 9px; padding: 7px 8px; background: #eaf3f1; }
.receipt-tokens span { color: #66807c; font-size: 7px; }
.receipt-tokens strong { color: var(--harbor); font: 700 8px/1.3 "Cascadia Mono", monospace; text-align: right; }
.non-model-stamp {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  margin-top: 16px;
  padding: 12px;
  border: 1px solid #d2bd93;
  background: #fff9ed;
  > span { align-self: start; padding: 5px; border: 1px solid #b98431; color: #93631f; font: 700 7px/1 "Cascadia Mono", monospace; transform: rotate(-2deg); }
  strong { color: #75521e; font-size: 9px; }
  p { margin: 5px 0 0; color: #806d4f; font-size: 7px; line-height: 1.55; }
}
.unknown-rule { margin: 12px 0 0; color: #71827f; font-size: 7px; line-height: 1.6; }
.commercial-error { margin: 14px 0 0; padding: 10px 12px; border-left: 3px solid var(--red); background: #fff3f2; color: #914d47; font-size: 9px; line-height: 1.5; }

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
.analysis-error { border-color: #dfc4c3; .error-symbol { width: 48px; height: 48px; flex: 0 0 auto; color: var(--red); font-size: 28px; } > div { flex: 1; } }
.result-loading { display: grid; grid-template-columns: auto minmax(240px, 0.65fr) 1fr; }
.loading-radar { width: 52px; height: 52px; color: var(--ink); font-size: 38px; }

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
.panel-empty { display: flex; min-height: 150px; align-items: center; justify-content: center; gap: 15px; color: var(--muted); > .el-icon { width: 38px; height: 38px; color: #77797e; font-size: 24px; } p { max-width: 330px; font-size: 10px; line-height: 1.6; } &.large { min-height: 220px; } }

.result-empty { justify-content: center; min-height: 260px; .eyebrow { margin: 0 0 9px; color: #4e8884; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; } h2 { max-width: 560px; font: 700 21px/1.2 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; } > div:last-child > p:last-child { max-width: 590px; } }
.empty-orbit { display: grid; width: 82px; height: 82px; flex: 0 0 auto; place-items: center; border: 1px solid var(--rule); border-radius: 50%; background: #fff; color: var(--ink); .el-icon { font-size: 34px; } }

// Reference direction: neutral paper, precise hairlines, black reserved for decisive actions.
.proof-heading,
.console-heading,
.history-heading,
.panel-heading { border-color: var(--rule); }
.proof-heading .eyebrow,
.history-heading .eyebrow,
.panel-heading > div > span,
.result-empty .eyebrow,
.sample-index,
.citation-row a,
.citation-row strong,
.source-response summary { color: #606267; }
.diagnostic-boundary,
.sample-facts,
.receipt-tokens,
.call-receipt,
.history-run,
.history-state,
.result-empty { background: #f7f7f4; }
.diagnostic-boundary span,
.gap-ledger > header span,
.gap-ledger li small,
.gap-empty,
.receipt-total > strong,
.operation-ledger strong { color: var(--ink); }
.history-panel,
.analysis-error,
.result-loading,
.result-empty,
.result-panel,
.methodology-disclosure,
.call-receipt { border-color: var(--rule); border-radius: 8px; }
.history-run { border-color: #deded9; border-radius: 7px; }
.history-run::after { content: none; }
.history-run:hover:not(:disabled) { border-color: #9d9ea1; background: #fff; }
.history-run.active { border-color: var(--ink); background: #fff; }
.history-run .run-date,
.history-run > small { color: #696b70; }
.dimension-row > i { background: #e6e6e2; }
.dimension-row > i b { background: var(--ink); }
.dimension-row > i b.attention { background: var(--amber); }
.dimension-row > i b.critical { background: var(--red); }
.call-receipt { border-left-color: #818287; }
.receipt-tokens { border-radius: 5px; }
.non-model-stamp > span { transform: none; }
.result-header { border-radius: 8px 8px 0 0; }
.metric-track { border-radius: 0 0 8px 8px; overflow: hidden; }
.metric-card::after { background: #98999c; }
.metric-card.signal::after,
.confidence-card.confidence-high::after { background: var(--ink); }
.result-panel { overflow: hidden; }
.recommendation-list span,
.panel-heading.inverse > div > span { color: #c6c7ca; }
.mention-state.found { color: #343538; }
.mention-state.found::before { background: var(--ink); }
.evidence-list blockquote { border-left-color: var(--ink); background: #f8f8f6; }
.evidence-list span { color: #626469; }
.ownership-mark.owned { border-color: #98999c; background: #f1f1ee; color: #36373a; }

@media (max-width: 760px) {
  .visibility-workbench { padding: 20px 14px 38px; }
}

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes radar { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  .spinning, .loading-radar i:nth-child(3) { animation: none; }
  .rail-node, .history-run { transition: none; }
}

@media (max-width: 1180px) {
  .proof-grid { grid-template-columns: 1fr; }
  .sampling-config-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-track { grid-template-columns: repeat(3, 1fr); }
  .metric-card:nth-child(3) { border-right: 0; }
  .metric-card:nth-child(-n + 3) { border-bottom: 1px solid var(--rule); }
}

@media (max-width: 900px) {
  .page-heading { align-items: flex-start; flex-direction: column; }
  .local-engine-seal { width: 100%; box-sizing: border-box; }
  .result-grid { grid-template-columns: 1fr; }
  .diagnostic-body { grid-template-columns: 1fr; }
  .dimension-ledger { border-right: 0; border-bottom: 1px solid #dbe4e2; }
  .result-loading { grid-template-columns: auto 1fr; }
  .result-loading :deep(.el-skeleton) { grid-column: 1 / -1; }
}

@media (max-width: 760px) {
  .commercial-proof { padding: 20px 16px; }
  .proof-heading, .proof-actions { align-items: flex-start; flex-direction: column; }
  .proof-actions { width: 100%; }
  .diagnostic-board { grid-template-columns: 1fr; }
  .diagnostic-score { min-height: 170px; border-right: 0; border-bottom: 1px solid #b8ceca; }
  .diagnostic-score > strong { margin-top: 20px; }
  .diagnostic-boundary { align-items: flex-start; flex-direction: column; }
  .diagnostic-boundary p { text-align: left; }
  .analysis-console { grid-template-columns: 36px minmax(0, 1fr); }
  .signal-rail { padding-top: 24px; i { min-height: 150px; } }
  .rail-node { width: 20px; height: 20px; }
  .analysis-form { padding: 22px 16px 0; }
  .form-grid { grid-template-columns: 1fr; gap: 18px; }
  .live-sampling-panel { padding: 17px 14px; }
  .sampling-heading, .dispatch-confirm, .privacy-boundary { align-items: flex-start; flex-direction: column; }
  .provider-switch, .sampling-config-grid { grid-template-columns: 1fr; }
  .dispatch-slip { grid-template-columns: 42px minmax(0, 1fr); }
  .dispatch-index { padding-right: 5px; padding-left: 5px; font-size: 10px; }
  .dispatch-main { padding: 14px; }
  .dispatch-title { grid-template-columns: 1fr; }
  .dispatch-confirm :deep(.el-button) { width: 100%; }
  .execution-receipts > article { grid-template-columns: 28px minmax(0, 1fr); }
  .execution-receipts dl { grid-column: 2; }
  .manual-divider { align-items: flex-start; flex-direction: column; gap: 5px; }
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
