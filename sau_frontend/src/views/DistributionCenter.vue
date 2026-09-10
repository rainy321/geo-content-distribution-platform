<template>
  <main class="distribution-center">
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

    <section
      v-if="accountBindingRequired"
      :class="['publish-mode-banner', publishingRuntimeReady ? 'armed' : 'blocked']"
      :role="publishingRuntimeReady ? 'status' : 'alert'"
    >
      <span>{{ publishingRuntimeReady ? 'LIVE / ARMED' : 'LIVE / BLOCKED' }}</span>
      <div>
        <strong>{{ publishingRuntimeReady ? '真实发布已开启' : '真实发布已授权，但运行时未就绪' }}</strong>
        <p v-if="publishingRuntimeReady">每个平台必须手动选择一个具体账号；执行前还会核对任务版本、正文、素材和账号，并要求单任务或明确批次强确认。</p>
        <p v-else>{{ publishingBlockedReason }}</p>
      </div>
    </section>
    <section v-else-if="demoAvailable" class="publish-mode-banner demo" role="status">
      <span>DEMO / SAFE</span>
      <div>
        <strong>当前为演示分发</strong>
        <p>任务会经过完整状态链，但不会访问媒体平台，也不需要绑定真实账号。</p>
      </div>
    </section>
    <section v-else-if="platformsLoadError" class="publish-mode-banner blocked" role="alert">
      <span>ROUTE / UNKNOWN</span>
      <div>
        <strong>发布能力清单未载入</strong>
        <p>所有渠道已安全禁用。请确认本机服务正常后重新进入发布中心。</p>
      </div>
    </section>
    <section v-else-if="capabilitiesLoaded" class="publish-mode-banner locked" role="status">
      <span>LIVE / LOCKED</span>
      <div>
        <strong>当前构建未开放发布任务</strong>
        <p>{{ publishingBlockedReason }}</p>
      </div>
    </section>

    <section class="dispatch-console" aria-labelledby="dispatch-title">
      <div class="dispatch-form">
        <div class="section-heading">
          <span>CONTROL / 01</span>
          <div>
            <h2 id="dispatch-title">建立分发任务</h2>
            <p>一次选择多个平台，系统会为每个平台创建独立任务。</p>
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
            :disabled="articlesLoading || articlesLoadError"
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
              v-for="platform in platforms"
              :key="platform.key"
              type="button"
              :class="['platform-option', {
                selected: selectedPlatforms.includes(platform.key),
                unavailable: !isPlatformSelectable(platform)
              }]"
              :disabled="!isPlatformSelectable(platform)"
              :aria-disabled="!isPlatformSelectable(platform)"
              :aria-pressed="selectedPlatforms.includes(platform.key)"
              @click="togglePlatform(platform.key)"
            >
              <span class="platform-code">{{ platform.code }}</span>
              <span class="platform-name">{{ platform.name }}</span>
              <small>{{ isPlatformSelectable(platform) ? platform.note : (platform.unavailable_reason || platform.note) }}</small>
              <em v-if="platform.frequency_guidance">
                {{ frequencyGuidanceLabel(platform.frequency_guidance) }}
              </em>
              <i v-if="platform.verification?.likely">可能需要本人验证</i>
              <span class="selection-mark" aria-hidden="true">
                {{ selectedPlatforms.includes(platform.key) ? '✓' : '+' }}
              </span>
            </button>
          </div>
        </fieldset>

        <section v-if="boundedManifestPlatforms.length" class="content-variant-panel" aria-labelledby="variant-title">
          <div class="asset-heading">
            <span class="asset-code">COPY / FIT</span>
            <div>
              <strong id="variant-title">平台短版预检</strong>
              <small>原稿始终保留在内容库；这里只编辑各平台任务实际发送的副本，不会回写原文。</small>
            </div>
            <i>{{ preflightLoading ? '计算中' : '创建前校验' }}</i>
          </div>
          <div v-if="preflightLoading" class="variant-loading">正在按平台上限、话题和安全余量生成短版…</div>
          <div v-else-if="preflightError" class="variant-error" role="alert">
            <span>{{ preflightError }}</span>
            <el-button plain @click="refreshPublishPreflight">重新预检</el-button>
          </div>
          <div v-else class="variant-list">
            <article
              v-for="platform in boundedManifestPlatforms"
              :key="platform.key"
              :class="['variant-card', { invalid: !platformDraftReadyForDispatch(platformDrafts[platform.key]) }]"
            >
              <header>
                <div>
                  <span>{{ platform.code }} / {{ platform.name }}</span>
                  <strong>{{ platformDrafts[platform.key]?.content_policy?.label || '发布正文' }}</strong>
                </div>
                <b>
                  原稿 {{ platformDrafts[platform.key]?.original_length || 0 }}
                  → 实发 {{ platformDrafts[platform.key]?.publish_length || 0 }}
                  / 平台上限 {{ platformDrafts[platform.key]?.content_policy?.hard_limit || '—' }}
                </b>
              </header>
              <label class="variant-field-label">
                <span>
                  发布标题 · {{ platformDrafts[platform.key]?.title_length || 0 }}
                  <template v-if="platformDrafts[platform.key]?.title_policy?.hard_limit">
                    / 上限 {{ platformDrafts[platform.key]?.title_policy?.hard_limit }}
                  </template>
                </span>
                <el-input
                  :model-value="platformDrafts[platform.key]?.editable_title || ''"
                  @input="value => updatePlatformTitle(platform.key, value)"
                />
              </label>
              <el-input
                :model-value="platformDrafts[platform.key]?.editable_content || ''"
                type="textarea"
                :rows="7"
                resize="vertical"
                @input="value => updatePlatformDraft(platform.key, value)"
              />
              <div class="variant-meta">
                <span>
                  软件安全线 {{ platformDrafts[platform.key]?.content_policy?.safe_limit || '—' }} 字；
                  已为话题/换行/计数差异预留 {{ platformDrafts[platform.key]?.content_policy?.reserved_characters || 0 }} 字
                </span>
                <strong v-if="platformDraftReadyForDispatch(platformDrafts[platform.key])">可创建任务</strong>
                <strong v-else-if="!platformDrafts[platform.key]?.within_safe_limit || !platformDrafts[platform.key]?.title_within_safe_limit" class="over-limit">请继续缩短后再创建</strong>
                <strong v-else class="over-limit">先修复 AI 可引用性问题</strong>
              </div>
              <div
                :class="['citeability-receipt', {
                  stale: !platformDrafts[platform.key]?.citeability_current,
                  failed: platformDrafts[platform.key]?.citeability_current && !platformDrafts[platform.key]?.citeability_passed
                }]"
              >
                <div>
                  <span>AI 可引用性</span>
                  <strong v-if="!platformDrafts[platform.key]?.citeability_current">内容编辑后正在重新检查…</strong>
                  <strong v-else>
                    {{ platformDrafts[platform.key]?.citeability_score ?? '—' }}
                    / {{ platformDrafts[platform.key]?.citeability_pass_score ?? 75 }}
                    · {{ platformDrafts[platform.key]?.citeability_passed ? '通过' : '未通过' }}
                  </strong>
                </div>
                <p v-if="!citeabilityRequired">演示任务不会真实发送；评分仅作内容优化参考。</p>
                <ul v-else-if="platformDrafts[platform.key]?.citeability_current && !platformDrafts[platform.key]?.citeability_passed">
                  <li
                    v-for="issue in citeabilityMessages(platformDrafts[platform.key]).slice(0, 3)"
                    :key="issue"
                  >{{ issue }}</li>
                </ul>
              </div>
              <p>{{ platformDrafts[platform.key]?.adaptation_reason }}</p>
              <footer>
                <el-button text @click="restoreAutomaticVariant(platform.key)">恢复自动短版</el-button>
                <el-button text @click="loadOriginalIntoVariant(platform.key)">载入原稿编辑</el-button>
              </footer>
            </article>
          </div>
        </section>

        <section v-if="accountBindingRequired && manifestPlatforms.length" class="account-routing" aria-labelledby="account-routing-title">
          <div class="asset-heading">
            <span class="asset-code">ACCOUNT / BIND</span>
            <div>
              <strong id="account-routing-title">发布账号绑定</strong>
              <small>必须逐个平台手动选择。任务创建时即固定账号；后续新增或切换账号不会改变已经确认的目标。</small>
            </div>
          </div>
          <div v-if="accountsLoadError" class="account-route-error" role="alert">
            <span>账号清单未载入，任务创建已暂停。</span>
            <el-button :loading="accountsLoading" @click="fetchAccounts">重新载入账号</el-button>
          </div>
          <div class="account-route-list">
            <div v-for="platform in manifestPlatforms" :key="platform.key" class="account-route-row">
              <div>
                <strong>{{ platform.name }}</strong>
                <small>{{ connectedAccounts(platform.key).length ? '选择本次实际发送账号' : '还没有可用账号' }}</small>
              </div>
              <el-select
                v-if="connectedAccounts(platform.key).length"
                v-model="selectedAccountIds[platform.key]"
                clearable
                :placeholder="`选择${platform.name}账号`"
              >
                <el-option
                  v-for="account in connectedAccounts(platform.key)"
                  :key="account.id"
                  :label="accountDisplayName(account)"
                  :value="account.id"
                />
              </el-select>
              <el-button v-else type="primary" plain @click="requestPlatformLogin(platform)">
                登录 {{ platform.name }}
              </el-button>
            </div>
          </div>
        </section>

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

        <section v-if="manifestPlatforms.length" class="verification-panel" aria-labelledby="verification-title">
          <div class="verification-heading">
            <span>HUMAN / CHECK</span>
            <div>
              <strong id="verification-title">发布前准备本人验证</strong>
              <small>平台验证是动态风控，无法提前保证是否出现；软件不会绕过验证码或在状态不明时自动重试。</small>
            </div>
          </div>
          <div class="verification-list">
            <article v-for="platform in manifestPlatforms" :key="platform.key">
              <strong>{{ platform.name }}</strong>
              <span>{{ platform.verification?.notice || '可能要求账号安全验证，请保持绑定设备可用。' }}</span>
              <small>{{ frequencyGuidanceLabel(platform.frequency_guidance) }}</small>
            </article>
          </div>
          <p>以上频率是 GEO 软件保守建议，不是平台官方配额或发布通过保证；新账号、重复内容和短时批量操作应进一步降频，账号运营方需自行承担限流、处罚或封禁风险。</p>
        </section>

        <div class="schedule-panel">
          <div>
            <strong>定时发布</strong>
            <span>{{ demoAvailable ? '演示任务可选择到点自动执行。' : '真实任务到点后只进入队列，等待人工单项或批次确认。' }}</span>
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
          <div v-if="scheduleEnabled && demoAvailable" class="schedule-auto-execute">
            <el-checkbox v-model="autoExecuteAtDue">到点自动执行</el-checkbox>
            <p>
              仅适用于 Demo：到点后自动走演示状态链，不会访问任何媒体平台。
            </p>
          </div>
          <div v-else-if="scheduleEnabled" class="schedule-manual-confirm" role="note">
            <strong>到点后等待人工确认</strong>
            <p>系统只会把任务转为排队中，不会自动访问平台。届时需核对正文、素材和账号，并进行单任务或明确批次强确认。</p>
          </div>
        </div>

        <div class="dispatch-actions">
          <p>
            {{ dispatchModeCopy }}
          </p>
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
            <strong>{{ scheduleEnabled ? (demoAvailable && autoExecuteAtDue ? 'DEMO@TIME' : 'SCHEDULED') : 'NOW' }}</strong>
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
          <el-checkbox
            v-if="batchEligibleJobs.length"
            :model-value="allBatchJobsSelected"
            :indeterminate="someBatchJobsSelected"
            :disabled="batchExecuting"
            @change="toggleAllBatchJobs"
          >
            选择本页待发
          </el-checkbox>
          <el-button
            v-if="batchEligibleJobs.length"
            type="danger"
            plain
            :loading="batchExecuting"
            :disabled="batchExecuting"
            @click="executeRealBatch"
          >
            连续发布 {{ batchJobsToRun.length }} 项
          </el-button>
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

      <div v-if="jobsLoading" class="jobs-loading" aria-live="polite">
        <div v-for="index in 4" :key="index" class="job-skeleton"><el-skeleton :rows="2" animated /></div>
      </div>

      <div v-else-if="jobsLoadError" class="board-state is-error" role="alert">
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
            <small v-if="job.demo" class="demo-mark">DEMO / 演示任务</small>
            <small v-if="job.images?.length" class="asset-mark">{{ job.images.length }} IMG / 已附素材</small>
            <small v-if="job.video" class="asset-mark">01 VIDEO / 已附素材</small>
            <small v-if="job.auto_execute" class="schedule-mark">AUTO@TIME / 已授权</small>
            <small v-if="job.account_name" class="account-mark">账号 / {{ job.account_name }}</small>
            <small v-else-if="!job.demo" class="account-mark missing">未绑定账号 / 禁止执行</small>
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
          </div>

          <div class="job-time">
            <span>{{ job.finished_at ? '完成时间' : job.started_at ? '开始时间' : '创建时间' }}</span>
            <time>{{ formatDate(job.finished_at || job.started_at || job.created_at) }}</time>
          </div>

          <div class="job-actions">
            <el-checkbox
              v-if="isBatchEligible(job)"
              :model-value="selectedJobIds.has(job.job_id)"
              :disabled="batchExecuting || busyJobIds.has(job.job_id)"
              @change="selected => toggleBatchJob(job.job_id, selected)"
            >
              批次
            </el-checkbox>
            <el-button
              v-if="job.reconciliation_required"
              plain
              :loading="busyJobIds.has(job.job_id)"
              :disabled="batchExecuting"
              @click="openReconciliation(job)"
            >
              核验平台结果
            </el-button>
            <el-button
              v-if="!job.demo && !job.account_id && ['queued', 'need_action', 'failed'].includes(job.status)"
              type="primary"
              plain
              @click="requestPlatformLogin(platformByKey(job.platform), job)"
            >
              登录并重建任务
            </el-button>
            <el-button
              v-else-if="['failed', 'need_action'].includes(job.status) && !job.reconciliation_required"
              type="primary"
              plain
              :loading="busyJobIds.has(job.job_id)"
              @click="retryJob(job)"
            >
              {{ job.status === 'need_action' ? '完成验证后继续' : '重新发布' }}
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
              :disabled="batchExecuting"
              @click="executeRealJob(job)"
            >
              确认真实发布
            </el-button>
            <el-button v-if="job.url" text :icon="Link" @click="openResult(job.url)">查看结果</el-button>
            <span v-if="!job.url && !['failed', 'need_action', 'processing', 'queued'].includes(job.status)" class="no-action">无需操作</span>
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

    <el-dialog
      v-model="reconciliationDialog.visible"
      title="人工核验平台结果"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!reconciliationDialog.submitting"
      :show-close="!reconciliationDialog.submitting"
      @closed="resetReconciliationDialog"
    >
      <div class="reconciliation-form">
        <div class="reconciliation-warning" role="alert">
          <strong>{{ reconciliationJobLabel }}</strong>
          <p>先在平台创作者后台按标题、发布时间和账号核对。此操作只收口本地状态，不会再次点击发布。</p>
        </div>
        <el-radio-group v-model="reconciliationDialog.decision" :disabled="reconciliationDialog.submitting">
          <el-radio-button value="published">后台确认已发布</el-radio-button>
          <el-radio-button value="not_published">后台确认未发布</el-radio-button>
        </el-radio-group>
        <label for="reconciliation-note">核验说明（必填）</label>
        <el-input
          id="reconciliation-note"
          v-model="reconciliationDialog.note"
          type="textarea"
          :rows="4"
          maxlength="500"
          show-word-limit
          :disabled="reconciliationDialog.submitting"
          placeholder="例如：已检查全部作品、审核中、草稿箱，精确标题存在/不存在"
        />
        <template v-if="reconciliationDialog.decision === 'published'">
          <label for="reconciliation-url">公开链接（可选）</label>
          <el-input
            id="reconciliation-url"
            v-model="reconciliationDialog.resultUrl"
            maxlength="2048"
            :disabled="reconciliationDialog.submitting"
            placeholder="https://…；平台没有公链时可以留空"
          />
        </template>
        <el-checkbox
          v-model="reconciliationDialog.platformChecked"
          :disabled="reconciliationDialog.submitting"
        >
          我已在正确平台、正确账号下人工核验，不是根据软件进度条猜测
        </el-checkbox>
        <template v-if="reconciliationDialog.decision === 'not_published'">
          <el-checkbox
            v-model="reconciliationDialog.executionStoppedChecked"
            :disabled="reconciliationDialog.submitting"
          >
            我已关闭相关发布/登录窗口，确认旧执行进程已结束，并等待至少 5 分钟后检查了作品、审核中和草稿箱
          </el-checkbox>
          <p class="reconciliation-next-step">
            结案后任务会变为“发布失败”，届时才可重新排队并再次进行真实发布强确认。
          </p>
        </template>
      </div>
      <template #footer>
        <el-button :disabled="reconciliationDialog.submitting" @click="reconciliationDialog.visible = false">取消</el-button>
        <el-button
          :type="reconciliationDialog.decision === 'published' ? 'success' : 'danger'"
          :loading="reconciliationDialog.submitting"
          :disabled="!canSubmitReconciliation"
          @click="submitReconciliation"
        >
          {{ reconciliationDialog.decision === 'published' ? '确认记为发布成功' : '确认结案为未发布' }}
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Lock, MagicStick, Picture, Promotion, Refresh, Upload, VideoCamera } from '@element-plus/icons-vue'
import { articleApi } from '@/api/article'
import { accountApi } from '@/api/account'
import { materialApi } from '@/api/material'
import { publishApi } from '@/api/publish'
import {
  countPublishCharacters,
  frequencyGuidanceLabel,
  refreshEditableVariant
} from '@/utils/publishPreflight'

