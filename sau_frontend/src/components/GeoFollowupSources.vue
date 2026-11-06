<template>
  <section v-if="view.routes.length" class="followup-sources" aria-label="后续复测来源渠道" data-testid="geo-followup-sources">
    <h3>后续复测的渠道线索</h3>
    <p class="summary">{{ view.probeCount }} 次复测 · {{ view.caseCount }} 道原题 · {{ view.sourceCount }} 条来源 · {{ view.routes.length }} 个候选渠道</p>
    <p class="boundary">这些来源不证明本公司的业务事实，也不表示入口已核对、获得投稿权限或 GEO 已有效果。</p>
    <details><summary>展开查看来源和对应原题</summary>
      <article v-for="route in view.routes" :key="route.id" class="route">
        <h4>{{ route.label }} <small>{{ surfaceLabel(route.surface) }}</small></h4>
        <p class="note">{{ route.note }}</p>
        <ul><li v-for="source in route.sources" :key="`${source.probeId}:${source.caseId}:${source.sourceRef}`">
          <p v-if="source.url"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a></p>
          <p v-else>{{ source.title || '来源标题待补' }} · 公开网址未确认</p>
          <p v-if="source.url" class="url">{{ source.url }}</p>
          <p class="attribution">原题 {{ source.caseId }} · {{ modeLabel(source.clientMode) }} · {{ source.modelLabel }} · {{ provenanceLabel(source) }}</p>
          <details class="source-details"><summary>查看原题与来源编号</summary><p class="question">{{ source.question }}</p><p class="attribution">复测记录：{{ source.probeId }}</p><p class="attribution">来源引用：{{ source.sourceRef }}</p></details>
        </li></ul>
      </article>
    </details>
  </section>
</template>

<script setup>
import {computed} from 'vue'
import {followupSourcesView} from '@/utils/geoFollowupSources'
const props = defineProps({cycle: {type: Object, required: true}})
const view = computed(() => followupSourcesView(props.cycle))
const surfaceLabel = value => ({owned_site: '官网范围', community_candidate: '公开平台候选', vendor_documentation: '文档来源', unknown: '平台待核对', url_unknown: '网址待补'})[value] || '待核对'
const provenanceLabel = source => source.source === 'user_reported_external' ? '用户提供的来源，未独立核验' : source.nativeVerified ? '软件采集来源，正文事实未核实' : '研究导入来源，未独立核验'
const modeLabel = value => ({consumer: '普通对话', work: '工作模式', unknown: '对话模式未知'})[value] || '对话模式未知'
</script>

<style scoped>
.followup-sources{margin:16px 0;padding:14px 16px;border:1px solid var(--geo-border,#d9e5e4);border-radius:10px;background:#f8fbfa;min-width:0;overflow-wrap:anywhere;color:var(--geo-text,#18343a)}h3,h4,p{margin:0}h3{font-size:14px;font-weight:600}.summary{margin-top:7px;font-size:13px}.boundary,.note,.attribution,.question,.url{font-size:12px;line-height:1.8;color:var(--geo-muted,#60747a)}.boundary{margin-top:7px}summary{padding-top:10px;cursor:pointer;font-size:12px;color:var(--geo-primary,#286653)}summary:focus-visible,a:focus-visible{outline:2px solid var(--geo-primary,#286653);outline-offset:3px}.route{margin-top:13px;border-top:1px solid var(--geo-border,#d9e5e4);padding-top:12px}h4{font-size:13px;font-weight:550}small{margin-left:7px;font-size:11px;font-weight:400;color:var(--geo-muted,#60747a)}.note{margin-top:4px}ul{margin:8px 0 0;padding-left:18px}li{margin-top:10px;font-size:13px;line-height:1.7}a{color:var(--geo-primary,#286653)}.question{margin-top:6px}.source-details summary{padding-top:3px;font-size:11px}@media(max-width:600px){.followup-sources{padding:12px}}
</style>
