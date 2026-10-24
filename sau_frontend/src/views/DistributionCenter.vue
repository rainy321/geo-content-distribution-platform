<template>
  <main class="distribution-center">
    <div v-if="journeyProjectId" class="journey-return" role="region" aria-label="优化流程衔接"><div><strong>本次优化流程 · 发布</strong><p>固定这篇稿件与对应平台。提交后返回反馈步骤查看真实结果。</p><el-button text @click="leaveJourney">退出本次流程，打开通用发布中心</el-button></div><el-button @click="returnToJourney">返回优化流程</el-button></div>
    <el-alert v-if="journeyProjectId && !articlesLoading && !journeyContextMatches" type="error" :closable="false" title="本次流程的稿件、品牌或渠道不匹配，请返回优化流程重新进入。"/>
    <header class="page-heading">
      <div class="heading-copy">
        <p class="eyebrow">DISTRIBUTION ROUTING / 内容分发</p>
        <h1>发布中心</h1>
        <p>从就绪稿件创建独立渠道任务。每个平台单独记录进度，失败不会拖住其他渠道。</p>
      </div>
      <div class="live-indicator" :class="{ active: activeJobCount > 0 }">
        <span class="pulse" aria-hidden="true" />
        <div>
          <strong>{{ activeJobCount ? `${activeJobCount} 个任务处理中` : '任务轨迹已同步' }}</strong>
          <small>{{ lastSyncedLabel }}</small>
        </div>
      </div>
    </header>

    <section class="dispatch-console" aria-labelledby="dispatch-title">
      <div class="dispatch-form">
        <div class="section-heading">
          <span>CONTROL / 01</span>
          <div>
            <h2 id="dispatch-title">建立分发任务</h2>
            <p>{{ journeyProjectId ? '核对账号后，为这一篇文章创建发布任务。' : '一次选择多个平台，系统会为每个平台创建独立任务。' }}</p>
          </div>
        </div>

        <div class="field-block">
          <label for="article-select">就绪稿件</label>
          <el-select
            id="article-select"
            v-model="selectedArticleId"
            filterable
            clearable
            placeholder="选择一篇已标记就绪的文章"
            :loading="articlesLoading"
            :disabled="articlesLoading || articlesLoadError || Boolean(journeyProjectId)"
          >
            <el-option
              v-for="article in articles"
              :key="article.id"
              :label="article.title"
              :value="article.id"
            >
              <div class="article-option">
                <span>{{ article.title }}</span>
                <small>{{ article.project_name }} · GEO {{ article.geo_score }}</small>
              </div>
            </el-option>
          </el-select>
          <p v-if="articlesLoadError" class="field-error">就绪稿件暂未载入，请刷新后重试。</p>
          <div v-else-if="!articlesLoading && articles.length === 0" class="field-guidance">
            还没有就绪稿件。请先在内容库中将文章标记为“就绪”。
            <button type="button" @click="router.push('/content-library')">前往内容库</button>
          </div>
        </div>

        <article v-if="selectedArticle" class="article-proof">
          <div class="proof-id">A-{{ String(selectedArticle.id).padStart(4, '0') }}</div>
          <div class="proof-copy">
            <span>{{ selectedArticle.project_name }}</span>
            <strong>{{ selectedArticle.title }}</strong>
            <div class="proof-tags">
              <i v-for="tag in selectedArticle.tags.slice(0, 3)" :key="tag"># {{ tag }}</i>
              <i v-if="selectedArticle.tags.length === 0">尚未设置标签</i>
            </div>
          </div>
          <div :class="['proof-score', scoreTone(selectedArticle.geo_score)]">
            <b>{{ selectedArticle.geo_score }}</b>
            <small>GEO</small>
          </div>
        </article>

        <fieldset class="platform-fieldset">
          <legend>分发渠道</legend>
          <p>平台能力由后端统一声明；图文渠道和视频渠道会分别校验所需素材。</p>
          <div class="platform-grid">
            <button
              v-for="platform in visiblePlatforms"
              :key="platform.key"
              type="button"
              :class="['platform-option', platform.delivery_state, { selected: selectedPlatforms.includes(platform.key) }]"
              :aria-pressed="selectedPlatforms.includes(platform.key)"
              :aria-disabled="platform.delivery_state === 'paused'"
              :disabled="platform.delivery_state === 'paused' || Boolean(journeyProjectId)"
              @click="togglePlatform(platform.key)"
            >
              <span class="platform-code">{{ platform.code }}</span>
              <span class="platform-name">{{ platform.name }}</span>
              <small>{{ platform.delivery_label }}</small>
              <em>建议 {{ platform.safe_daily_new }}→{{ platform.safe_daily_stable }}/24h · 间隔≥{{ platform.min_interval_hours }}h</em>
              <span class="selection-mark" aria-hidden="true">
                {{ selectedPlatforms.includes(platform.key) ? '✓' : '+' }}
              </span>
            </button>
          </div>

          <div v-if="manifestPlatforms.length" class="policy-checklist" aria-label="所选渠道发布前检查">
            <article v-for="platform in manifestPlatforms" :key="platform.key">
              <div class="policy-platform">
                <span>{{ platform.code }}</span>
                <div>
                  <strong>{{ platform.name }}</strong>
                  <small>{{ platform.channel_role_label }} · {{ platform.crawl_label }}</small>
                </div>
              </div>
              <el-select
                :model-value="accountSelections[platform.key] || null"
                placeholder="选择已连接账号"
                clearable
                @update:model-value="value => setAccountSelection(platform.key, value)"
              >
                <el-option
                  v-for="account in connectedAccountsFor(platform.key)"
                  :key="account.id"
                  :label="account.account_name"
                  :value="account.id"
                />
              </el-select>
              <div class="quota-copy" :class="quotaView(platform.key).allowed ? 'safe' : 'blocked'" role="status">
                <strong>{{ quotaView(platform.key).title }}</strong>
                <small>{{ quotaView(platform.key).detail }}</small>
                <small v-if="quotaView(platform.key).nextAt">最早可用：{{ formatDate(quotaView(platform.key).nextAt, false) }}</small>
              </div>
            </article>
            <p>额度只统计本系统滚动 24 小时内的真实任务；平台后台手工发布量无法读取，请发布前自行核对。</p>
            <p>{{ scheduleEnabled ? '额度按当前计划时间核对。' : '额度按当前时间核对。' }}<el-button text :loading="accountsStatus==='loading'" @click="fetchAccounts">刷新账号额度</el-button></p>
          </div>
        </fieldset>

        <section
          :class="['asset-panel', { required: imageRequired, missing: imageRequired && !selectedImage && !autoImage }]"
          aria-labelledby="asset-title"
        >
          <div class="asset-heading">
            <span class="asset-code">ASSET / IMG</span>
            <div>
              <strong id="asset-title">共享图片资产</strong>
              <small>百家号取首图作为封面，小红书用作笔记图片；其他渠道可按各自规则使用。</small>
            </div>
            <div class="asset-heading-controls">
              <el-switch v-if="imageRequired" v-model="autoImage" active-text="自动配图" />
              <i>{{ imageRequired ? '所选渠道必需' : '可选' }}</i>
            </div>
          </div>

          <article v-if="selectedImage" class="asset-record">
            <img :src="selectedImage.previewUrl" :alt="`${selectedImage.name} 预览`" />
            <div class="asset-copy">
              <span>READY / 已入素材库</span>
              <strong>{{ selectedImage.name }}</strong>
              <small>{{ selectedImage.filename }}</small>
              <div v-if="imagePlatforms.length" class="asset-routes">
                <b v-for="platform in imagePlatforms" :key="platform.key">→ {{ platform.name }}</b>
              </div>
            </div>
            <el-button text @click="clearSelectedImage">移除</el-button>
          </article>

          <div v-else-if="imageRequired && autoImage" class="asset-empty asset-auto">
            <el-icon><MagicStick /></el-icon>
            <div>
              <strong>发布时自动匹配图片</strong>
              <span>优先按品牌、标题和标签推荐素材；没有合适图片时生成一张中性 GEO 封面。</span>
            </div>
            <div class="asset-button-pair">
              <el-button plain :loading="imageRecommending" @click="recommendImage">智能推荐</el-button>
              <el-button plain :loading="coverGenerating" @click="generateCover">生成封面</el-button>
            </div>
          </div>

          <div v-else class="asset-empty">
            <el-icon><Picture /></el-icon>
            <div>
              <strong>{{ imageRequired ? '这条路由还缺一张图片' : '需要图片时在这里上传' }}</strong>
              <span>JPG / JPEG / PNG，最大 5MB；任务只保存素材文件名，不接收任意本地路径。</span>
            </div>
            <el-button
              plain
              :icon="Upload"
              :loading="imageUploading"
              @click="openImagePicker"
            >
              {{ imageUploading ? `${imageUploadProgress}%` : '选择图片' }}
            </el-button>
          </div>
          <input
            ref="imageInput"
            class="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            @change="handleImageSelected"
          />
        </section>

        <section
          v-if="videoRequired"
          :class="['asset-panel', 'video-panel', { required: true, missing: !selectedVideo }]"
          aria-labelledby="video-asset-title"
        >
          <div class="asset-heading">
            <span class="asset-code">ASSET / VIDEO</span>
            <div>
              <strong id="video-asset-title">共享视频资产</strong>
              <small>{{ videoPlatforms.map(item => item.name).join('、') }}只接受视频，文章正文会作为描述随任务传入。</small>
            </div>
            <i>所选渠道必需</i>
          </div>

          <article v-if="selectedVideo" class="asset-record video-record">
            <div class="video-glyph">▶</div>
            <div class="asset-copy">
              <span>READY / 已入素材库</span>
              <strong>{{ selectedVideo.name }}</strong>
              <small>{{ selectedVideo.filename }}</small>
              <div class="asset-routes">
                <b v-for="platform in videoPlatforms" :key="platform.key">→ {{ platform.name }}</b>
              </div>
            </div>
            <el-button text @click="clearSelectedVideo">移除</el-button>
          </article>
          <div v-else class="asset-empty">
            <el-icon><VideoCamera /></el-icon>
            <div>
              <strong>视频渠道还缺一段视频</strong>
              <span>MP4 / MOV / MKV / WEBM，最大 150MB；同一视频可复用到本次所有视频渠道。</span>
            </div>
            <el-button plain :icon="Upload" :loading="videoUploading" @click="openVideoPicker">
              {{ videoUploading ? `${videoUploadProgress}%` : '选择视频' }}
            </el-button>
          </div>
          <input ref="videoInput" class="visually-hidden" type="file" accept="video/mp4,video/quicktime,.mp4,.mov,.mkv,.webm" @change="handleVideoSelected" />
        </section>

        <div class="schedule-panel">
          <div>
            <strong>定时发布</strong>
            <span>先创建计划任务；是否到点自动执行由下方单独授权。</span>
          </div>
          <el-switch
            v-model="scheduleEnabled"
            aria-label="启用定时发布"
            @change="handleScheduleToggle"
          />
          <el-date-picker
            v-if="scheduleEnabled"
            v-model="publishAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择发布时间"
            :disabled-date="disablePastDate"
          />
          <div v-if="scheduleEnabled" class="schedule-auto-execute">
            <el-checkbox v-model="autoExecuteAtDue" :disabled="!autoExecuteAvailable">到点自动执行</el-checkbox>
            <p>
              {{ autoExecuteAvailable
                ? '默认关闭。开启后仍需明确确认，并受服务端风控保护。'
                : '当前所选渠道仅支持到点转人工确认，不开放无人值守发布。' }}
            </p>
          </div>
        </div>

        <div class="risk-confirmation">
          <el-checkbox v-model="riskAcknowledged">
            我已核对账号后台，并理解建议量不等同于平台官方剩余额度
          </el-checkbox>
          <p>超过建议频率会提高审核、限流或账号受限风险。系统不会绕过验证码或平台风控，也不承诺账号安全、AI 收录、引用或排名。</p>
        </div>

        <div class="dispatch-actions">
          <p>
            {{ accountsStatus==='ready' && accountsLoadedKey===quotaScopeKey && accountOverview.execution_mode==='demo' ? '当前为演示模式，不访问真实平台；可不绑定账号建立演示任务。' : '真实任务需绑定账号并再次确认；执行模式以本次服务端检查为准。' }}
            <span v-if="hasExhaustedQuota" class="quota-retry-hint" role="status">24h额度按本系统已有任务预留；重试请使用下方原任务，真实发布仍需再次确认。</span>
          </p>
          <p v-if="reviewTokenMissing || dispatchError" class="job-action-error" role="alert">{{ dispatchError || '当前稿件的确认信息不完整，请重新读取稿件并核对后再创建真实任务。' }}<el-button text :loading="articlesLoading" @click="fetchArticles">重新读取稿件</el-button></p>
          <el-button
            type="primary"
            size="large"
            :icon="Promotion"
            :loading="dispatching"
            :disabled="!canDispatch"
            @click="dispatchArticle"
          >
            {{ dispatchButtonLabel }}
          </el-button>
        </div>
      </div>

      <aside class="route-manifest" aria-label="本次分发清单">
        <div class="manifest-heading">
          <span>ROUTE / 02</span>
          <h2>渠道路由</h2>
          <p>一篇稿件，多条独立轨迹。</p>
        </div>

        <div v-if="manifestPlatforms.length" class="route-line">
          <div
            v-for="(platform, index) in manifestPlatforms"
            :key="platform.key"
            class="route-node"
          >
            <div class="node-index">{{ String(index + 1).padStart(2, '0') }}</div>
            <div class="node-copy">
              <strong>{{ platform.name }}</strong>
              <span>{{ latestPlatformState(platform.key) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="route-empty">
          <div class="empty-orbit" aria-hidden="true"><span /></div>
          <strong>选择渠道后生成路由</strong>
          <p>平台之间互不阻塞，任何失败都可以单独重试。</p>
        </div>

        <div class="manifest-summary">
          <div>
            <span>ARTICLE</span>
            <strong>{{ selectedArticle ? `A-${String(selectedArticle.id).padStart(4, '0')}` : '—' }}</strong>
          </div>
          <div>
            <span>CHANNELS</span>
            <strong>{{ selectedPlatforms.length }}</strong>
          </div>
          <div>
            <span>MODE</span>
            <strong>{{ scheduleEnabled ? (autoExecuteAtDue ? 'AUTO@TIME' : 'SCHEDULED') : 'NOW' }}</strong>
          </div>
          <div>
            <span>ASSET</span>
            <strong>{{ assetSummary }}</strong>
          </div>
        </div>

        <div class="safety-note">
          <el-icon><Lock /></el-icon>
          <p><strong>安全边界</strong>验证码、扫码、风控和页面变化只会进入“待人工确认”，系统不会绕过平台验证。</p>
        </div>
      </aside>
    </section>

    <section class="job-board" aria-labelledby="jobs-title">
      <div class="board-toolbar">
        <div class="section-heading compact">
          <span>LEDGER / 03</span>
          <div>
            <h2 id="jobs-title">发布任务</h2>
            <p>{{ jobsRangeLabel }}</p>
          </div>
        </div>
        <div class="job-filters">
          <el-select
            v-model="jobFilters.platform"
            clearable
            placeholder="全部平台"
            aria-label="按平台筛选"
            @change="applyJobFilters"
          >
            <el-option v-for="platform in platforms" :key="platform.key" :label="platform.name" :value="platform.key" />
          </el-select>
          <el-select
            v-model="jobFilters.status"
            clearable
            placeholder="全部状态"
            aria-label="按状态筛选"
            @change="applyJobFilters"
          >
            <el-option v-for="status in statusOptions" :key="status.value" :label="status.label" :value="status.value" />
          </el-select>
          <el-button :icon="Refresh" :loading="jobsRefreshing" @click="fetchJobs()">刷新</el-button>
        </div>
      </div>

      <p v-if="jobsLoadError && jobs.length" class="jobs-stale" role="status">任务状态刷新失败，下面保留上次记录。请先刷新核对原任务，勿重复创建或提交。<el-button text @click="fetchJobs()">重新读取</el-button></p>
      <div v-if="jobsLoading" class="jobs-loading" aria-live="polite">
        <div v-for="index in 4" :key="index" class="job-skeleton"><el-skeleton :rows="2" animated /></div>
      </div>

      <div v-else-if="jobsLoadError && !jobs.length" class="board-state is-error" role="alert">
        <div class="state-symbol">!</div>
        <div>
          <h3>任务轨迹没有载入</h3>
          <p>检查后端服务后重新加载。已经创建的任务不会丢失。</p>
        </div>
        <el-button type="primary" @click="fetchJobs()">重新加载</el-button>
      </div>

      <div v-else-if="jobs.length === 0" class="board-state">
        <div class="state-symbol">↗</div>
        <div>
          <h3>{{ hasJobFilters ? '当前筛选下没有任务' : '第一条分发轨迹等待建立' }}</h3>
          <p>{{ hasJobFilters ? '清除筛选查看其他状态。' : '选择稿件与渠道后，任务会按平台逐条出现在这里。' }}</p>
        </div>
        <el-button v-if="hasJobFilters" @click="clearJobFilters">清除筛选</el-button>
      </div>

      <div v-else class="job-list">
        <article v-for="job in jobs" :key="job.job_id" class="job-row">
          <div :class="['status-signal', job.status]">
            <span />
            <small>{{ statusLabel(job.status) }}</small>
          </div>

          <div class="job-identity">
            <span>JOB-{{ String(job.job_id).padStart(5, '0') }}</span>
            <strong>{{ platformName(job.platform) }}</strong>
            <small v-if="job.account_name" class="account-mark">{{ job.account_name }}</small>
            <small v-if="job.demo" class="demo-mark">DEMO / 演示任务</small>
            <small v-if="job.images?.length" class="asset-mark">{{ job.images.length }} IMG / 已附素材</small>
            <small v-if="job.video" class="asset-mark">01 VIDEO / 已附素材</small>
            <small v-if="job.auto_execute" class="schedule-mark">AUTO@TIME / 已授权</small>
            <small v-if="job.status === 'success' && job.public_url_recorded" class="public-mark">PUBLIC URL / 可进入抓取验证</small>
            <small v-else-if="job.status === 'success'" class="private-mark">仅平台后台证据 / 不计 AI 可抓取</small>
          </div>

          <div class="job-article">
            <span>{{ job.article_title || `文章 #${job.article_id}` }}</span>
            <small>{{ job.publish_at ? `计划 ${formatDate(job.publish_at, false)}` : `创建 ${formatDate(job.created_at)}` }}</small>
          </div>

          <div class="job-progress">
            <div class="progress-copy">
              <span>{{ job.message || defaultStatusMessage(job.status) }}</span>
              <b>{{ statusProgress(job.status) }}%</b>
            </div>
            <div class="progress-track" :class="job.status">
              <span :style="{ width: `${statusProgress(job.status)}%` }" />
            </div>
            <details v-if="job.message" class="job-message-details"><summary>展开任务说明</summary><p>{{ job.message }}</p></details>
            <p v-if="jobActionErrors[job.job_id]" class="job-action-error" role="alert">{{ jobActionErrors[job.job_id] }}</p>
          </div>

          <div class="job-time">
            <span>{{ job.status==='queued' && job.started_at ? '上次尝试' : job.finished_at ? '完成时间' : job.started_at ? '开始时间' : '创建时间' }}</span>
            <time>{{ formatDate(job.finished_at || job.started_at || job.created_at) }}</time>
          </div>

          <div class="job-actions">
            <el-button
              v-if="['failed', 'need_action'].includes(job.status)"
              type="primary"
              plain
              :loading="busyJobIds.has(job.job_id)"
              :disabled="jobsLoadError"
              @click="retryJob(job)"
            >
              {{ job.demo ? (job.status === 'need_action' ? '确认后继续' : '重新发布') : '重新检查 / 排队' }}
            </el-button>
            <el-button
              v-else-if="job.demo && job.status === 'queued'"
              type="primary"
              :loading="busyJobIds.has(job.job_id)"
              @click="executeDemoJob(job)"
            >
              执行演示
            </el-button>
            <el-button
              v-else-if="job.can_execute_real"
              type="danger"
              plain
              :loading="busyJobIds.has(job.job_id)"
              :disabled="jobsLoadError"
              @click="executeRealJob(job)"
            >
              确认真实发布
            </el-button>
            <div v-if="canCheckResult(job)" class="result-check-action">
              <el-button
                plain
                :loading="busyJobIds.has(job.job_id) || job.result_checking"
                :disabled="busyJobIds.has(job.job_id) || job.result_checking"
                title="只读打开可见浏览器核对平台结果。需要登录或验证时请在窗口正常完成，不会再次填写或提交。"
                @click="checkPlatformResult(job)"
              >核对平台结果</el-button>
              <small>只读核对，不会再次提交</small>
            </div>
            <el-button v-if="job.url" text :icon="Link" @click="openResult(job.url)">查看结果</el-button>
            <span v-if="!job.url && !['failed', 'need_action', 'queued'].includes(job.status) && !canCheckResult(job)" class="no-action">无需操作</span>
          </div>
        </article>
      </div>

      <footer v-if="jobPagination.total_pages > 1" class="board-pagination">
        <span>PAGE {{ String(jobPagination.page).padStart(2, '0') }} / {{ String(jobPagination.total_pages).padStart(2, '0') }}</span>
        <el-pagination
          v-model:current-page="jobPagination.page"
          background
          layout="prev, pager, next"
          :page-size="jobPagination.page_size"
          :total="jobPagination.total"
          @current-change="fetchJobs"
        />
      </footer>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Lock, MagicStick, Picture, Promotion, Refresh, Upload, VideoCamera } from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { accountApi } from '@/api/account'
import { materialApi } from '@/api/material'
import { publishApi } from '@/api/publish'
import { distributionActionError, distributionArticleSnapshot, distributionJobSnapshot, distributionOverviewMatches, distributionQuota, distributionReference, validDistributionToken } from '@/utils/geoDistributionState'

const route = useRoute()
const router = useRouter()

const platformCodes = {
  zhihu: 'ZH', toutiao: 'TT', baijiahao: 'BJ', sohu: 'SH', xiaohongshu: 'XH',
  douyin: 'DY', kuaishou: 'KS', bilibili: 'BI', channels: 'WX', tiktok: 'TK'
}
const modeNames = { article: '文章', image_article: '图文', image_note: '图片笔记', video: '视频' }
const platforms = ref([])
const accountOverview = ref({ platforms: [], accounts: [] })
const accountSelections = reactive({})
const accountsStatus=ref('idle'),accountsLoadedKey=ref('')

const statusOptions = [
  { value: 'queued', label: '排队中' },
  { value: 'processing', label: '发布中' },
  { value: 'success', label: '发布成功' },
  { value: 'failed', label: '发布失败' },
  { value: 'need_action', label: '待人工确认' },
  { value: 'scheduled', label: '计划中' }
]

const articles = ref([])
const articlesLoading = ref(false)
const articlesLoadError = ref(false)
const selectedArticleId = ref(null)
const selectedPlatforms = ref([])
const journeyProjectId = computed(() => { const value=Number(route.query.journey_project);return Number.isSafeInteger(value)&&value>0?value:null })
const journeyArticleId = computed(() => { const value=Number(route.query.article_id);return Number.isSafeInteger(value)&&value>0?value:null })
const journeyPlatform = computed(() => typeof route.query.platform==='string' ? route.query.platform : '')
const returnToJourney = () => router.push({path:'/journey',query:{journey_project:String(journeyProjectId.value)}})
const leaveJourney = async () => { await router.replace({path:'/publish-center',query:{}});selectedArticleId.value=null;selectedPlatforms.value=[];jobPagination.page=1;await Promise.allSettled([fetchArticles(),fetchJobs()]) }
const scheduleEnabled = ref(false)
const publishAt = ref('')
const autoExecuteAtDue = ref(false)
const riskAcknowledged = ref(false)
const dispatching = ref(false)
const dispatchError=ref('')
const imageInput = ref(null)
const selectedImage = ref(null)
const autoImage = ref(true)
const imageUploading = ref(false)
const imageUploadProgress = ref(0)
const imageRecommending = ref(false)
const coverGenerating = ref(false)
const videoInput = ref(null)
const selectedVideo = ref(null)
const videoUploading = ref(false)
const videoUploadProgress = ref(0)

const jobs = ref([])
const jobsLoading = ref(false)
const jobsRefreshing = ref(false)
const jobsLoadError = ref(false)
const lastSyncedAt = ref(null)
const busyJobIds = ref(new Set())
const jobActionErrors=ref({})
const jobEpochs=ref(new Map()),dispatchEpoch=ref(0)
let observedJobKeys=new Map()
const jobFilters = reactive({ platform: '', status: '' })
const jobPagination = reactive({ page: 1, page_size: 20, total: 0, total_pages: 0 })
let pollTimer = null
let pollingStopped = false
let contextEpoch=0,accountsRequest=0,jobsRequest=0,articlesRequest=0,platformsRequest=0,imageRequest=0,mounted=false
const routeContextKey=computed(()=>JSON.stringify([route.query.journey_project||'',route.query.article_id||'',route.query.platform||'']))
const viewScope=()=>({epoch:contextEpoch,key:routeContextKey.value})
const sameView=scope=>!pollingStopped&&scope.epoch===contextEpoch&&scope.key===routeContextKey.value
const quotaReference=computed(()=>distributionReference(scheduleEnabled.value,publishAt.value))
const quotaScopeKey=computed(()=>JSON.stringify([routeContextKey.value,selectedArticleId.value,quotaReference.value.key]))
const jobsScopeKey=computed(()=>JSON.stringify([routeContextKey.value,jobFilters.platform,jobFilters.status,jobPagination.page,jobPagination.page_size]))

const selectedArticle = computed(() => (
  articles.value.find(article => article.id === selectedArticleId.value) || null
))
const reviewTokenMissing=computed(()=>accountOverview.value.execution_mode==='real'&&selectedArticle.value?.status==='ready'&&!validDistributionToken(selectedArticle.value.review_fingerprint))
const visiblePlatforms = computed(() => journeyProjectId.value ? platforms.value.filter(item=>item.key===journeyPlatform.value) : platforms.value)
const journeyContextMatches = computed(() => !journeyProjectId.value || Boolean(
  journeyArticleId.value && selectedArticle.value?.id===journeyArticleId.value
  && selectedArticle.value?.project_id===journeyProjectId.value
  && selectedPlatforms.value.length===1 && selectedPlatforms.value[0]===journeyPlatform.value
  && platforms.value.some(item=>item.key===journeyPlatform.value && item.delivery_state!=='paused')
))

const manifestPlatforms = computed(() => (
  platforms.value.filter(platform => selectedPlatforms.value.includes(platform.key))
))

const imagePlatforms = computed(() => (
  manifestPlatforms.value.filter(platform => platform.requires_images)
))

const imageRequired = computed(() => imagePlatforms.value.length > 0)
const videoPlatforms = computed(() => (
  manifestPlatforms.value.filter(platform => platform.requires_video)
))
const videoRequired = computed(() => videoPlatforms.value.length > 0)
const autoExecuteAvailable = computed(() => Boolean(
  manifestPlatforms.value.length
  && manifestPlatforms.value.every(platform => platform.supports_auto_execute)
))
const hasExhaustedQuota = computed(() => manifestPlatforms.value.some(platform => {
  const account = selectedAccountFor(platform.key)
  return distributionQuota(account?.publish_policy).state==='limit'
}))
const quotaView=key=>{
  if(!quotaReference.value.valid)return {allowed:false,title:'先选择计划时间',detail:'选定时间后会重新核对额度。'}
  if(accountsStatus.value==='loading')return {allowed:false,title:'正在核对账号额度',detail:'读取完成后才能创建新任务。'}
  if(accountsStatus.value==='error')return {allowed:false,title:'账号额度读取失败',detail:'请刷新后重试，原任务仍保留。'}
  if(accountsStatus.value!=='ready'||accountsLoadedKey.value!==quotaScopeKey.value)return {allowed:false,title:'额度需要重新核对',detail:'请刷新当前时间的账号额度。'}
  if(accountOverview.value.execution_mode==='demo')return {allowed:true,title:'演示模式，不访问真实平台',detail:'无需媒体账号；演示结果不代表真实发布。'}
  if(accountOverview.value.execution_mode!=='real')return {allowed:false,title:'执行模式尚未确认',detail:'请重新读取服务端发布模式。'}
  const account=selectedAccountFor(key)
  return account?distributionQuota(account.publish_policy,accountOverview.value.reference_at):{allowed:false,title:'先绑定已连接账号',detail:'连接账号后再核对当前时间的发布额度。'}
}
const quotaBlock=computed(()=>manifestPlatforms.value.map(platform=>quotaView(platform.key)).find(value=>!value.allowed)||null)
const dispatchKey=computed(()=>JSON.stringify([routeContextKey.value,selectedArticleId.value,distributionArticleSnapshot(selectedArticle.value),selectedPlatforms.value,
  selectedPlatforms.value.map(key=>accountSelections[key]),scheduleEnabled.value,publishAt.value,autoExecuteAtDue.value,
  selectedImage.value?.filename,selectedVideo.value?.filename,autoImage.value,riskAcknowledged.value]))
const jobActionKey=job=>distributionJobSnapshot(job)
const currentJob=job=>!jobsLoadError.value&&!jobsLoading.value&&!jobsRefreshing.value&&jobs.value.some(value=>jobActionKey(value)===jobActionKey(job))
const setJobActionError=(id,message)=>{jobActionErrors.value={...jobActionErrors.value,[id]:message}}

const assetSummary = computed(() => {
  const parts = []
  if (selectedImage.value) parts.push('01 IMG')
  else if (imageRequired.value && autoImage.value) parts.push('AUTO IMG')
  if (selectedVideo.value) parts.push('01 VIDEO')
  return parts.join(' + ') || 'NONE'
})

const canDispatch = computed(() => Boolean(
  selectedArticleId.value
  && !articlesLoading.value
  && !articlesLoadError.value
  && !reviewTokenMissing.value
  && !dispatchError.value
  && selectedArticle.value?.status==='ready'
  && journeyContextMatches.value
  && selectedPlatforms.value.length
  && (!imageRequired.value || selectedImage.value || autoImage.value)
  && (!videoRequired.value || selectedVideo.value)
  && (!scheduleEnabled.value || publishAt.value)
  && riskAcknowledged.value
  && !quotaBlock.value
  && !imageUploading.value
  && !videoUploading.value
  && !dispatching.value
))

const dispatchButtonLabel = computed(() => {
  if (!journeyContextMatches.value) return '请返回优化流程核对稿件与渠道'
  if (!selectedArticleId.value) return '先选择就绪稿件'
  if (selectedArticle.value?.status!=='ready') return '请查看下方已有任务，勿重复创建'
  if(reviewTokenMissing.value||dispatchError.value)return '请重新读取并核对稿件'
  if (!selectedPlatforms.value.length) return '至少选择一个渠道'
  if (quotaBlock.value) return quotaBlock.value.title
  if (!riskAcknowledged.value) return '先确认发布风险边界'
  if (imageUploading.value) return '图片上传中'
  if (videoUploading.value) return '视频上传中'
  if (imageRequired.value && !selectedImage.value && !autoImage.value) return '先补齐渠道图片'
  if (videoRequired.value && !selectedVideo.value) return '先补齐渠道视频'
  if (scheduleEnabled.value && !publishAt.value) return '选择计划发布时间'
  if (scheduleEnabled.value && autoExecuteAtDue.value) {
    return `创建 ${selectedPlatforms.value.length} 个自动计划任务`
  }
  return scheduleEnabled.value
    ? `创建 ${selectedPlatforms.value.length} 个计划任务`
    : `分发到 ${selectedPlatforms.value.length} 个渠道`
})

const activeJobCount = computed(() => (
  jobs.value.filter(job => ['queued', 'processing'].includes(job.status)).length
))

const hasJobFilters = computed(() => Boolean(jobFilters.platform || jobFilters.status))

const lastSyncedLabel = computed(() => {
  if (!lastSyncedAt.value) return '等待首次同步'
  return `最近同步 ${lastSyncedAt.value.toLocaleTimeString('zh-CN', { hour12: false })}`
})

const jobsRangeLabel = computed(() => {
  if (!jobPagination.total) return '还没有符合条件的发布任务'
  const start = (jobPagination.page - 1) * jobPagination.page_size + 1
  const end = Math.min(jobPagination.page * jobPagination.page_size, jobPagination.total)
  return `显示第 ${start}–${end} 条，共 ${jobPagination.total} 条任务`
})

const fetchArticles = async () => {
  if(pollingStopped)return
  const scope=viewScope(),request=++articlesRequest
  const current=()=>sameView(scope)&&request===articlesRequest
  articlesLoading.value = true
  articlesLoadError.value = false
  try {
    if(journeyProjectId.value){
      if(!journeyArticleId.value)throw new Error('优化流程缺少稿件编号')
      const response=await articleApi.getArticle(journeyArticleId.value)
      if(!current())return
      if(response.data?.project_id!==journeyProjectId.value)throw new Error('稿件与当前品牌不一致')
      articles.value=[response.data];selectedArticleId.value=journeyArticleId.value;dispatchError.value=''
      return
    }
    const response = await articleApi.getArticles({ status: 'ready', page: 1, page_size: 100 })
    if(!current())return
    articles.value = response.data?.items || []
    dispatchError.value=''
    const queryId = Number(route.query.article_id)
    if (!selectedArticleId.value && Number.isInteger(queryId) && articles.value.some(item => item.id === queryId)) {
      selectedArticleId.value = queryId
    }
  } catch (error) {
    if(!current())return
    articlesLoadError.value = true
    console.error('加载就绪稿件失败:', error)
  } finally {
    if(current())articlesLoading.value = false
  }
}

const fetchPlatforms = async () => {
  if(pollingStopped)return
  const scope=viewScope(),request=++platformsRequest
  try {
    const response = await publishApi.getPlatforms()
    if(!sameView(scope)||request!==platformsRequest)return
    platforms.value = (response.data || []).map(item => ({
      ...item,
      code: platformCodes[item.key] || item.key.slice(0, 2).toUpperCase(),
      note: `${item.priority} · ${modeNames[item.content_mode] || item.content_mode}`
    }))
    syncDefaultAccountSelections()
  } catch (error) {
    console.error('加载平台能力失败:', error)
  }
}

const fetchAccounts = async () => {
  if(pollingStopped)return
  const reference=quotaReference.value,scope=viewScope(),key=quotaScopeKey.value,request=++accountsRequest
  if(!reference.valid){accountsStatus.value='idle';return}
  accountsStatus.value='loading'
  const current=()=>sameView(scope)&&request===accountsRequest&&key===quotaScopeKey.value
  try {
    const response = await accountApi.getMediaAccounts(reference.params)
    if(!current())return
    if(!distributionOverviewMatches(response.data,reference))throw new Error('账号额度未对应当前计划时间')
    accountOverview.value = response.data || { platforms: [], accounts: [] }
    accountsLoadedKey.value=key;accountsStatus.value='ready'
    syncDefaultAccountSelections()
  } catch (error) {
    if(!current())return
    accountsStatus.value='error'
    console.error('加载账号额度失败:', error)
  }
}

const fetchJobs = async (pageOrSilent = false) => {
  if(pollingStopped)return
  const silent = pageOrSilent === true
  if (typeof pageOrSilent === 'number') jobPagination.page = pageOrSilent
  const scope=viewScope(),key=jobsScopeKey.value,request=++jobsRequest
  const current=()=>sameView(scope)&&request===jobsRequest&&key===jobsScopeKey.value
  if (silent) jobsRefreshing.value = false
  else if (jobs.value.length) jobsRefreshing.value = true
  else jobsLoading.value = true
  if(!silent)jobsLoadError.value = false

  const params = {
    page: jobPagination.page,
    page_size: jobPagination.page_size
  }
  if (journeyProjectId.value && Number.isSafeInteger(Number(route.query.article_id))) params.article_id=Number(route.query.article_id)
  if (jobFilters.platform) params.platform = jobFilters.platform
  if (jobFilters.status) params.status = jobFilters.status

  try {
    const response = await publishApi.getJobs(params)
    if(!current())return
    jobs.value = response.data?.items || []
    Object.assign(jobPagination, response.data?.pagination || {})
    lastSyncedAt.value = new Date()
    jobsLoadError.value=false
  } catch (error) {
    if(!current())return
    jobsLoadError.value = true
    console.error('加载发布任务失败:', error)
  } finally {
    if(current()){jobsLoading.value = false;jobsRefreshing.value = false}
  }
}

const togglePlatform = (key) => {
  if(journeyProjectId.value)return
  const platform = platforms.value.find(item => item.key === key)
  if (platform?.delivery_state === 'paused') {
    ElMessage.warning(`${platform.name}真实自动发布当前暂停，可在内容创作中生成平台稿后手工发布`)
    return
  }
  selectedPlatforms.value = selectedPlatforms.value.includes(key)
    ? selectedPlatforms.value.filter(item => item !== key)
    : [...selectedPlatforms.value, key]
}

const connectedAccountsFor = (platformKey) => (
  (accountOverview.value.accounts || [])
    .filter(account => account.platform_key === platformKey && account.status === 'connected')
    .sort((left, right) => {
      const checked = String(right.last_checked_at || '').localeCompare(String(left.last_checked_at || ''))
      return checked || right.id - left.id
    })
)

const selectedAccountFor = (platformKey) => {
  const accountId = accountSelections[platformKey]
  return connectedAccountsFor(platformKey).find(account => account.id === accountId) || null
}

const setAccountSelection = (platformKey, accountId) => {
  accountSelections[platformKey] = accountId || null
  riskAcknowledged.value = false
}

const syncDefaultAccountSelections = () => {
  for (const platform of platforms.value) {
    const accounts = connectedAccountsFor(platform.key)
    if (!accounts.some(account => account.id === accountSelections[platform.key])) {
      accountSelections[platform.key] = accounts[0]?.id || null
    }
  }
}

const handleScheduleToggle = (enabled) => {
  if (!enabled) {
    publishAt.value = ''
    autoExecuteAtDue.value = false
  }
  if (!autoExecuteAvailable.value) autoExecuteAtDue.value = false
}

const openImagePicker = () => {
  if (!imageUploading.value) imageInput.value?.click()
}

const handleImageSelected = async (event) => {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const isSupportedImage = ['image/jpeg', 'image/png'].includes(file.type)
    || /\.(jpe?g|png)$/i.test(file.name || '')
  if (!isSupportedImage) {
    ElMessage.error('图片仅支持 JPG、JPEG、PNG 格式')
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.error('图片不能超过 5MB')
    return
  }

  imageUploading.value = true
  imageUploadProgress.value = 0
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await materialApi.uploadMaterial(formData, progressEvent => {
      if (progressEvent.total) {
        imageUploadProgress.value = Math.round(
          (progressEvent.loaded * 100) / progressEvent.total
        )
      }
    })
    const filename = String(response.data?.filepath || '').trim()
    if (!filename) throw new Error('上传接口没有返回素材文件名')
    selectedImage.value = {
      filename,
      name: file.name,
      previewUrl: materialApi.getMaterialPreviewUrl(filename)
    }
    imageUploadProgress.value = 100
    ElMessage.success('图片已进入素材库，将随所选渠道任务保存')
  } catch (error) {
    console.error('上传分发图片失败:', error)
  } finally {
    imageUploading.value = false
  }
}

