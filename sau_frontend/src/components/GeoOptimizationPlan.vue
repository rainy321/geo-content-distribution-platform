<template>
  <section v-if="view" class="optimization-plan" aria-label="本次 GEO 优化计划" data-testid="geo-optimization-plan">
    <header><h3>本次 GEO 优化计划</h3><span>已安排 {{ view.questionsWithActions }} 道原题<span v-if="view.uncoveredCaseIds.length"> · {{ view.uncoveredCaseIds.length }} 道待安排</span></span></header>
    <div v-if="view.actions.length > 1" class="plan-choices" role="group" aria-label="选择优化行动">
      <button v-for="action in view.actions" :key="action.id" type="button" :disabled="busy" :aria-pressed="view.selected.id===action.id" :title="action.topic" @click="select(action.id)">{{ action.caseIds.join('、') }} · {{ action.topic }}</button>
    </div>
    <div class="plan-current" :key="`${cycle.id}:${view.selected.id}`">
      <div class="plan-title"><h4>{{ view.selected.topic }}</h4><span v-for="goal in view.selected.goals" :key="goal.key">{{ goal.label }}</span></div>
      <p v-if="view.selected.gap"><strong>解决什么</strong>{{ preview(view.selected.gap) }}</p>
      <p v-if="view.selected.changeSummary"><strong>具体改动</strong>{{ preview(view.selected.changeSummary) }}</p>
      <p v-if="view.needsRefresh" class="plan-refresh">收到新反馈，建议更新选题后再核对这份计划。</p>
      <details><summary>查看写作要求、依据与原题验收</summary><div class="plan-details">
        <p v-if="view.selected.gap.length>160" class="plan-full-summary"><strong>完整缺口：</strong>{{ view.selected.gap }}</p><p v-if="view.selected.changeSummary.length>160" class="plan-full-summary"><strong>完整改动：</strong>{{ view.selected.changeSummary }}</p>
        <div class="plan-columns"><div v-if="view.selected.writingRequirements.length"><h5>文章要补什么</h5><ul><li v-for="(item,index) in view.selected.writingRequirements" :key="index">{{ item }}</li></ul></div><div v-if="view.selected.evidenceNeeded.length"><h5>先核对哪些依据</h5><ul><li v-for="(item,index) in view.selected.evidenceNeeded" :key="index">{{ item }}</li></ul></div></div>
        <h5>优先核对来源</h5><ul v-if="view.selected.sourceRoutes.length" class="plan-sources"><li v-for="route in view.selected.sourceRoutes" :key="route.id">{{ route.label }}<small>来源编号 {{ route.sourceIds.join('、') }}</small></li></ul><p v-else class="plan-muted">尚无匹配来源，先补充可核对的公开资料。</p>
        <h5>完成后按原题看什么</h5><ol class="plan-checks"><li v-for="check in view.selected.checks" :key="check.caseId"><span>{{ check.caseId }} · {{ check.label }}</span><p>{{ check.question }}</p><small>{{ check.signal }}</small></li></ol>
      </div></details>
    </div>
    <p v-if="view.note" class="plan-note">{{ view.note }}</p>
  </section>
</template>

<script setup>
import {computed} from 'vue'
import {optimizationPlanView,optimizationPlanSelection} from '@/utils/geoOptimizationPlan'
const props=defineProps({cycle:{type:Object,required:true},selectedBriefId:{type:String,default:''},busy:Boolean})
const emit=defineEmits(['select'])
const view=computed(()=>optimizationPlanView(props.cycle,props.selectedBriefId))
const select=id=>{const selected=optimizationPlanSelection(view.value,id,props.busy);if(selected)emit('select',selected)}
const preview=value=>value.length>160 ? value.slice(0,160)+'…' : value
</script>

<style scoped>
.optimization-plan{min-width:0;max-width:100%;box-sizing:border-box;margin:18px 0 22px;padding:16px 18px;border:1px solid var(--geo-border,#d9e5e4);border-radius:12px;background:#f8fbfa;color:var(--geo-text,#18343a)}header{display:flex;align-items:center;justify-content:space-between;gap:12px}h3,h4,h5,p{margin:0}h3{font-size:14px;font-weight:600}header>span,.plan-muted,.plan-note{font-size:12px;color:var(--geo-muted,#60747a)}.plan-choices{min-width:0;display:flex;gap:7px;overflow-x:auto;padding:12px 0 3px;max-width:100%}.plan-choices button{flex:0 0 auto;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid var(--geo-border,#d9e5e4);border-radius:7px;background:white;padding:7px 10px;font:inherit;font-size:12px;color:var(--geo-muted,#60747a);cursor:pointer;transition:background-color .16s,color .16s,border-color .16s}.plan-choices button[aria-pressed=true]{background:#e8f3ef;border-color:#aacdc1;color:var(--geo-primary,#286653)}.plan-choices button:focus-visible,summary:focus-visible{outline:2px solid var(--geo-primary,#286653);outline-offset:3px}.plan-choices button:disabled{cursor:default;opacity:.6}.plan-title{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:13px 0 10px}.plan-title h4{font-size:14px;font-weight:550;overflow-wrap:anywhere}.plan-title>span{font-size:11px;color:var(--geo-primary,#286653);background:#eaf3ef;border-radius:4px;padding:3px 6px}.plan-current>p{font-size:13px;line-height:1.8;margin-top:7px;overflow-wrap:anywhere}.plan-current>p>strong{font-weight:500;color:var(--geo-muted,#60747a);margin-right:12px}.plan-current>.plan-refresh{font-size:12px;color:#8a651d}details{margin-top:12px;border-top:1px solid var(--geo-border,#d9e5e4)}summary{cursor:pointer;font-size:12px;color:var(--geo-primary,#286653);padding:11px 0 0}.plan-details{padding-top:12px;font-size:12px;line-height:1.8;overflow-wrap:anywhere}.plan-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px 24px}.plan-details h5{font-size:12px;font-weight:600;margin:0 0 6px}.plan-details ul,.plan-checks{margin:0 0 14px;padding-left:18px}.plan-details li{margin:4px 0}.plan-sources small{margin-left:9px;color:var(--geo-muted,#60747a)}.plan-checks li{padding:2px 0 7px}.plan-checks span,.plan-checks small{color:var(--geo-muted,#60747a)}.plan-checks p{margin:4px 0}.plan-note{margin-top:12px;line-height:1.7}.plan-muted{margin-bottom:14px}@media(max-width:600px){.optimization-plan{padding:14px}header{align-items:flex-start;flex-direction:column;gap:5px}.plan-columns{grid-template-columns:1fr;gap:0}.plan-sources small{display:block;margin-left:0}.plan-choices button{max-width:200px}}@media(prefers-reduced-motion:reduce){.plan-choices button{transition:none}}
</style>
