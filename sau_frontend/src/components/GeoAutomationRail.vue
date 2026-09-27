<template>
  <section class="automation-ledger" aria-label="一键 GEO 执行轨道">
    <article
      v-for="item in run?.items || []"
      :key="item.id"
      :class="['automation-row', `is-${item.status}`]"
    >
      <header class="row-identity">
        <span>A{{ String(item.position).padStart(2, '0') }}</span>
        <div>
          <strong>{{ item.topic }}</strong>
          <small>{{ automationStatusLabel(item.status) }} · {{ item.message || stageMessage(item) }}</small>
        </div>
      </header>

      <ol class="execution-spine" :aria-label="`${item.topic}执行阶段`">
        <li
          v-for="stage in AUTOMATION_STAGES"
          :key="stage.key"
          :class="`stage-${automationStageState(item, stage.key, run?.config?.publish_mode)}`"
          :aria-current="automationStageState(item, stage.key, run?.config?.publish_mode) === 'active' ? 'step' : undefined"
        >
          <span class="stage-mark" aria-hidden="true">
            {{ stageSymbol(automationStageState(item, stage.key, run?.config?.publish_mode)) }}
          </span>
          <b>{{ stage.key === 'publishing' && run?.config?.publish_mode === 'prepare' ? '未发布' : stage.label }}</b>
        </li>
      </ol>

      <aside v-if="['need_action', 'failed', 'skipped'].includes(item.status)" class="issue-pocket" role="alert">
        <span>已暂存，主流程继续</span>
        <strong>{{ item.error?.message || item.message || '该条目需要人工检查' }}</strong>
      </aside>

      <footer v-if="item.article_id || item.publish_jobs?.length" class="row-evidence">
        <button
          v-if="item.article_id"
          type="button"
          :disabled="locked"
          :title="locked ? '运行中的文章已锁定，避免并发覆盖' : '打开文章'"
          @click="$emit('open-article', item.article_id)"
        >
          ARTICLE {{ item.article_id }}
        </button>
        <button
          v-for="job in item.publish_jobs || []"
          :key="job.job_id"
          type="button"
          @click="$emit('open-publish', job.job_id)"
        >
          {{ String(job.platform).toUpperCase() }} · JOB {{ job.job_id }} · {{ job.status }}
        </button>
        <code v-if="item.generation_request_id">{{ item.generation_request_id }}</code>
      </footer>
    </article>
  </section>
</template>

<script setup>
import {
  AUTOMATION_STAGES,
  automationStageState,
  automationStatusLabel
} from '@/utils/geoAutomation'

defineProps({
  run: {
    type: Object,
    default: null
  },
  locked: {
    type: Boolean,
    default: false
  }
})

defineEmits(['open-article', 'open-publish'])

const stageSymbol = state => ({
  complete: '✓',
  active: '●',
  issue: '!',
  not_applicable: '—',
  pending: '○'
}[state] || '○')

const stageMessage = item => ({
  queued: '等待前序文章',
  generating: '模型正在生成草稿',
  optimizing: '正在执行 GEO 优化与引用门禁',
  preflighting: '正在生成并检查平台副本',
  publishing: '按渠道串行执行'
}[item?.status] || '检查点已保存')
</script>

<style lang="scss" scoped>
.automation-ledger {
  display: grid;
  gap: 12px;
}

.automation-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.78fr) minmax(360px, 1.5fr);
  gap: 14px 24px;
  padding: 18px 20px;
  border: 1px solid var(--geo-line);
  border-left: 3px solid var(--geo-line-strong);
  background: var(--geo-surface);

  &.is-generating,
  &.is-optimizing,
  &.is-preflighting,
  &.is-publishing {
    border-left-color: var(--geo-success);
  }

  &.is-need_action,
  &.is-failed {
    border-left-color: var(--geo-danger);
  }
}

.row-identity {
  display: flex;
  min-width: 0;
  gap: 12px;

  > span {
    flex: 0 0 auto;
    font: 700 12px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace;
    color: var(--geo-muted);
  }

  div {
    display: grid;
    min-width: 0;
    gap: 5px;
  }

  strong {
    overflow: hidden;
    color: var(--geo-ink);
    font-size: 15px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  small {
    color: var(--geo-muted);
    line-height: 1.5;
  }
}

.execution-spine {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  align-items: start;
  margin: 0;
  padding: 0;
  list-style: none;

  li {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 7px;
    color: var(--geo-subtle);
    font-size: 12px;

    &:not(:last-child)::after {
      position: absolute;
      top: 10px;
      left: calc(50% + 13px);
      width: calc(100% - 26px);
      height: 1px;
      background: var(--geo-line);
      content: '';
    }

    &.stage-complete {
      color: var(--geo-success);

      &:not(:last-child)::after { background: var(--geo-success); }
    }

    &.stage-active { color: var(--geo-ink); }
    &.stage-issue { color: var(--geo-danger); }
    &.stage-not_applicable { color: var(--geo-subtle); }
  }
}

.stage-mark {
  position: relative;
  z-index: 1;
  display: grid;
  width: 21px;
  height: 21px;
  place-items: center;
  border: 1px solid currentColor;
  border-radius: 50%;
  background: var(--geo-surface);
  font: 700 11px/1 ui-monospace, "Cascadia Mono", Consolas, monospace;
}

.stage-active .stage-mark {
  color: var(--geo-success);
  animation: rail-pulse 1.8s ease-in-out infinite;
}

.issue-pocket,
.row-evidence {
  grid-column: 1 / -1;
}

.issue-pocket {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 8px 14px;
  padding: 10px 12px;
  border-left: 2px solid var(--geo-danger);
  background: #fbf4f3;
  color: var(--geo-danger);

  span {
    font: 700 11px/1.6 ui-monospace, "Cascadia Mono", Consolas, monospace;
    text-transform: uppercase;
  }

  strong { font-size: 13px; line-height: 1.55; }
}

.row-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--geo-line);

  button,
  code {
    padding: 4px 7px;
    border: 1px solid var(--geo-line);
    background: transparent;
    color: var(--geo-muted);
    font: 600 10px/1.4 ui-monospace, "Cascadia Mono", Consolas, monospace;
  }

  button { cursor: pointer; }
  button:hover { border-color: var(--geo-ink); color: var(--geo-ink); }
  button:disabled { cursor: not-allowed; opacity: 0.48; }
}

@keyframes rail-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(50, 103, 77, 0); }
  50% { box-shadow: 0 0 0 5px rgba(50, 103, 77, 0.12); }
}

@media (max-width: 980px) {
  .automation-row { grid-template-columns: 1fr; }
  .issue-pocket,
  .row-evidence { grid-column: 1; }
}

@media (max-width: 620px) {
  .automation-row { padding: 16px; }

  .execution-spine {
    grid-template-columns: 1fr;
    gap: 6px;

    li {
      grid-template-columns: 24px 1fr;
      justify-items: start;
      align-items: center;

      &:not(:last-child)::after {
        top: 22px;
        left: 10px;
        width: 1px;
        height: 18px;
      }
    }
  }

  .issue-pocket { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .stage-active .stage-mark { animation: none; }
}
</style>