const route = useRoute()
const router = useRouter()

const platformCodes = {
  zhihu: 'ZH', toutiao: 'TT', baijiahao: 'BJ', sohu: 'SH', xiaohongshu: 'XH',
  douyin: 'DY', kuaishou: 'KS', bilibili: 'BI', channels: 'WX', tiktok: 'TK'
}
const modeNames = { article: '文章', image_article: '图文', image_note: '图片笔记', video: '视频' }
const platformMaturityNotes = {
  zhihu: '可用 · 文章发布',
  toutiao: '可用 · 文章发布',
  baijiahao: '可用 · 图文发布',
  sohu: '可用 · 文章发布',
  xiaohongshu: '可用 · 图片笔记',
  tiktok: '可用 · 视频发布',
  channels: '提交后需后台核对',
  douyin: '实验性 · 可能转待人工',
  kuaishou: '实验性 · 可能转待人工',
  bilibili: '兼容运行时 · 视频发布'
}
const platforms = ref([])
const capabilitiesLoaded = ref(false)
const platformsLoadError = ref(false)
const accountOverview = ref({ platforms: [], accounts: [] })
const selectedAccountIds = reactive({})
const accountsLoading = ref(false)
const accountsLoadError = ref(false)

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
const platformDrafts = reactive({})
const automaticPlatformVariants = reactive({})
const preflightLoading = ref(false)
const preflightError = ref('')
let preflightRequestSerial = 0
let preflightDebounceTimer = null
const scheduleEnabled = ref(false)
const publishAt = ref('')
const autoExecuteAtDue = ref(false)
const dispatching = ref(false)
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
const selectedJobIds = ref(new Set())
const batchExecuting = ref(false)
const reconciliationDialog = reactive({
  visible: false,
  job: null,
  decision: 'published',
  note: '',
  resultUrl: '',
  platformChecked: false,
  executionStoppedChecked: false,
  submitting: false
})
const jobFilters = reactive({ platform: '', status: '' })
const jobPagination = reactive({ page: 1, page_size: 20, total: 0, total_pages: 0 })
let pollTimer = null
let pollingStopped = false