const clearSelectedImage = () => {
  selectedImage.value = null
  imageUploadProgress.value = 0
}

const materialRecordToImage = record => ({
  filename: record.file_path,
  name: record.filename,
  previewUrl: materialApi.getMaterialPreviewUrl(record.file_path)
})

const recommendImage = async ({ silent = false } = {}) => {
  if (pollingStopped||!selectedArticleId.value || imageRecommending.value) return
  const scope=viewScope(),articleId=selectedArticleId.value,request=++imageRequest
  const current=()=>sameView(scope)&&articleId===selectedArticleId.value&&request===imageRequest
  imageRecommending.value = true
  try {
    const response = await articleApi.recommendImages(articleId)
    if(!current())return
    const first = response.data?.items?.[0]
    if (first) {
      selectedImage.value = materialRecordToImage(first)
      if (!silent) ElMessage.success('已按品牌和文章关键词匹配素材')
    } else if (!silent) {
      ElMessage.info('素材库暂无匹配图片，可直接生成封面')
    }
  } catch (error) {
    console.error('推荐文章图片失败:', error)
  } finally {
    if(request===imageRequest)imageRecommending.value = false
  }
}

const generateCover = async () => {
  if (pollingStopped||!selectedArticleId.value || coverGenerating.value) return
  const scope=viewScope(),articleId=selectedArticleId.value,request=++imageRequest
  coverGenerating.value = true
  try {
    const response = await articleApi.generateCover(articleId)
    if(!sameView(scope)||articleId!==selectedArticleId.value||request!==imageRequest)return
    selectedImage.value = materialRecordToImage(response.data)
    ElMessage.success('中性 GEO 封面已生成并进入素材库')
  } catch (error) {
    console.error('生成文章封面失败:', error)
  } finally {
    if(request===imageRequest)coverGenerating.value = false
  }
}

