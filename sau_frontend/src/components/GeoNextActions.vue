<template>
  <aside v-if="view.actions.length" class="geo-next-actions" aria-label="根据采集记录继续优化" data-testid="geo-next-actions">
    <div class="next-heading"><div><strong>接下来做什么</strong><p>按这轮已保存的记录，继续处理缺口。</p></div><span>{{ view.actions.length }} 项待核对</span></div>
    <ol><li v-for="action in view.actions.slice(0,expanded ? 6 : 3)" :key="action.id"><div><strong>{{ action.label }}</strong><p>{{ action.reason }}</p></div><el-button :disabled="busy" text @click="emit('navigate',action)" :data-testid="`next-action-${action.key}`">查看 <span aria-hidden="true">→</span></el-button></li></ol>
    <el-button v-if="view.actions.length > 3" text size="small" @click="expanded=!expanded">{{ expanded ? '收起' : `再看 ${view.actions.length-3} 项` }}</el-button>
    <details v-if="view.attention.length" class="next-evidence"><summary>为什么有这些建议</summary><p v-for="(item,index) in view.attention" :key="index">{{ item.message }}<small v-if="item.evidence_ids.length">依据：{{ labels(item.evidence_ids) }}</small></p><p class="next-note">建议只用于导航；发布、索引和 AI 实际采用仍分别核对。</p></details>
  </aside>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {nextActions} from '@/utils/geoNextActions'
const props=defineProps({cycle:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['navigate']), expanded=ref(false)
const view=computed(()=>nextActions(props.cycle))
const labels=ids=>ids.map(id=>view.value.evidence.find(item=>item.id===id)?.label || id).join('、')
watch(()=>props.cycle.id,()=>{expanded.value=false})
</script>
<style scoped>
.geo-next-actions{border:1px solid var(--geo-border,#d9e5e4);border-radius:12px;padding:18px 20px;margin:18px 0;background:#fbfdfc;scroll-margin-top:22px}.next-heading{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.next-heading strong{font-size:15px;color:var(--geo-text,#18343a)}.next-heading p,.next-heading>span{margin:5px 0 0;color:var(--geo-muted,#60747a);font-size:12px}.next-heading>span{white-space:nowrap}.geo-next-actions ol{list-style:none;padding:0;margin:8px 0 0}.geo-next-actions li{display:flex;gap:16px;align-items:center;border-top:1px solid var(--geo-border,#e1e9e7);padding:12px 0}.geo-next-actions li>div{flex:1;min-width:0}.geo-next-actions li strong{font-size:13px;font-weight:500}.geo-next-actions li p{font-size:12px;color:var(--geo-muted,#60747a);line-height:1.7;margin:5px 0 0}.next-evidence{font-size:12px;color:var(--geo-muted,#60747a);line-height:1.8}.next-evidence summary{cursor:pointer;padding:8px 0}.next-evidence p{margin:8px 0}.next-evidence small{display:block;color:#71858a}.next-note{border-top:1px solid var(--geo-border,#d9e5e4);padding-top:8px}@media(max-width:600px){.geo-next-actions{padding:16px}.next-heading>span{display:none}.geo-next-actions li{gap:8px;align-items:flex-start}}
</style>