const selectedArticle = computed(() => (
  articles.value.find(article => article.id === selectedArticleId.value) || null
))

const manifestPlatforms = computed(() => (
  platforms.value.filter(platform => selectedPlatforms.value.includes(platform.key))
))

const boundedManifestPlatforms = computed(() => manifestPlatforms.value)

const imagePlatforms = computed(() => (
  manifestPlatforms.value.filter(platform => platform.requires_images)
))

const imageRequired = computed(() => imagePlatforms.value.length > 0)
const videoPlatforms = computed(() => (
  manifestPlatforms.value.filter(platform => platform.requires_video)
))
const videoRequired = computed(() => videoPlatforms.value.length > 0)
const realPublishingAuthorized = computed(() => (
  platforms.value.some(item => item.real_publishing_authorized === true)
))
const publishingRuntimeReady = computed(() => (
  realPublishingAuthorized.value
  && platforms.value.some(item => item.runtime_available === true)
))
const demoAvailable = computed(() => (
  platforms.value.some(item => item.demo_available === true)
))
const citeabilityRequired = computed(() => (
  realPublishingAuthorized.value && !demoAvailable.value
))
const platformDraftReadyForDispatch = draft => Boolean(
  draft?.within_safe_limit === true
  && draft?.title_within_safe_limit === true
  && (
    !citeabilityRequired.value
    || (draft?.citeability_current === true && draft?.citeability_passed === true)
  )
)
const platformCopiesReady = computed(() => (
  !preflightLoading.value
  && !preflightError.value
  && boundedManifestPlatforms.value.every(platform => (
    platformDraftReadyForDispatch(platformDrafts[platform.key])
  ))
))
const citeabilityMessages = draft => {
  const blocking = (draft?.citeability_blocking_issues || []).map(item => (
    typeof item === 'string' ? item : (item?.message || item?.code || '')
  ))
  return [...blocking, ...(draft?.citeability_suggestions || [])]
    .map(item => String(item || '').trim())
    .filter(Boolean)
}
const accountBindingRequired = computed(() => (
  realPublishingAuthorized.value && !demoAvailable.value
))
const publishingBlockedReason = computed(() => (
  platforms.value.find(item => item.task_creation_available !== true)?.unavailable_reason
  || (realPublishingAuthorized.value
    ? '本机发布组件未通过探针，请检查 Google Chrome 与发布组件后重启软件。'
    : '当前构建未授权真实发布，也未启用演示任务。')
))
const dispatchModeCopy = computed(() => {
  if (demoAvailable.value) return 'Demo 任务会走完整状态链，但不会访问真实平台。'
  if (publishingRuntimeReady.value) {
    return '真实发布已开启：任务只会发送到明确绑定的账号，并在执行前进行版本绑定和强确认。'
  }
  if (realPublishingAuthorized.value) return `真实发布当前不可用：${publishingBlockedReason.value}`
  return publishingBlockedReason.value
})
const accountsReady = computed(() => (
  demoAvailable.value
  || (
    accountBindingRequired.value
    && !accountsLoadError.value
    && manifestPlatforms.value.every(platform => Boolean(selectedAccountIds[platform.key]))
  )
))

const assetSummary = computed(() => {
  const parts = []
  if (selectedImage.value) parts.push('01 IMG')
  else if (imageRequired.value && autoImage.value) parts.push('AUTO IMG')
  if (selectedVideo.value) parts.push('01 VIDEO')
  return parts.join(' + ') || 'NONE'
})

const canDispatch = computed(() => Boolean(
  capabilitiesLoaded.value
  && !platformsLoadError.value
  && selectedArticleId.value
  && selectedPlatforms.value.length
  && manifestPlatforms.value.every(platform => platform.task_creation_available === true)
  && (!imageRequired.value || selectedImage.value || autoImage.value)
  && (!videoRequired.value || selectedVideo.value)
  && platformCopiesReady.value
  && accountsReady.value
  && (!scheduleEnabled.value || publishAt.value)
  && !imageUploading.value
  && !videoUploading.value
  && !dispatching.value
))