const openVideoPicker = () => {
  if (!videoUploading.value) videoInput.value?.click()
}

const handleVideoSelected = async (event) => {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!/\.(mp4|mov|mkv|webm)$/i.test(file.name || '')) {
    ElMessage.error('视频仅支持 MP4、MOV、MKV、WEBM 格式')
    return
  }
  if (file.size > 150 * 1024 * 1024) {
    ElMessage.error('视频不能超过 150MB')
    return
  }
  videoUploading.value = true
  videoUploadProgress.value = 0
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await materialApi.uploadMaterial(formData, progressEvent => {
      if (progressEvent.total) {
        videoUploadProgress.value = Math.round((progressEvent.loaded * 100) / progressEvent.total)
      }
    })
    const filename = String(response.data?.filepath || '').trim()
    if (!filename) throw new Error('上传接口没有返回素材文件名')
    selectedVideo.value = { filename, name: file.name }
    videoUploadProgress.value = 100
    ElMessage.success('视频已进入素材库，将复用于所选视频渠道')
  } catch (error) {
    console.error('上传分发视频失败:', error)
  } finally {
    videoUploading.value = false
  }
}

const clearSelectedVideo = () => {
  selectedVideo.value = null
  videoUploadProgress.value = 0
}

const dispatchArticle = async () => {
  if (!canDispatch.value) return
  const demo=accountOverview.value.execution_mode==='demo',reviewFingerprint=selectedArticle.value?.review_fingerprint
  if(!demo&&!validDistributionToken(reviewFingerprint)){dispatchError.value='当前稿件的确认信息不完整，请重新读取并核对稿件。';return}
  const scope=viewScope(),key=dispatchKey.value,intent=dispatchEpoch.value,reference=quotaReference.value
  const current=()=>sameView(scope)&&key===dispatchKey.value&&intent===dispatchEpoch.value
  const requests=selectedPlatforms.value.map(platform=>({article_id:selectedArticleId.value,platform,
    images:selectedImage.value?[selectedImage.value.filename]:[],
    video:platforms.value.find(item=>item.key===platform)?.requires_video?(selectedVideo.value?.filename||''):'',
    auto_image:autoImage.value,publish_at:reference.at,account_id:selectedAccountFor(platform)?.id||null,
    ...(!demo?{expected_review_fingerprint:reviewFingerprint}:{})}))
  dispatching.value=true
  try {
  let confirmedAutoExecute = false
  if (scheduleEnabled.value && autoExecuteAtDue.value) {
    try {
      await ElMessageBox.confirm(
        `这将授权系统在 ${publishAt.value} 到达后，自动尝试把《${selectedArticle.value?.title || ''}》发布到 ${selectedPlatforms.value.length} 个所选渠道。任务不会提前执行，失败或状态不明时不会自动重试。是否确认？`,
        '确认定时自动执行',
        {
          confirmButtonText: '确认到点自动执行',
          cancelButtonText: '只创建普通计划',
          type: 'warning',
          distinguishCancelAndClose: true
        }
      )
      confirmedAutoExecute = true
    } catch (action) {
      if (action !== 'cancel') return
    }
  }
  if(!current()||quotaBlock.value)return
  let createdCount = 0
  let failedCount = 0
  let demoCount = 0

  for (const payload of requests) {
    if(!current())return
    try {
      const response = await publishApi.createJob({...payload,auto_execute:confirmedAutoExecute})
      if(!current())return
      let job = response.data
      createdCount += 1
      if (job.demo) demoCount += 1
      if (job.demo && job.status === 'queued') {
        const executed = await publishApi.executeDemoJob(job.job_id)
        job = executed.data
      }
    } catch (error) {
      if(!current())return
      failedCount += 1
      if(error?.response?.status===409){dispatchError.value='稿件或发布条件已更新，请重新读取稿件并核对当前版本后再创建；不会自动重试。';break}
      console.error(`创建 ${payload.platform} 发布任务失败:`, error)
    }
  }

  await Promise.allSettled([fetchJobs(true), fetchAccounts()])
  if(!current())return
  if (createdCount && !failedCount) {
    ElMessage.success(
      demoCount
        ? `${createdCount} 个演示任务已完成，未访问真实平台`
        : `${createdCount} 个发布任务已创建`
    )
  } else if (createdCount) {
    ElMessage.warning(`已创建 ${createdCount} 个任务，${failedCount} 个渠道创建失败`)
  } else if(failedCount){
    ElMessage.error('尚未确认创建结果，请先刷新原任务记录核对，勿重复创建。')
  }
  } finally {dispatching.value=false}
}