const dispatchButtonLabel = computed(() => {
  if (platformsLoadError.value) return '发布能力未载入'
  if (capabilitiesLoaded.value && !platforms.value.some(item => item.task_creation_available === true)) {
    return realPublishingAuthorized.value ? '发布运行时未就绪' : '当前构建未开放发布'
  }
  if (!selectedArticleId.value) return '先选择就绪稿件'
  if (!selectedPlatforms.value.length) return '至少选择一个渠道'
  if (preflightLoading.value) return '正在生成平台短版'
  if (preflightError.value) return '平台短版预检失败'
  if (!platformCopiesReady.value) {
    const limitsInvalid = boundedManifestPlatforms.value.some(platform => (
      platformDrafts[platform.key]?.within_safe_limit !== true
      || platformDrafts[platform.key]?.title_within_safe_limit !== true
    ))
    return limitsInvalid ? '先将平台短版缩至安全线内' : '先通过 AI 可引用性检查'
  }
  if (imageUploading.value) return '图片上传中'
  if (videoUploading.value) return '视频上传中'
  if (!accountsReady.value) return '先登录并选择每个平台账号'
  if (imageRequired.value && !selectedImage.value && !autoImage.value) return '先补齐渠道图片'
  if (videoRequired.value && !selectedVideo.value) return '先补齐渠道视频'
  if (scheduleEnabled.value && !publishAt.value) return '选择计划发布时间'
  if (scheduleEnabled.value && demoAvailable.value && autoExecuteAtDue.value) {
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

const batchEligibleJobs = computed(() => jobs.value.filter(isBatchEligible))
const selectedBatchJobs = computed(() => batchEligibleJobs.value.filter(job => (
  selectedJobIds.value.has(job.job_id)
)))
const batchJobsToRun = computed(() => (
  selectedBatchJobs.value.length ? selectedBatchJobs.value : batchEligibleJobs.value
))
const allBatchJobsSelected = computed(() => (
  batchEligibleJobs.value.length > 0
  && selectedBatchJobs.value.length === batchEligibleJobs.value.length
))
const someBatchJobsSelected = computed(() => (
  selectedBatchJobs.value.length > 0 && !allBatchJobsSelected.value
))
const reconciliationJobLabel = computed(() => {
  const job = reconciliationDialog.job
  if (!job) return '发布任务'
  return `JOB-${String(job.job_id).padStart(5, '0')} · ${platformName(job.platform)} · ${job.article_title || `文章 #${job.article_id}`}`
})
const canSubmitReconciliation = computed(() => {
  const note = reconciliationDialog.note.trim()
  const url = reconciliationDialog.resultUrl.trim()
  return Boolean(
    reconciliationDialog.job
    && reconciliationDialog.platformChecked
    && (
      reconciliationDialog.decision !== 'not_published'
      || reconciliationDialog.executionStoppedChecked
    )
    && note.length >= 4
    && note.length <= 500
    && (
      reconciliationDialog.decision === 'not_published'
      || !url
      || /^https?:\/\//i.test(url)
    )
  )
})

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
  articlesLoading.value = true
  articlesLoadError.value = false
  try {
    const response = await articleApi.getArticles({ status: 'ready', page: 1, page_size: 100 })
    articles.value = response.data?.items || []
    const queryId = Number(route.query.article_id)
    if (!selectedArticleId.value && Number.isInteger(queryId) && articles.value.some(item => item.id === queryId)) {
      selectedArticleId.value = queryId
    }
  } catch (error) {
    articlesLoadError.value = true
    console.error('加载就绪稿件失败:', error)
  } finally {
    articlesLoading.value = false
  }
}

const fetchPlatforms = async () => {
  platformsLoadError.value = false
  try {
    const response = await publishApi.getPlatforms()
    platforms.value = (response.data || []).map(item => ({
      ...item,
      code: platformCodes[item.key] || item.key.slice(0, 2).toUpperCase(),
      note: item.maturity || (item.key === 'bilibili' && item.demo_available
        ? '仅演示 · 真实运行时未开放'
        : platformMaturityNotes[item.key])
        || `${item.priority} · ${modeNames[item.content_mode] || item.content_mode}`
    }))
    capabilitiesLoaded.value = true
    selectedPlatforms.value = selectedPlatforms.value.filter(key => (
      platforms.value.some(item => item.key === key && item.task_creation_available === true)
    ))
  } catch (error) {
    platforms.value = []
    selectedPlatforms.value = []
    capabilitiesLoaded.value = false
    platformsLoadError.value = true
    console.error('加载平台能力失败:', error)
  }
}

const fetchAccounts = async () => {
  accountsLoading.value = true
  accountsLoadError.value = false
  try {
    const response = await accountApi.getMediaAccounts()
    accountOverview.value = response.data || { platforms: [], accounts: [] }
    for (const platform of platforms.value) {
      const accounts = connectedAccounts(platform.key)
      const current = Number(selectedAccountIds[platform.key])
      if (!accounts.some(account => account.id === current)) {
        selectedAccountIds[platform.key] = null
      }
    }
    return true
  } catch (error) {
    accountsLoadError.value = true
    console.error('加载媒体账号失败:', error)
    return false
  } finally {
    accountsLoading.value = false
  }
}

const clearPublishPreflight = () => {
  if (preflightDebounceTimer) window.clearTimeout(preflightDebounceTimer)
  preflightDebounceTimer = null
  preflightRequestSerial += 1
  for (const key of Object.keys(platformDrafts)) delete platformDrafts[key]
  for (const key of Object.keys(automaticPlatformVariants)) delete automaticPlatformVariants[key]
  preflightError.value = ''
}

const refreshPublishPreflight = async ({ preserveEdits = true } = {}) => {
  const requestSerial = ++preflightRequestSerial
  const platformKeys = boundedManifestPlatforms.value.map(platform => platform.key)
  if (!selectedArticleId.value || !platformKeys.length) {
    clearPublishPreflight()
    preflightLoading.value = false
    return
  }
  preflightLoading.value = true
  preflightError.value = ''
  try {
    const payload = {
      article_id: selectedArticleId.value,
      platforms: platformKeys
    }
    if (preserveEdits) {
      const overrides = {}
      const titleOverrides = {}
      for (const platformKey of platformKeys) {
        const draft = platformDrafts[platformKey]
        if (draft?.manually_edited) overrides[platformKey] = draft.editable_content
        if (draft?.title_manually_edited) titleOverrides[platformKey] = draft.editable_title
      }
      if (Object.keys(overrides).length) payload.overrides = overrides
      if (Object.keys(titleOverrides).length) payload.title_overrides = titleOverrides
    }
    const response = await publishApi.previewVariants(payload)
    if (requestSerial !== preflightRequestSerial) return
    const variants = response.data?.variants || []
    const activeKeys = new Set(platformKeys)
    for (const key of Object.keys(platformDrafts)) {
      if (!activeKeys.has(key)) delete platformDrafts[key]
    }
    for (const key of Object.keys(automaticPlatformVariants)) {
      if (!activeKeys.has(key)) delete automaticPlatformVariants[key]
    }
    for (const variant of variants) {
      const serverDraft = refreshEditableVariant(variant)
      const existing = platformDrafts[variant.platform]
      if (!preserveEdits || !existing || !automaticPlatformVariants[variant.platform]) {
        automaticPlatformVariants[variant.platform] = serverDraft
      }
      platformDrafts[variant.platform] = serverDraft
    }
  } catch (error) {
    if (requestSerial !== preflightRequestSerial) return
    preflightError.value = error?.response?.data?.msg || error?.message || '平台短版预检失败，请重试'
    console.error('平台短版预检失败:', error)
  } finally {
    if (requestSerial === preflightRequestSerial) preflightLoading.value = false
  }
}

const schedulePublishPreflight = () => {
  preflightRequestSerial += 1
  if (preflightDebounceTimer) window.clearTimeout(preflightDebounceTimer)
  preflightDebounceTimer = window.setTimeout(() => {
    preflightDebounceTimer = null
    refreshPublishPreflight({ preserveEdits: true })
  }, 450)
}

const updatePlatformDraft = (platformKey, value) => {
  const current = platformDrafts[platformKey]
  if (!current) return
  platformDrafts[platformKey] = refreshEditableVariant({
    ...current,
    editable_content: value,
    manually_edited: true,
    adapted: String(value || '') !== String(current.original_content || ''),
    adaptation_reason: '用户编辑的平台副本；原稿未修改'
  })
  schedulePublishPreflight()
}

const updatePlatformTitle = (platformKey, value) => {
  const current = platformDrafts[platformKey]
  if (!current) return
  platformDrafts[platformKey] = refreshEditableVariant({
    ...current,
    editable_title: value,
    title_manually_edited: true,
    title_adapted: String(value || '').trim() !== String(current.original_title || '').trim()
  })
  schedulePublishPreflight()
}

const restoreAutomaticVariant = platformKey => {
  const automatic = automaticPlatformVariants[platformKey]
  if (!automatic) return
  if (preflightDebounceTimer) window.clearTimeout(preflightDebounceTimer)
  preflightDebounceTimer = null
  preflightRequestSerial += 1
  preflightLoading.value = false
  platformDrafts[platformKey] = refreshEditableVariant({ ...automatic })
  ElMessage.success(`${platformName(platformKey)}已恢复自动短版`)
}

const loadOriginalIntoVariant = platformKey => {
  const current = platformDrafts[platformKey]
  if (!current) return
  platformDrafts[platformKey] = refreshEditableVariant({
    ...current,
    editable_content: current.original_content,
    manually_edited: true,
    adapted: false,
    adaptation_reason: '已载入完整原稿；若超过安全线，请在本框内缩短后再创建任务'
  })
  schedulePublishPreflight()
}

const platformByKey = key => platforms.value.find(platform => platform.key === key) || {
  key,
  name: platformName(key)
}

const connectedAccounts = platformKey => (
  (accountOverview.value.accounts || []).filter(account => (
    account.platform_key === platformKey && account.status === 'connected'
  ))
)

const accountDisplayName = account => `${account.account_name} · ID ${account.id}`

const selectedAccountFor = platformKey => {
  const accountId = Number(selectedAccountIds[platformKey])
  return connectedAccounts(platformKey).find(account => account.id === accountId) || null
}

const requestPlatformLogin = async (platform, job = null) => {
  if (!platform?.key) return
  if (platform.login_available !== true) {
    ElMessage.warning(platform.unavailable_reason || '该平台登录运行时当前不可用')
    return
  }
  await router.push({
    path: '/account-management',
    query: {
      login_platform: platform.key,
      return_to: '/publish-center',
      ...(job?.job_id ? { job_id: job.job_id } : {})
    }
  })
}

const fetchJobs = async (pageOrSilent = false) => {
  const silent = pageOrSilent === true
  if (typeof pageOrSilent === 'number') jobPagination.page = pageOrSilent
  if (silent) jobsRefreshing.value = false
  else if (jobs.value.length) jobsRefreshing.value = true
  else jobsLoading.value = true
  jobsLoadError.value = false

  const params = {
    page: jobPagination.page,
    page_size: jobPagination.page_size
  }
  if (jobFilters.platform) params.platform = jobFilters.platform
  if (jobFilters.status) params.status = jobFilters.status

  try {
    const response = await publishApi.getJobs(params)
    jobs.value = response.data?.items || []
    Object.assign(jobPagination, response.data?.pagination || {})
    lastSyncedAt.value = new Date()
  } catch (error) {
    if (!silent) jobsLoadError.value = true
    console.error('加载发布任务失败:', error)
  } finally {
    jobsLoading.value = false
    jobsRefreshing.value = false
  }
}

function isBatchEligible(job) {
  return Boolean(
    job
    && !job.demo
    && job.status === 'queued'
    && job.can_execute_real
    && job.account_id
  )
}

const toggleBatchJob = (jobId, selected) => {
  const next = new Set(selectedJobIds.value)
  if (selected) next.add(jobId)
  else next.delete(jobId)
  selectedJobIds.value = next
}

const toggleAllBatchJobs = selected => {
  const next = new Set(selectedJobIds.value)
  for (const job of batchEligibleJobs.value) {
    if (selected) next.add(job.job_id)
    else next.delete(job.job_id)
  }
  selectedJobIds.value = next
}

const togglePlatform = (key) => {
  const platform = platforms.value.find(item => item.key === key)
  if (platform && !isPlatformSelectable(platform)) {
    ElMessage.warning(platform.unavailable_reason || '该平台运行时当前不可用')
    return
  }
  selectedPlatforms.value = selectedPlatforms.value.includes(key)
    ? selectedPlatforms.value.filter(item => item !== key)
    : [...selectedPlatforms.value, key]
}

const isPlatformSelectable = platform => platform.task_creation_available === true

const handleScheduleToggle = (enabled) => {
  if (!enabled) {
    publishAt.value = ''
    autoExecuteAtDue.value = false
  }
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
  if (!selectedArticleId.value || imageRecommending.value) return
  imageRecommending.value = true
  try {
    const response = await articleApi.recommendImages(selectedArticleId.value)
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
    imageRecommending.value = false
  }
}

const generateCover = async () => {
  if (!selectedArticleId.value || coverGenerating.value) return
  coverGenerating.value = true
  try {
    const response = await articleApi.generateCover(selectedArticleId.value)
    selectedImage.value = materialRecordToImage(response.data)
    ElMessage.success('中性 GEO 封面已生成并进入素材库')
  } catch (error) {
    console.error('生成文章封面失败:', error)
  } finally {
    coverGenerating.value = false
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
  if (!demoAvailable.value) {
    const selectedNames = manifestPlatforms.value.map(platform => platform.name).join('、')
    try {
      await ElMessageBox.confirm(
        h('div', { style: { lineHeight: '1.65' } }, [
          h('p', { style: { margin: '0 0 8px' } }, `将为 ${selectedNames} 创建真实发布任务。创建任务本身不会绕过平台验证。`),
          h('p', { style: { margin: '0 0 8px', color: '#985b16', fontWeight: '700' } },
            '执行时可能弹出短信验证码、扫码、滑块或安全确认，请保持绑定手机和对应 App 可用。'),
          h('p', { style: { margin: 0, fontSize: '11px', color: '#6d6a61' } },
            'GEO 的每日条数和发布间隔仅为软件保守建议，不是平台官方配额；遇到验证、限流或审核异常时应停止批量操作。')
        ]),
        '发布前检查 · 可能需要本人验证',
        {
          confirmButtonText: '已准备好，创建任务',
          cancelButtonText: '稍后再发',
          type: 'warning'
        }
      )
    } catch {
      return
    }
  }
  let confirmedAutoExecute = false
  if (scheduleEnabled.value && demoAvailable.value && autoExecuteAtDue.value) {
    try {
      await ElMessageBox.confirm(
        `这将授权系统在 ${publishAt.value} 到达后自动执行 ${selectedPlatforms.value.length} 个演示任务；不会访问真实平台。是否确认？`,
        '确认定时演示任务',
        {
          confirmButtonText: '确认到点执行演示',
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
  dispatching.value = true
  let createdCount = 0
  let failedCount = 0
  let demoCount = 0
  const creationFailures = []

  for (const platform of selectedPlatforms.value) {
    try {
      const capability = platforms.value.find(item => item.key === platform)
      const response = await publishApi.createJob({
        article_id: selectedArticleId.value,
        platform,
        images: selectedImage.value ? [selectedImage.value.filename] : [],
        video: capability?.requires_video ? (selectedVideo.value?.filename || '') : '',
        auto_image: autoImage.value,
        publish_at: scheduleEnabled.value ? publishAt.value : null,
        auto_execute: demoAvailable.value && confirmedAutoExecute,
        account_id: selectedAccountIds[platform] || null,
        publish_title: platformDrafts[platform]?.editable_title ?? null,
        publish_content: platformDrafts[platform]?.editable_content ?? null
      })
      let job = response.data
      createdCount += 1
      if (job.demo) demoCount += 1
      if (job.demo && job.status === 'queued') {
        const executed = await publishApi.executeDemoJob(job.job_id)
        job = executed.data
      }
    } catch (error) {
      failedCount += 1
      creationFailures.push(`${platformName(platform)}：${batchFailureMessage(error)}`)
      console.error(`创建 ${platform} 发布任务失败:`, error)
    }
  }

  await fetchJobs(true)
  dispatching.value = false
  if (createdCount && !failedCount) {
    ElMessage.success(
      demoCount
        ? `${createdCount} 个演示任务已完成，未访问真实平台`
        : `${createdCount} 个发布任务已创建`
    )
  } else if (createdCount) {
    ElMessage.warning(`已创建 ${createdCount} 个任务，${failedCount} 个渠道创建失败`)
  } else if (failedCount) {
    ElMessage.error(`${failedCount} 个渠道均未创建任务`)
  }
  if (creationFailures.length) {
    await ElMessageBox.alert(
      h('div', { style: { lineHeight: '1.7' } }, creationFailures.map(message => (
        h('p', { style: { margin: '0 0 6px' } }, message)
      ))),
      '部分渠道未创建 · 已继续处理其余渠道',
      { confirmButtonText: '我知道了', type: 'warning' }
    )
  }
}

const executeDemoJob = async (job) => {
  markJobBusy(job.job_id, true)
  try {
    await publishApi.executeDemoJob(job.job_id)
    ElMessage.success('演示任务已完成，未访问真实平台')
    await fetchJobs(true)
  } catch (error) {
    console.error('执行演示任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const batchFailureMessage = error => (
  error?.response?.data?.msg
  || error?.response?.data?.message
  || error?.message
  || '请求未完成，请核对本地任务状态和平台后台'
)

const executeRealBatch = async () => {
  if (batchExecuting.value) return
  const listedJobs = [...batchJobsToRun.value]
  if (!listedJobs.length) {
    ElMessage.warning('当前页面没有可连续发布的排队任务')
    return
  }

  const accountsLoaded = await fetchAccounts()
  if (!accountsLoaded) {
    ElMessage.error('账号清单未载入，批次没有开始')
    return
  }

  const detailResults = await Promise.allSettled(
    listedJobs.map(job => publishApi.getJob(job.job_id))
  )
  const preparedJobs = []
  const preparationIssues = []
  detailResults.forEach((result, index) => {
    const listed = listedJobs[index]
    if (result.status !== 'fulfilled') {
      preparationIssues.push({
        job: listed,
        reason: `确认快照读取失败：${batchFailureMessage(result.reason)}`
      })
      return
    }
    const detail = result.value?.data
    const account = (accountOverview.value.accounts || []).find(item => (
      item.id === detail?.account_id
      && item.platform_key === detail?.platform
      && item.status === 'connected'
    ))
    let reason = ''
    if (!detail || detail.status !== 'queued' || !detail.can_execute_real) {
      reason = '任务已不再是可执行的排队状态'
    } else if (!/^[0-9a-f]{64}$/.test(String(detail.confirmation_fingerprint || ''))) {
      reason = '任务确认指纹缺失'
    } else if (!detail.confirmation_snapshot || !detail.publication_snapshot) {
      reason = '任务与发布正文快照不完整'
    } else if (!account) {
      reason = '绑定账号已离线或不存在'
    }
    if (reason) preparationIssues.push({ job: listed, reason })
    else preparedJobs.push({ ...detail, boundAccount: account })
  })

  if (!preparedJobs.length) {
    await fetchJobs(true)
    ElMessage.error(`批次没有开始：${preparationIssues.map(item => item.reason).join('；')}`)
    return
  }

  const confirmationPhrase = `批量发布 ${preparedJobs.length} 项`
  const taskLines = preparedJobs.map(job => h(
    'li',
    { style: { margin: '3px 0' } },
    `JOB-${String(job.job_id).padStart(5, '0')} · ${platformName(job.platform)} / ${job.boundAccount.account_name} · ${job.publication_snapshot.title}`
  ))
  try {
    await ElMessageBox.prompt(
      h('div', { style: { color: '#423b31', lineHeight: '1.6' } }, [
        h('p', { style: { margin: '0 0 8px', fontWeight: '700' } },
          `将按列表顺序真实执行 ${preparedJobs.length} 个任务。`),
        h('ul', { style: { maxHeight: '180px', overflow: 'auto', margin: '0 0 8px', paddingLeft: '20px', fontSize: '11px' } }, taskLines),
        preparationIssues.length
          ? h('p', { style: { margin: '7px 0', color: '#985b16' } },
              `${preparationIssues.length} 个已变化任务将跳过，不会访问平台。`)
          : null,
        h('p', { style: { margin: '8px 0 0', color: '#985b16', fontWeight: '700' } },
          '每项独立执行：失败、验证码、待核验或状态未知不会阻塞后续，也不会自动重试同一任务。平台可能弹出本人验证窗口。'),
        h('p', { style: { margin: '8px 0 0' } }, `请输入“${confirmationPhrase}”继续。`)
      ]),
      '强确认 · 连续真实发布',
      {
        confirmButtonText: `连续执行 ${preparedJobs.length} 项`,
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true,
        inputPlaceholder: confirmationPhrase,
        inputValidator: value => String(value || '').trim() === confirmationPhrase
          || `请输入“${confirmationPhrase}”`
      }
    )
  } catch {
    return
  }

  batchExecuting.value = true
  const outcomes = preparationIssues.map(item => ({
    job_id: item.job.job_id,
    platform: item.job.platform,
    status: 'skipped',
    message: item.reason
  }))
  try {
    for (const job of preparedJobs) {
      markJobBusy(job.job_id, true)
      try {
        const response = await publishApi.executeRealJob(job)
        const result = response.data || {}
        outcomes.push({
          job_id: job.job_id,
          platform: job.platform,
          status: result.reconciliation_required
            ? 'awaiting_reconcile'
            : (result.status || 'unknown'),
          message: result.message || defaultStatusMessage(result.status)
        })
      } catch (error) {
        outcomes.push({
          job_id: job.job_id,
          platform: job.platform,
          status: 'request_error',
          message: batchFailureMessage(error)
        })
      } finally {
        markJobBusy(job.job_id, false)
        toggleBatchJob(job.job_id, false)
        await fetchJobs(true)
      }
    }
  } finally {
    batchExecuting.value = false
    await fetchJobs(true)
  }

  const counts = outcomes.reduce((summary, outcome) => {
    summary[outcome.status] = (summary[outcome.status] || 0) + 1
    return summary
  }, {})
  const summaryParts = [
    counts.success ? `成功 ${counts.success}` : '',
    counts.awaiting_reconcile ? `已提交待核验 ${counts.awaiting_reconcile}` : '',
    counts.processing ? `执行状态未知 ${counts.processing}` : '',
    counts.need_action ? `需本人验证 ${counts.need_action}` : '',
    counts.failed ? `明确失败 ${counts.failed}` : '',
    counts.skipped ? `安全跳过 ${counts.skipped}` : '',
    counts.request_error ? `请求异常 ${counts.request_error}` : ''
  ].filter(Boolean)
  const outcomeLines = outcomes.map(outcome => h(
    'li',
    { style: { margin: '4px 0' } },
    `JOB-${String(outcome.job_id).padStart(5, '0')} · ${platformName(outcome.platform)} · ${statusLabel(outcome.status)}：${outcome.message}`
  ))
  await ElMessageBox.alert(
    h('div', { style: { lineHeight: '1.6' } }, [
      h('p', { style: { margin: '0 0 8px', fontWeight: '700' } }, summaryParts.join('；') || '批次已结束'),
      h('ul', { style: { maxHeight: '260px', overflow: 'auto', margin: 0, paddingLeft: '20px', fontSize: '11px' } }, outcomeLines),
      (counts.awaiting_reconcile || counts.processing || counts.request_error)
        ? h('p', { style: { margin: '10px 0 0', color: '#985b16' } },
            '状态不明的任务没有自动重试。请先在平台后台核验，再使用“核验平台结果”收口。')
        : null,
      counts.need_action
        ? h('p', { style: { margin: '8px 0 0', color: '#985b16' } },
            '需要本人验证的任务已暂停；完成短信、扫码、滑块等验证后，可从任务列表继续。')
        : null
    ]),
    '连续发布批次已结束',
    { confirmButtonText: '查看任务列表' }
  )
}

const executeRealJob = async (job) => {
  let currentJob
  let articleSnapshot
  let publicationSnapshot
  try {
    const jobResponse = await publishApi.getJob(job.job_id)
    currentJob = jobResponse.data
    articleSnapshot = currentJob?.confirmation_snapshot
    publicationSnapshot = currentJob?.publication_snapshot
  } catch (error) {
    console.error('读取真实发布确认快照失败:', error)
    ElMessage.error('无法读取任务与正文快照，已停止真实发布')
    return
  }
  if (
    !articleSnapshot
    || Number(articleSnapshot.article_id) !== Number(currentJob.article_id)
    || typeof articleSnapshot.title !== 'string'
    || typeof articleSnapshot.content !== 'string'
    || !publicationSnapshot
    || typeof publicationSnapshot.title !== 'string'
    || typeof publicationSnapshot.content !== 'string'
  ) {
    ElMessage.error('任务与实际发布副本的同源确认快照无效，已停止真实发布')
    await fetchJobs(true)
    return
  }
  if (!/^[0-9a-f]{64}$/.test(String(currentJob.confirmation_fingerprint || ''))) {
    ElMessage.warning('任务确认凭据缺失，已停止执行。请刷新任务后重新核对。')
    await fetchJobs(true)
    return
  }
  if (!currentJob.can_execute_real) {
    ElMessage.warning('任务状态已经变化，已停止执行。请刷新后重新确认。')
    await fetchJobs(true)
    return
  }
  const accountsLoaded = await fetchAccounts()
  if (!accountsLoaded) {
    ElMessage.error('账号清单未载入，已停止真实发布')
    return
  }
  const boundAccount = (accountOverview.value.accounts || []).find(account => (
    account.id === currentJob.account_id
    && account.platform_key === currentJob.platform
    && account.status === 'connected'
  ))
  if (!boundAccount) {
    await requestPlatformLogin(platformByKey(currentJob.platform), currentJob)
    return
  }
  const assetText = [
    currentJob.images?.length ? `图片 ${currentJob.images.join('、')}` : '',
    currentJob.video ? `视频 ${currentJob.video}` : ''
  ].filter(Boolean).join('；') || '无附加素材'
  const articleTitle = publicationSnapshot.title || currentJob.article_title || `文章 #${currentJob.article_id}`
  const articleContent = String(publicationSnapshot.content || '').trim() || '（正文为空）'
  const originalLength = countPublishCharacters(articleSnapshot.content)
  const publishLength = countPublishCharacters(articleContent)
  const verificationNotice = publicationSnapshot.verification?.notice
    || '平台可能要求短信、扫码、滑块或安全验证，请保持绑定设备可用。'
  const frequencyLabel = frequencyGuidanceLabel(publicationSnapshot.frequency_guidance)
  const jobCode = `JOB-${String(currentJob.job_id).padStart(5, '0')}`
  const confirmationPhrase = `发布 ${jobCode}`
  const confirmationMessage = h('div', { style: { color: '#423b31', lineHeight: '1.65' } }, [
    h('p', { style: { margin: '0 0 10px' } }, [
      `任务 ${jobCode}（版本 ${currentJob.state_version}）将真实发送到 `,
      h('strong', `${platformName(currentJob.platform)} / ${accountDisplayName(boundAccount)}`),
      '。'
    ]),
    h('p', { style: { margin: '0 0 8px', fontWeight: '700' } }, `《${articleTitle}》`),
    h('div', {
      style: {
        maxHeight: '180px',
        overflow: 'auto',
        padding: '10px 12px',
        border: '1px solid #d8d2c7',
        background: '#faf9f6',
        color: '#4f5354',
        fontSize: '12px',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word'
      }
    }, articleContent),
    h('p', { style: { margin: '8px 0 0', fontSize: '11px', color: '#3c6f6b' } },
      publicationSnapshot.adapted
        ? `实际发送平台短版：原稿 ${originalLength} 字 → 实发 ${publishLength} 字；原稿未修改。`
        : `实际发送 ${publishLength} 字；与当前原稿一致。`),
    h('p', { style: { margin: '10px 0 0' } }, `素材：${assetText}`),
    h('p', { style: { margin: '8px 0 0', padding: '8px 10px', background: '#fff4e5', color: '#8c581d', fontSize: '11px' } },
      `本人验证预警：${verificationNotice}`),
    h('p', { style: { margin: '6px 0 0', fontSize: '10px', color: '#78684f' } },
      `${frequencyLabel}。这是软件保守建议，不是平台官方配额。`),
    h('p', { style: { margin: '6px 0 0', fontSize: '11px', color: '#78684f' } },
      `确认内容绑定指纹：${String(currentJob.confirmation_fingerprint).slice(0, 12)}…`),
    h('p', { style: { margin: '8px 0 0', color: '#985b16', fontWeight: '700' } },
      `平台接受内容后不能依靠本软件撤回。请输入“${confirmationPhrase}”继续。`)
  ])
  try {
    await ElMessageBox.prompt(
      confirmationMessage,
      '强确认 · 真实发布',
      {
        confirmButtonText: `真实发布到 ${boundAccount.account_name}`,
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true,
        inputPlaceholder: confirmationPhrase,
        inputValidator: value => String(value || '').trim() === confirmationPhrase
          || `请输入“${confirmationPhrase}”`
      }
    )
  } catch {
    return
  }

  markJobBusy(currentJob.job_id, true)
  try {
    const response = await publishApi.executeRealJob(currentJob)
    const result = response.data
    if (result.status === 'success') ElMessage.success(`${platformName(currentJob.platform)}真实发布成功`)
    else if (result.status === 'need_action') {
      const requiresReconciliation = Boolean(result.reconciliation_required)
      await ElMessageBox.alert(
        h('div', { style: { lineHeight: '1.65' } }, [
          h('p', { style: { margin: '0 0 8px', fontWeight: '700' } }, result.message || '平台要求人工确认。'),
          requiresReconciliation
            ? h('p', { style: { margin: '0 0 8px' } }, '本次执行调用已经结束；软件没有把未知状态误报成成功。')
            : h('p', { style: { margin: '0 0 8px' } }, verificationNotice),
          h('p', { style: { margin: 0, color: '#985b16' } },
            requiresReconciliation
              ? '请先在平台后台核对是否已经产生内容，再使用“核验平台结果”收口；核验前禁止重复提交。'
              : '请在已打开的平台窗口中本人完成验证，再先核对平台后台是否已经产生内容；不要直接重复提交。')
        ]),
        requiresReconciliation ? '已提交 · 等待平台核验' : '任务已暂停 · 需要本人验证',
        { confirmButtonText: '我知道了', type: 'warning' }
      )
    }
    else if (result.status === 'processing') ElMessage.info(result.message || '平台状态仍在确认中')
    else ElMessage.error(result.message || '真实发布未完成')
    await fetchJobs(true)
  } catch (error) {
    console.error('执行真实发布任务失败:', error)
    const message = batchFailureMessage(error)
    await fetchJobs(true)
    await ElMessageBox.alert(
      h('div', { style: { lineHeight: '1.65' } }, [
        h('p', { style: { margin: '0 0 8px', fontWeight: '700' } }, message),
        h('p', { style: { margin: 0, color: '#985b16' } },
          '任务状态已刷新。若平台窗口曾经打开，请先到平台后台核对是否已经产生内容；状态不明时不要直接重复提交。')
      ]),
      '真实发布未完成',
      { confirmButtonText: '查看任务状态', type: 'error' }
    )
  } finally {
    markJobBusy(currentJob.job_id, false)
  }
}

const retryJob = async (job) => {
  if (job.status === 'need_action') {
    const platform = platformByKey(job.platform)
    try {
      await ElMessageBox.confirm(
        h('div', { style: { lineHeight: '1.65' } }, [
          h('p', { style: { margin: '0 0 8px', fontWeight: '700' } }, job.message || '上次流程需要人工确认。'),
          h('p', { style: { margin: '0 0 8px' } }, platform.verification?.notice || '请准备完成平台要求的短信、扫码、滑块或安全验证。'),
          h('p', { style: { margin: 0, color: '#985b16' } },
            '继续前请先在平台后台核对是否已经发布，避免状态不明时重复发送；重新排队后仍需再次执行强确认。')
        ]),
        '继续待人工任务',
        {
          confirmButtonText: '已核对平台，重新排队',
          cancelButtonText: '先不继续',
          type: 'warning'
        }
      )
    } catch {
      return
    }
  }
  markJobBusy(job.job_id, true)
  try {
    const response = await publishApi.retryJob(job.job_id)
    const retried = response.data
    if (retried.demo) {
      await publishApi.executeDemoJob(retried.job_id)
      ElMessage.success('演示任务已重新执行')
    } else {
      ElMessage.success('任务已重新排队；真实发布仍需再次确认')
    }
    await fetchJobs(true)
  } catch (error) {
    console.error('重试发布任务失败:', error)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const resetReconciliationDialog = () => {
  if (reconciliationDialog.submitting) return
  reconciliationDialog.job = null
  reconciliationDialog.decision = 'published'
  reconciliationDialog.note = ''
  reconciliationDialog.resultUrl = ''
  reconciliationDialog.platformChecked = false
  reconciliationDialog.executionStoppedChecked = false
}

const openReconciliation = async job => {
  if (batchExecuting.value || reconciliationDialog.submitting) return
  markJobBusy(job.job_id, true)
  try {
    const response = await publishApi.getJob(job.job_id)
    const current = response.data
    if (current?.status !== 'need_action' || !current?.reconciliation_required) {
      ElMessage.warning('任务状态已经变化，请按刷新后的状态继续')
      await fetchJobs(true)
      return
    }
    reconciliationDialog.job = current
    reconciliationDialog.decision = 'published'
    reconciliationDialog.note = ''
    reconciliationDialog.resultUrl = ''
    reconciliationDialog.platformChecked = false
    reconciliationDialog.executionStoppedChecked = false
    reconciliationDialog.visible = true
  } catch (error) {
    console.error('读取人工对账任务失败:', error)
    ElMessage.error(`无法读取最新任务状态：${batchFailureMessage(error)}`)
  } finally {
    markJobBusy(job.job_id, false)
  }
}

const submitReconciliation = async () => {
  if (!canSubmitReconciliation.value || reconciliationDialog.submitting) return
  const job = reconciliationDialog.job
  const decision = reconciliationDialog.decision
  const resultUrl = decision === 'published'
    ? reconciliationDialog.resultUrl.trim()
    : ''
  reconciliationDialog.submitting = true
  markJobBusy(job.job_id, true)
  try {
    const response = await publishApi.reconcileJob(job, {
      decision,
      verification_note: reconciliationDialog.note.trim(),
      result_url: resultUrl,
      execution_stopped_confirmed: reconciliationDialog.executionStoppedChecked
    })
    const reconciled = response.data
    reconciliationDialog.visible = false
    if (reconciled.status === 'success') {
      ElMessage.success('已按平台后台证据记为发布成功；没有再次发送内容')
    } else {
      ElMessage.warning('已按平台后台证据结案为未发布；现在可以安全地重新排队')
    }
    await fetchJobs(true)
  } catch (error) {
    console.error('人工对账失败:', error)
    ElMessage.error(`人工对账未写入：${batchFailureMessage(error)}`)
    await fetchJobs(true)
  } finally {
    reconciliationDialog.submitting = false
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
const batchStatusLabels = {
  awaiting_reconcile: '已提交待核验',
  skipped: '安全跳过',
  request_error: '请求异常',
  unknown: '结果未知'
}
const statusLabel = (status) => (
  statusOptions.find(item => item.value === status)?.label
  || batchStatusLabels[status]
  || status
)

const latestPlatformState = (platformKey) => {
  const job = jobs.value.find(item => item.platform === platformKey)
  return job ? statusLabel(job.status) : '等待创建任务'
}

const statusProgress = (status) => ({
  scheduled: 10,
  queued: 18,
  processing: 72,
  need_action: 92,
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
  const normalized = assumeUtc && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
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
    scheduleJobPoll()
  }, 5000)
}

onMounted(async () => {
  pollingStopped = false
  await Promise.allSettled([fetchPlatforms(), fetchArticles(), fetchJobs()])
  if (accountBindingRequired.value) await fetchAccounts()
  const requestedJobId = Number(route.query.job_id)
  if (Number.isInteger(requestedJobId) && requestedJobId > 0) {
    try {
      const response = await publishApi.getJob(requestedJobId)
      const requestedJob = response.data
      const requestedPlatform = platformByKey(requestedJob.platform)
      if (!isPlatformSelectable(requestedPlatform)) {
        ElMessage.warning(requestedPlatform.unavailable_reason || '原任务平台当前不可用，无法重建')
      } else {
        selectedArticleId.value = requestedJob.article_id
        selectedPlatforms.value = [requestedJob.platform]
        selectedAccountIds[requestedJob.platform] = null
        ElMessage.info('已定位原稿件与平台；请重新选择账号并核对素材后创建新任务')
      }
    } catch (error) {
      console.error('恢复待重建任务失败:', error)
    } finally {
      await router.replace({ path: '/publish-center' })
    }
  }
  scheduleJobPoll()
})

watch(selectedArticleId, async value => {
  selectedImage.value = null
  selectedVideo.value = null
  clearPublishPreflight()
  if (value) {
    await Promise.allSettled([
      recommendImage({ silent: true }),
      refreshPublishPreflight({ preserveEdits: false })
    ])
  }
})

watch(
  () => selectedPlatforms.value.join('|'),
  () => refreshPublishPreflight({ preserveEdits: true })
)

onBeforeUnmount(() => {
  pollingStopped = true
  if (pollTimer) window.clearTimeout(pollTimer)
  if (preflightDebounceTimer) window.clearTimeout(preflightDebounceTimer)
  pollTimer = null
  preflightDebounceTimer = null
})
</script>

<style lang="scss" scoped>
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

.publish-mode-banner {
  display: grid;
  grid-template-columns: 128px minmax(0, 1fr);
  gap: 20px;
  align-items: center;
  margin: 0 0 18px;
  padding: 15px 18px;
  border: 1px solid #cbd8d5;
  border-left: 4px solid #438e89;
  background: #f5faf8;

  > span {
    color: #377b77;
    font: 800 9px/1.25 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  strong { color: var(--harbor); font-size: 13px; }
  p { margin: 4px 0 0; color: var(--muted); font-size: 11px; line-height: 1.6; }

  &.armed {
    border-color: #dfc18f;
    border-left-color: #b66d16;
    background: #fff8ec;

    > span { color: #a35e12; }
    strong { color: #62431e; }
    p { color: #796345; }
  }

  &.blocked,
  &.locked {
    border-color: #dac8a5;
    border-left-color: #9b742e;
    background: #fffbf2;

    > span { color: #846326; }
  }
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.platform-option {
  position: relative;
  min-height: 142px;
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
  &.unavailable {
    cursor: not-allowed;
    border-color: #dfe3e1;
    background: #f5f6f5;
    color: #6f7778;
    transform: none;

    .platform-code,
    .selection-mark { color: #8b9493; }
    small { color: #747c7d; }
  }

  .platform-code {
    display: block;
    color: #48827f;
    font: 700 9px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  .platform-name { display: block; margin-top: 11px; font-size: 15px; font-weight: 700; }
  small { display: block; margin-top: 5px; color: var(--muted); font-size: 10px; }
  em {
    display: block;
    margin-top: 8px;
    color: #3d6e6b;
    font-size: 9px;
    font-style: normal;
    line-height: 1.45;
  }
  > i {
    display: inline-block;
    margin-top: 6px;
    padding: 3px 5px;
    background: #fff4e5;
    color: #94601f;
    font-size: 8px;
    font-style: normal;
  }
  .selection-mark { position: absolute; top: 10px; right: 10px; color: #338e89; font-size: 14px; }
}

.content-variant-panel {
  margin-top: 18px;
  border: 1px solid #91bbb7;
  background: #f8fcfb;
}

.variant-loading,
.variant-error {
  padding: 18px;
  color: var(--muted);
  font-size: 11px;
}

.variant-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--danger);
  background: #fff5f3;
}

.variant-list { display: grid; gap: 12px; padding: 14px; }

.variant-card {
  padding: 14px;
  border: 1px solid #cadbd7;
  background: #fff;

  &.invalid { border-color: #d8877e; box-shadow: inset 3px 0 #c15454; }

  > header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 10px;

    div { display: grid; gap: 3px; }
    span { color: #388b87; font: 700 9px/1 "Cascadia Mono", monospace; }
    strong { font-size: 13px; }
    b { color: #4d696e; font: 700 10px/1.4 "Cascadia Mono", monospace; text-align: right; }
  }

  :deep(.el-textarea__inner) {
    border-radius: 2px;
    font-size: 12px;
    line-height: 1.65;
  }

  > p {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 10px;
  }

  > footer {
    display: flex;
    justify-content: flex-end;
    margin-top: 4px;
  }
}

.variant-field-label {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
  color: #35545a;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
}

.variant-meta {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  margin-top: 8px;
  font-size: 10px;

  span { color: var(--muted); }
  strong { color: #267d78; white-space: nowrap; }
  .over-limit { color: var(--danger); }
}

.citeability-receipt {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid #b9d8d3;
  background: #f2faf8;

  > div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  span {
    color: #367d79;
    font: 700 9px/1.4 "Cascadia Mono", monospace;
    letter-spacing: .08em;
    text-transform: uppercase;
  }

  strong { color: #226e69; font-size: 10px; }

  p,
  ul {
    margin: 7px 0 0;
    color: #63777a;
    font-size: 10px;
    line-height: 1.55;
  }

  ul { padding-left: 16px; }

  &.stale {
    border-color: #d6c08f;
    background: #fffaf0;

    strong { color: #8b641f; }
  }

  &.failed {
    border-color: #dfaaa3;
    background: #fff5f3;

    strong,
    li { color: #a44d48; }
  }
}

.verification-panel {
  margin-top: 18px;
  padding: 15px;
  border: 1px solid #d9bd8e;
  border-left: 3px solid var(--amber);
  background: #fffaf1;

  > p {
    margin: 11px 0 0;
    color: #856e4e;
    font-size: 9px;
    line-height: 1.55;
  }
}

.verification-heading {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 12px;

  > span { color: #a06623; font: 700 9px/1.4 "Cascadia Mono", monospace; }
  > div { display: grid; gap: 4px; }
  strong { color: #68481f; font-size: 13px; }
  small { color: #836f53; font-size: 10px; line-height: 1.5; }
}

.verification-list {
  display: grid;
  gap: 7px;
  margin-top: 12px;

  article {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
    padding-top: 7px;
    border-top: 1px solid #ead8b9;
  }

  strong { font-size: 11px; }
  span { color: #6f6556; font-size: 10px; line-height: 1.5; }
  small { color: #8b5d24; font-size: 9px; white-space: nowrap; }
}

.account-routing {
  margin-top: 18px;
  padding: 18px;
  border: 1px solid #c9d8d4;
  background: #f8fbfa;
}

.account-route-list {
  display: grid;
  gap: 9px;
  margin-top: 14px;
}

.account-route-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 12px;
  padding: 10px 12px;
  border-left: 3px solid var(--danger);
  background: #fff4f2;
  color: #8f4744;
  font-size: 11px;
}

.account-route-row {
  display: grid;
  grid-template-columns: minmax(150px, .65fr) minmax(220px, 1fr);
  gap: 18px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid var(--rule);

  > div:first-child {
    display: grid;
    gap: 3px;
  }

  small { color: var(--muted); }
  :deep(.el-select) { width: 100%; }
}

.account-mark {
  color: #257b69;

  &.missing { color: var(--danger); }
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

.schedule-manual-confirm {
  grid-column: 1 / -1;
  padding: 11px 12px;
  border-left: 3px solid #438e89;
  background: #f0f7f5;

  strong { color: #285f5c; font-size: 12px; }
  p { margin: 4px 0 0; color: #5d7573; font-size: 10px; line-height: 1.55; }
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

.schedule-mark {
  align-self: flex-start;
  color: #a36724 !important;
  font: 700 8px/1 "Cascadia Mono", monospace !important;
}

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
  gap: 6px;

  :deep(.el-button) { margin-left: 0; border-radius: 3px; }
  :deep(.el-checkbox) { margin-right: 2px; }
  :deep(.el-checkbox__label) { padding-left: 4px; font-size: 10px; }
  .no-action { color: #9aa8a6; font-size: 10px; }
}

.reconciliation-form {
  display: grid;
  gap: 13px;

  > label { color: #345159; font-size: 12px; font-weight: 700; }
  :deep(.el-radio-group) { width: 100%; }
  :deep(.el-radio-button) { flex: 1; }
  :deep(.el-radio-button__inner) { width: 100%; }
  :deep(.el-checkbox) { height: auto; align-items: flex-start; white-space: normal; }
  :deep(.el-checkbox__label) { line-height: 1.5; white-space: normal; }
}

.reconciliation-warning {
  padding: 12px 13px;
  border-left: 3px solid var(--amber);
  background: #fbf6ee;

  strong { color: #50483e; font-size: 12px; }
  p { margin: 6px 0 0; color: #756d62; font-size: 11px; line-height: 1.6; }
}

.reconciliation-next-step {
  margin: 0;
  padding: 9px 11px;
  background: #f3f7f6;
  color: #53706e;
  font-size: 10px;
  line-height: 1.55;
}

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
}

@media (max-width: 680px) {
  .page-heading { gap: 18px; }
  .live-indicator { width: 100%; box-sizing: border-box; }
  .dispatch-form, .route-manifest { padding: 20px; }
  .platform-grid { grid-template-columns: repeat(2, 1fr); }
  .publish-mode-banner { grid-template-columns: 1fr; gap: 7px; }
  .account-route-row { grid-template-columns: 1fr; gap: 8px; }
  .account-route-error { align-items: stretch; flex-direction: column; }
  .asset-heading { grid-template-columns: 1fr auto; }
  .asset-heading .asset-code { grid-column: 1 / -1; }
  .asset-empty, .asset-record { grid-template-columns: auto 1fr; }
  .asset-empty :deep(.el-button), .asset-record :deep(.el-button) { grid-column: 1 / -1; width: 100%; }
  .variant-card > header { align-items: flex-start; flex-direction: column; }
  .variant-card > header b { text-align: left; }
  .variant-meta { align-items: flex-start; flex-direction: column; }
  .verification-list article { grid-template-columns: 1fr; gap: 4px; }
  .verification-list small { white-space: normal; }
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