const executeDemoJob = async (job) => {
  if(pollingStopped||busyJobIds.value.has(job.job_id)||!currentJob(job))return
  const scope=viewScope()
  markJobBusy(job.job_id, true)
  setJobActionError(job.job_id,'')
  try {
    await publishApi.executeDemoJob(job.job_id)
    if(!sameView(scope))return
    ElMessage.success('演示任务已完成，未访问真实平台')
    await fetchJobs(true)
  } catch (error) {
    if(sameView(scope))setJobActionError(job.job_id,distributionActionError('execute',error))
    console.error('执行演示任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const executeRealJob = async (job) => {
  if(pollingStopped || busyJobIds.value.has(job.job_id)||!currentJob(job))return
  const scope=viewScope()
  const token=job.confirmation_snapshot
  if(!validDistributionToken(token)){setJobActionError(job.job_id,'原任务的确认信息不完整，请刷新任务列表并重新核对后再确认。');return}
  const authorization={id:job.job_id,key:jobActionKey(job),epoch:jobEpochs.value.get(job.job_id)||0,token}
  markJobBusy(job.job_id, true)
  setJobActionError(job.job_id,'')
  const policy = platforms.value.find(platform => platform.key === job.platform)
  try {
    try {await ElMessageBox.confirm(
      `将使用已连接的${platformName(job.platform)}账号真实发布《${job.article_title || `文章 #${job.article_id}`}》。本系统建议每个新账号滚动 24 小时不超过 ${policy?.safe_daily_new || 1} 条，间隔至少 ${policy?.min_interval_hours || 6} 小时；平台后台手工发布量未知。平台可能要求扫码、验证码或人工确认，且无法承诺账号安全或 AI 收录。是否继续？`,
      '确认真实发布',
      {
        confirmButtonText: '确认并执行',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true
      }
    )}catch{return}
    if(!sameView(scope)||!currentJob(job)||authorization.key!==jobActionKey(jobs.value.find(item=>item.job_id===authorization.id))
      ||authorization.epoch!==(jobEpochs.value.get(authorization.id)||0))return
    const response = await publishApi.executeRealJob(authorization.id,authorization.token)
    if(!sameView(scope))return
    const result = response.data
    if (result.status === 'success') ElMessage.success(`${platformName(job.platform)}真实发布成功`)
    else if (result.status === 'need_action') ElMessage.warning(result.message || '需要人工确认后继续')
    else if (result.status === 'processing') ElMessage.info(result.message || '平台状态仍在确认中')
    else ElMessage.error(result.message || '真实发布未完成')
    await fetchJobs(true)
  } catch (error) {
    if(sameView(scope))setJobActionError(job.job_id,distributionActionError('execute',error))
    console.error('执行真实发布任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const retryJob = async (job) => {
  if(pollingStopped || busyJobIds.value.has(job.job_id)||!currentJob(job))return
  const scope=viewScope()
  markJobBusy(job.job_id, true)
  setJobActionError(job.job_id,'')
  try {
    const response = await publishApi.retryJob(job.job_id)
    if(!sameView(scope))return
    const retried = response.data
    if (retried.demo) {
      await publishApi.executeDemoJob(retried.job_id)
      if(!sameView(scope))return
      ElMessage.success('演示任务已重新执行')
    } else {
      ElMessage.success('任务已重新排队；真实发布仍需再次确认')
    }
    await fetchJobs(true)
  } catch (error) {
    if(sameView(scope))setJobActionError(job.job_id,distributionActionError('retry',error))
    console.error('重试发布任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const canCheckResult = job => !job.demo && job.platform === 'toutiao' && job.status === 'processing'
const checkPlatformResult = async (job) => {
  if (pollingStopped||!canCheckResult(job) || job.result_checking || busyJobIds.value.has(job.job_id)) return
  const scope=viewScope()
  markJobBusy(job.job_id, true)
  setJobActionError(job.job_id,'')
  try {
    const response = await publishApi.checkResult(job.job_id)
    if(!sameView(scope))return
    ElMessage.info(`${response.data?.message || '已开始只读核对平台结果'} 请在可见窗口正常完成登录或验证；不会再次填写或提交。`)
    await fetchJobs(true)
  } catch (error) {
    if(sameView(scope))setJobActionError(job.job_id,distributionActionError('check',error))
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const markJobBusy = (jobId, busy) => {
  const next = new Set(busyJobIds.value)
  if (busy) next.add(jobId)
  else next.delete(jobId)
  busyJobIds.value = next
}

const applyJobFilters = () => {
  jobPagination.page = 1
  fetchJobs()
}

const clearJobFilters = () => {
  jobFilters.platform = ''
  jobFilters.status = ''
  applyJobFilters()
}

const platformName = (key) => platforms.value.find(platform => platform.key === key)?.name || key
const statusLabel = (status) => statusOptions.find(item => item.value === status)?.label || status

const latestPlatformState = (platformKey) => {
  const job = jobs.value.find(item => item.platform === platformKey)
  return job ? statusLabel(job.status) : '等待创建任务'
}

const statusProgress = (status) => ({
  scheduled: 10,
  queued: 18,
  processing: 72,
  need_action: 72,
  success: 100,
  failed: 100
}[status] || 0)

const defaultStatusMessage = (status) => ({
  scheduled: '等待计划时间',
  queued: '等待执行器领取',
  processing: '平台正在处理',
  need_action: '需要人工确认后继续',
  success: '发布流程已完成',
  failed: '发布流程失败'
}[status] || '状态待确认')

const scoreTone = (score) => {
  if (score >= 80) return 'strong'
  if (score >= 60) return 'medium'
  return 'weak'
}

const disablePastDate = (date) => date.getTime() < Date.now() - 24 * 60 * 60 * 1000

const formatDate = (value, assumeUtc = true) => {
  if (!value) return '—'
  const text = String(value)
  const normalized = assumeUtc && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,9})?$/.test(text)
    ? `${text.replace(' ', 'T')}Z`
    : text.includes(' ')
      ? text.replace(' ', 'T')
      : text
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date)
}

const openResult = (url) => {
  if (/^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer')
}

const scheduleJobPoll = () => {
  if (pollingStopped) return
  pollTimer = window.setTimeout(async () => {
    pollTimer = null
    await fetchJobs(true)
    if(!pollingStopped&&!dispatching.value&&quotaReference.value.valid)await fetchAccounts()
    scheduleJobPoll()
  }, 5000)
}

onMounted(async () => {
  pollingStopped = false
  mounted=true
  await Promise.allSettled([fetchPlatforms(), fetchAccounts(), fetchArticles(), fetchJobs()])
  if(pollingStopped)return
  const requested = typeof route.query.platform === 'string' ? route.query.platform : ''
  if (requested && platforms.value.some(item=>item.key===requested && item.delivery_state!=='paused')) selectedPlatforms.value=[requested]
  scheduleJobPoll()
})

watch(routeContextKey,()=>{
  contextEpoch++;articlesRequest++;platformsRequest++
  articles.value=[];selectedArticleId.value=null;jobActionErrors.value={}
  dispatchError.value=''
  const requested=journeyPlatform.value
  selectedPlatforms.value=requested&&platforms.value.some(item=>item.key===requested&&item.delivery_state!=='paused')?[requested]:[]
  if(mounted&&!pollingStopped){void fetchArticles();void fetchPlatforms()}
},{flush:'sync'})
watch(jobs,items=>{
  const next=new Map(items.map(item=>[item.job_id,jobActionKey(item)])),epochs=new Map(jobEpochs.value)
  for(const id of new Set([...observedJobKeys.keys(),...next.keys()])){
    if(observedJobKeys.get(id)!==next.get(id))epochs.set(id,(epochs.get(id)||0)+1)
  }
  observedJobKeys=next;jobEpochs.value=epochs
},{deep:true,flush:'sync'})
watch(dispatchKey,()=>{dispatchEpoch.value++},{flush:'sync'})
watch(quotaScopeKey,()=>{
  accountsRequest++;accountsLoadedKey.value='';accountsStatus.value='idle'
  if(mounted&&!pollingStopped)void fetchAccounts()
},{flush:'sync'})
watch(jobsScopeKey,()=>{
  jobsRequest++;jobs.value=[];jobsLoadError.value=false
  if(mounted&&!pollingStopped)void fetchJobs()
},{flush:'sync'})

watch(selectedPlatforms, () => {
  riskAcknowledged.value = false
  if (!autoExecuteAvailable.value) autoExecuteAtDue.value = false
})

watch(selectedArticleId, async value => {
  imageRequest++;imageRecommending.value=false;coverGenerating.value=false
  selectedImage.value = null
  selectedVideo.value = null
  if (value) await recommendImage({ silent: true })
},{flush:'sync'})

onBeforeUnmount(() => {
  pollingStopped = true
  mounted=false;contextEpoch++;accountsRequest++;jobsRequest++;articlesRequest++;platformsRequest++
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
})
</script>

<style lang="scss" scoped>
.journey-return{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 20px;margin-bottom:24px;border:1px solid var(--geo-border,#dce6e5);border-radius:14px;background:#fff}.journey-return strong{font-size:15px}.journey-return p{margin:5px 0 0;color:var(--geo-muted,#52676d);font-size:13px;line-height:1.6}@media(max-width:600px){.journey-return{align-items:flex-start;flex-direction:column}}
.quota-retry-hint{display:block;margin-top:6px;line-height:1.6}
.job-message-details{margin-top:8px;font-size:12px;line-height:1.7;color:var(--muted)}.job-message-details summary{cursor:pointer;width:fit-content}.job-message-details summary:focus-visible{outline:2px solid var(--signal);outline-offset:3px}.job-message-details p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}.job-action-error,.jobs-stale{color:var(--danger);font-size:12px;line-height:1.7;overflow-wrap:anywhere}.job-action-error{margin:8px 0 0}.jobs-stale{padding:12px 26px;margin:0;border-bottom:1px solid var(--rule)}
.distribution-center {
  --harbor: #102b31;
  --harbor-deep: #0a2227;
  --signal: #39b8b2;
  --signal-soft: #d9f1ee;
  --paper: #f4f7f5;
  --ink: #173038;
  --muted: #6d7d82;
  --rule: #d7e0dd;
  --amber: #c9852d;
  --danger: #c15454;
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

.page-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  margin-bottom: 22px;
  padding: 8px 4px 18px;
  border-bottom: 1px solid #cad6d2;
}

.heading-copy {
  max-width: 720px;

  .eyebrow {
    margin: 0 0 9px;
    color: #318f8b;
    font: 700 11px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.14em;
  }

  h1 {
    margin: 0;
    color: var(--harbor);
    font: 700 clamp(34px, 4vw, 50px)/0.98 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif;
    letter-spacing: -0.035em;
  }

  > p:last-child {
    margin: 13px 0 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.7;
  }
}

.live-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 218px;
  padding: 12px 14px;
  border: 1px solid var(--rule);
  background: rgba(255, 255, 255, 0.7);

  .pulse {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #8fa19f;
    box-shadow: 0 0 0 5px rgba(143, 161, 159, 0.12);
  }

  &.active .pulse {
    background: var(--signal);
    box-shadow: 0 0 0 5px rgba(57, 184, 178, 0.15);
    animation: status-pulse 1.8s ease-in-out infinite;
  }

  div {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  strong { font-size: 13px; }
  small { color: var(--muted); font-size: 11px; }
}

.dispatch-console {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.75fr);
  background: #fff;
  border: 1px solid var(--rule);
  box-shadow: 0 16px 45px rgba(20, 50, 57, 0.07);
}

.dispatch-form { padding: clamp(22px, 3vw, 38px); }

.section-heading {
  display: flex;
  gap: 18px;
  margin-bottom: 28px;

  > span {
    padding-top: 6px;
    color: #3c9692;
    font: 700 10px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
    white-space: nowrap;
  }

  h2 {
    margin: 0;
    color: var(--harbor);
    font: 700 25px/1.1 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif;
  }

  p {
    margin: 7px 0 0;
    color: var(--muted);
    font-size: 13px;
  }

  &.compact { margin-bottom: 0; }
}

.field-block {
  margin-bottom: 18px;

  > label,
  legend {
    display: block;
    margin-bottom: 9px;
    color: #345159;
    font-size: 13px;
    font-weight: 700;
  }

  :deep(.el-select) { width: 100%; }
  :deep(.el-select__wrapper) { min-height: 44px; border-radius: 3px; }
}

.article-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;

  small { color: #819095; }
}

.field-error,
.field-guidance {
  margin: 8px 0 0;
  color: var(--danger);
  font-size: 12px;

  button {
    padding: 0;
    border: 0;
    background: none;
    color: #267f7b;
    cursor: pointer;
    text-decoration: underline;
  }
}

.field-guidance { color: var(--muted); }

.article-proof {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 16px;
  align-items: center;
  margin-bottom: 26px;
  padding: 15px 16px;
  border-left: 3px solid var(--signal);
  background: var(--paper);
}

.proof-id {
  color: #43827f;
  font: 700 11px/1.2 "Cascadia Mono", monospace;
}

.proof-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  > span { color: var(--muted); font-size: 11px; }
  > strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
}

.proof-tags {
  display: flex;
  gap: 8px;
  overflow: hidden;

  i { color: #4b7777; font-size: 11px; font-style: normal; white-space: nowrap; }
}

.proof-score {
  display: grid;
  place-items: center;
  min-width: 54px;
  padding: 8px;
  border: 1px solid #bdcbc8;

  b { font: 700 20px/1 "Cascadia Mono", monospace; }
  small { margin-top: 3px; color: var(--muted); font-size: 8px; letter-spacing: 0.12em; }
  &.strong { color: #257b69; }
  &.medium { color: var(--amber); }
  &.weak { color: var(--danger); }
}

.platform-fieldset {
  margin: 0;
  padding: 0;
  border: 0;

  legend { padding: 0; color: #345159; font-size: 13px; font-weight: 700; }
  > p { margin: -3px 0 12px; color: var(--muted); font-size: 12px; }
}

.platform-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.platform-option {
  position: relative;
  min-height: 122px;
  padding: 13px 11px;
  border: 1px solid var(--rule);
  border-radius: 3px;
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  text-align: left;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;

  &:hover { transform: translateY(-2px); border-color: #81bebb; }
  &:focus-visible { outline: 3px solid rgba(57, 184, 178, 0.3); outline-offset: 2px; }
  &.selected { border-color: var(--signal); background: #eff9f7; box-shadow: inset 0 -3px var(--signal); }
  &:disabled { transform: none; border-style: dashed; border-color: #d7c3c3; background: #faf5f5; cursor: not-allowed; opacity: .82; }

  .platform-code {
    display: block;
    color: #48827f;
    font: 700 9px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  .platform-name { display: block; margin-top: 11px; font-size: 15px; font-weight: 700; }
  small { display: block; margin-top: 5px; color: var(--muted); font-size: 10px; }
  em { display: block; margin-top: 9px; color: #45696d; font-size: 9px; font-style: normal; line-height: 1.45; }
  &.paused em, &.paused small { color: #9a5757; }
  .selection-mark { position: absolute; top: 10px; right: 10px; color: #338e89; font-size: 14px; }
}

.policy-checklist {
  margin-top: 12px;
  border: 1px solid var(--rule);
  background: #f7faf9;

  > article {
    display: grid;
    grid-template-columns: minmax(180px, 1fr) minmax(170px, .8fr) minmax(140px, .55fr);
    gap: 14px;
    align-items: center;
    padding: 12px 14px;
    border-bottom: 1px solid #dfe7e4;
  }

  > p { margin: 0; padding: 10px 14px; color: #7b7060; background: #fbf6ee; font-size: 10px; line-height: 1.55; }
  :deep(.el-select) { width: 100%; }
}

.policy-platform {
  display: flex;
  align-items: center;
  gap: 10px;

  > span { display: grid; place-items: center; width: 34px; height: 34px; color: #fff; background: var(--harbor); font: 700 9px "Cascadia Mono", monospace; }
  > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  strong { font-size: 12px; }
  small { overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
}

.quota-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-left: 11px;
  border-left: 3px solid #8fa19f;

  strong { font-size: 11px; }
  small { color: var(--muted); font-size: 9px; line-height: 1.4; }
  &.safe { border-left-color: var(--signal); }
  &.blocked { border-left-color: var(--danger); color: var(--danger); }
}

.asset-panel {
  margin-top: 18px;
  border: 1px solid var(--rule);
  background: #fbfdfc;

  &.required { border-color: #8bbab7; }
  &.missing { border-left: 3px solid var(--amber); }
}

.asset-heading {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 13px;
  align-items: start;
  padding: 13px 15px;
  border-bottom: 1px solid var(--rule);

  .asset-code {
    padding-top: 3px;
    color: #3b8e8a;
    font: 700 9px/1.2 "Cascadia Mono", monospace;
    letter-spacing: 0.1em;
    white-space: nowrap;
  }

  > div { display: flex; flex-direction: column; gap: 4px; }
  strong { color: #29474f; font-size: 13px; }
  small { color: var(--muted); font-size: 10px; line-height: 1.5; }
  i {
    padding: 4px 6px;
    background: var(--signal-soft);
    color: #277b77;
    font: 700 9px/1 "Cascadia Mono", monospace;
    font-style: normal;
    white-space: nowrap;
  }
}

.asset-heading .asset-heading-controls {
  align-items: flex-end;
  gap: 6px;

  :deep(.el-switch__label) { color: #45666c; font-size: 10px; }
}

.asset-empty,
.asset-record {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 14px;
  align-items: center;
  min-height: 76px;
  padding: 13px 15px;
}

.asset-auto { background: #f3faf8; }

.asset-button-pair {
  display: flex !important;
  flex-direction: row !important;
  gap: 7px !important;
}

.video-panel { border-color: #9aaec2; }

.video-glyph {
  display: grid;
  width: 70px;
  height: 54px;
  place-items: center;
  border: 1px solid #aabac7;
  background: #142c36;
  color: #7ce0d8;
  font-size: 18px;
}

.asset-empty {
  > .el-icon {
    width: 40px;
    height: 40px;
    border: 1px dashed #8bbab7;
    color: #358d88;
    font-size: 18px;
  }

  > div { display: flex; flex-direction: column; gap: 4px; }
  strong { font-size: 12px; }
  span { color: var(--muted); font-size: 10px; line-height: 1.5; }
  :deep(.el-button) { border-radius: 3px; color: #267f7b; border-color: #7fb7b3; }
}

.asset-record {
  img {
    width: 70px;
    height: 54px;
    object-fit: cover;
    border: 1px solid #bfd0cd;
    background: var(--paper);
  }

  :deep(.el-button) { color: var(--danger); }
}

.asset-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;

  > span { color: #2e8984; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.07em; }
  > strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  > small { overflow: hidden; color: var(--muted); font: 9px/1.3 "Cascadia Mono", monospace; text-overflow: ellipsis; white-space: nowrap; }
}

.asset-routes {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 2px;

  b { padding: 3px 5px; background: #edf6f4; color: #437674; font-size: 9px; font-weight: 600; }
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.schedule-panel {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 12px;
  margin-top: 24px;
  padding: 14px 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);

  > div:first-child { display: flex; flex-direction: column; gap: 3px; }
  strong { font-size: 13px; }
  span { color: var(--muted); font-size: 11px; }
  :deep(.el-date-editor) { grid-column: 1 / -1; width: 100%; }
}

.schedule-auto-execute {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  align-items: start;
  padding: 11px 12px;
  border-left: 3px solid var(--amber);
  background: #fbf6ee;

  :deep(.el-checkbox) { grid-row: 1 / 3; margin-right: 0; }
  :deep(.el-checkbox__label) { color: #4c4b3f; font-size: 12px; font-weight: 700; }
  p { margin: 0; color: #7d7768; font-size: 10px; line-height: 1.55; }
}

.risk-confirmation {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid #e2c79f;
  background: #fff9ef;

  :deep(.el-checkbox__label) { color: #4f4b42; font-size: 12px; font-weight: 700; white-space: normal; }
  p { margin: 5px 0 0 24px; color: #7a6d59; font-size: 10px; line-height: 1.55; }
}

.dispatch-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 20px;

  p { max-width: 440px; margin: 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
  :deep(.el-button) { min-width: 210px; border-radius: 3px; background: var(--harbor); border-color: var(--harbor); }
  :deep(.el-button:hover) { background: #17434a; border-color: #17434a; }
}

.route-manifest {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: clamp(22px, 3vw, 34px);
  background: var(--harbor);
  color: #eef8f6;
}

.manifest-heading {
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);

  > span { color: #78d7d0; font: 700 10px/1 "Cascadia Mono", monospace; letter-spacing: 0.12em; }
  h2 { margin: 10px 0 0; font: 700 25px/1.05 "Bahnschrift SemiCondensed", "Microsoft YaHei", sans-serif; }
  p { margin: 7px 0 0; color: rgba(222, 240, 237, 0.65); font-size: 12px; }
}

.route-line {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 24px 0;

  &::before {
    content: '';
    position: absolute;
    top: 16px;
    bottom: 16px;
    left: 15px;
    width: 1px;
    background: linear-gradient(var(--signal), rgba(57, 184, 178, 0.15));
  }
}

.route-node {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 31px 1fr;
  gap: 13px;
  align-items: center;
  padding: 9px 0;
}

.node-index {
  width: 31px;
  height: 31px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(100, 214, 207, 0.75);
  border-radius: 50%;
  background: var(--harbor);
  color: #8ce3dc;
  font: 700 9px/1 "Cascadia Mono", monospace;
}

.node-copy {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;

  strong { font-size: 13px; }
  span { color: rgba(222, 240, 237, 0.58); font-size: 10px; }
}

.route-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  text-align: center;

  strong { margin-top: 17px; font-size: 14px; }
  p { max-width: 220px; margin: 7px 0 0; color: rgba(222, 240, 237, 0.58); font-size: 11px; line-height: 1.6; }
}

.empty-orbit {
  width: 68px;
  height: 68px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(100, 214, 207, 0.38);
  border-radius: 50%;
  transform: rotate(-20deg) scaleY(0.62);

  span { width: 11px; height: 11px; border-radius: 50%; background: var(--signal); transform: scaleY(1.6); }
}

.manifest-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin-top: auto;
  border-top: 1px solid rgba(255, 255, 255, 0.13);
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);

  div { padding: 13px 6px; border-right: 1px solid rgba(255, 255, 255, 0.13); }
  div:last-child { border-right: 0; }
  span { display: block; color: rgba(222, 240, 237, 0.48); font: 700 8px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
  strong { display: block; margin-top: 7px; color: #e8f7f4; font: 700 12px/1 "Cascadia Mono", monospace; }
}

.safety-note {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  color: rgba(222, 240, 237, 0.62);

  .el-icon { flex: 0 0 auto; margin-top: 2px; color: #76d6cf; }
  p { margin: 0; font-size: 10px; line-height: 1.6; }
  strong { color: #dff5f2; }
}

.job-board {
  margin-top: 22px;
  background: #fff;
  border: 1px solid var(--rule);
}

.board-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 26px;
  border-bottom: 1px solid var(--rule);
}

.job-filters {
  display: flex;
  gap: 8px;

  :deep(.el-select) { width: 146px; }
  :deep(.el-select__wrapper),
  :deep(.el-button) { border-radius: 3px; }
}

.jobs-loading { padding: 0 26px; }
.job-skeleton { padding: 22px 0; border-bottom: 1px solid #e5ebe9; }

.job-list { padding: 0 26px; }

.job-row {
  display: grid;
  grid-template-columns: 88px 142px minmax(160px, 1.15fr) minmax(190px, 1.4fr) 118px minmax(110px, auto);
  gap: 16px;
  align-items: center;
  min-height: 98px;
  border-bottom: 1px solid #e5ebe9;
}

.status-signal {
  display: flex;
  flex-direction: column;
  gap: 7px;
  align-items: flex-start;

  > span { width: 28px; height: 3px; background: #92a19f; }
  small { color: var(--muted); font-size: 10px; }
  &.success > span { background: var(--signal); }
  &.processing > span, &.queued > span { background: #4a9ed0; }
  &.need_action > span, &.scheduled > span { background: var(--amber); }
  &.failed > span { background: var(--danger); }
}

.job-identity,
.job-article,
.job-time {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.job-identity {
  > span { color: #47827f; font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.08em; }
  > strong { font-size: 14px; }
}

.demo-mark {
  align-self: flex-start;
  padding: 3px 5px;
  background: var(--signal-soft);
  color: #267d78 !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.asset-mark {
  align-self: flex-start;
  color: #3c827e !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.account-mark {
  align-self: flex-start;
  color: #506d73 !important;
  font-size: 9px !important;
}

.schedule-mark {
  align-self: flex-start;
  color: #a36724 !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

.public-mark,
.private-mark {
  align-self: flex-start;
  font: 700 8px/1.3 "Cascadia Mono", monospace !important;
}

.public-mark { color: #1e7c67 !important; }
.private-mark { color: #a06628 !important; }

.job-article {
  span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; }
  small { color: var(--muted); font-size: 10px; }
}

.progress-copy {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;

  span { overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  b { color: #547077; font: 700 10px/1 "Cascadia Mono", monospace; }
}

.progress-track {
  height: 4px;
  overflow: hidden;
  background: #e6ecea;

  > span { display: block; height: 100%; background: #5f9fbd; transition: width 260ms ease; }
  &.success > span { background: var(--signal); }
  &.failed > span { background: var(--danger); }
  &.need_action > span, &.scheduled > span { background: var(--amber); }
}

.job-time {
  span { color: var(--muted); font-size: 9px; }
  time { font: 600 10px/1.35 "Cascadia Mono", monospace; }
}

.job-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;

  :deep(.el-button) { border-radius: 3px; }
  .no-action { color: #9aa8a6; font-size: 10px; }
}

.result-check-action { display: grid; gap: 6px; justify-items: end; }
.result-check-action small { max-width: 150px; color: #677d77; font-size: 12px; line-height: 1.6; }

.board-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  min-height: 220px;
  padding: 30px;

  .state-symbol {
    width: 50px;
    height: 50px;
    display: grid;
    place-items: center;
    border: 1px solid #9db9b5;
    color: #348d88;
    font: 700 22px/1 "Cascadia Mono", monospace;
  }

  h3 { margin: 0; font-size: 16px; }
  p { margin: 7px 0 0; color: var(--muted); font-size: 12px; }
  &.is-error .state-symbol { border-color: #d49a9a; color: var(--danger); }
}

.board-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 26px;

  > span { color: var(--muted); font: 700 9px/1 "Cascadia Mono", monospace; letter-spacing: 0.1em; }
}

@keyframes status-pulse {
  50% { box-shadow: 0 0 0 8px rgba(57, 184, 178, 0.05); }
}

@media (prefers-reduced-motion: reduce) {
  .live-indicator.active .pulse { animation: none; }
  .platform-option, .progress-track > span { transition: none; }
}

@media (max-width: 1180px) {
  .platform-grid { grid-template-columns: repeat(3, 1fr); }
  .job-row { grid-template-columns: 78px 126px minmax(160px, 1fr) minmax(180px, 1fr); padding: 15px 0; }
  .job-time { grid-column: 3; }
  .job-actions { grid-column: 4; grid-row: 2; }
}

@media (max-width: 900px) {
  .dispatch-console { grid-template-columns: 1fr; }
  .route-manifest { min-height: 420px; }
  .page-heading, .board-toolbar { align-items: flex-start; flex-direction: column; }
  .job-filters { width: 100%; flex-wrap: wrap; }
  .job-filters :deep(.el-select) { flex: 1; min-width: 150px; }
  .policy-checklist > article { grid-template-columns: 1fr 1fr; }
  .quota-copy { grid-column: 1 / -1; }
}

@media (max-width: 680px) {
  .page-heading { gap: 18px; }
  .live-indicator { width: 100%; box-sizing: border-box; }
  .dispatch-form, .route-manifest { padding: 20px; }
  .platform-grid { grid-template-columns: repeat(2, 1fr); }
  .policy-checklist > article { grid-template-columns: 1fr; }
  .quota-copy { grid-column: auto; }
  .asset-heading { grid-template-columns: 1fr auto; }
  .asset-heading .asset-code { grid-column: 1 / -1; }
  .asset-empty, .asset-record { grid-template-columns: auto 1fr; }
  .asset-empty :deep(.el-button), .asset-record :deep(.el-button) { grid-column: 1 / -1; width: 100%; }
  .dispatch-actions { align-items: stretch; flex-direction: column; }
  .dispatch-actions :deep(.el-button) { width: 100%; }
  .manifest-summary { grid-template-columns: 1fr; }
  .manifest-summary div { border-right: 0; border-bottom: 1px solid rgba(255, 255, 255, 0.13); }
  .manifest-summary div:last-child { border-bottom: 0; }
  .board-toolbar, .job-list { padding-left: 16px; padding-right: 16px; }
  .job-row { grid-template-columns: 76px 1fr; gap: 12px; padding: 18px 0; }
  .job-article, .job-progress, .job-time, .job-actions { grid-column: 1 / -1; }
  .job-actions { justify-content: flex-start; grid-row: auto; }
  .board-state { align-items: flex-start; flex-direction: column; }
}
</style>
