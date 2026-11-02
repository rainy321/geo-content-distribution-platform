<template>
  <aside v-if="view" class="discovery-funnel" aria-label="本轮搜索发现诊断" data-testid="geo-discovery-funnel">
    <header><strong>先补哪一段搜索发现</strong><p>把自然需求、点名查询和给网址核验分开看，再决定下一步。</p></header>
    <ol class="funnel-stages">
      <li v-for="stage in view.rows" :key="stage.key" :class="stage.state">
        <span>{{ stage.label }}</span><strong>{{ stage.stateLabel }}</strong>
        <small v-for="row in stage.cases" :key="row.id">{{ row.id }} · {{ row.mode }} · {{ row.model }}</small>
      </li>
    </ol>
    <p v-if="!view.matched" class="funnel-note" role="status">本轮题目的模式或模型条件不一致或尚未确认，暂不合并判断优化方向。</p>
    <ol v-if="view.priorities.length" class="funnel-actions">
      <li v-for="item in view.priorities" :key="item.id"><div><strong>{{ item.label }}</strong><p>{{ item.reason }}</p></div><el-button text :disabled="busy" @click="emit('navigate',item.action)">去处理 <span aria-hidden="true">→</span></el-button></li>
    </ol>
    <details><summary>查看原题与判断口径</summary><section v-for="stage in view.rows" :key="stage.key"><p>{{ stage.note }}</p><p v-for="row in stage.cases" :key="row.id"><strong>{{ row.id }}</strong> {{ row.question }}<small v-if="stage.key!=='natural_discovery'">{{ row.citation }}</small></p></section><p>{{ view.note }}</p><p>来源列表和正文引用分别记录；品牌字面出现、给网址能阅读，都不等于自然推荐或优化成功。</p></details>
  </aside>
</template>
<script setup>
import {computed} from 'vue'
import {discoveryFunnelView} from '@/utils/geoDiscoveryFunnel'
const props=defineProps({cycle:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['navigate'])
const view=computed(()=>discoveryFunnelView(props.cycle))
</script>
<style scoped>
.discovery-funnel{border:1px solid var(--geo-border,#d9e5e4);border-radius:14px;padding:20px;margin:18px 0;background:#fff}.discovery-funnel header strong{font-size:16px;color:var(--geo-text,#18343a)}.discovery-funnel header p,.funnel-note{margin:7px 0 14px;font-size:12px;color:var(--geo-muted,#60747a);line-height:1.7}.funnel-stages{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;padding:0;margin:0;list-style:none}.funnel-stages li{display:flex;flex-direction:column;gap:8px;border-radius:10px;padding:14px;background:#f4f7f7;min-width:0}.funnel-stages li>span{font-size:12px;color:#546b71}.funnel-stages strong{font-size:15px;color:#31565c}.funnel-stages .not_observed strong{color:#94642f}.funnel-stages .observed strong{color:#137d75}.funnel-stages small{font-size:11px;color:#667e83;overflow-wrap:anywhere}.funnel-actions{padding:0;margin:14px 0 0;list-style:none}.funnel-actions li{display:flex;gap:12px;align-items:center;border-top:1px solid #e6eded;padding:12px 0}.funnel-actions li>div{flex:1;min-width:0}.funnel-actions strong{font-size:13px}.funnel-actions p{font-size:12px;color:#60747a;line-height:1.7;margin:5px 0 0}.discovery-funnel details{font-size:12px;color:#60747a;line-height:1.8;margin-top:8px}.discovery-funnel summary{cursor:pointer}.discovery-funnel details small{display:block}.discovery-funnel details section{padding-top:5px;border-bottom:1px solid #edf2f1}@media(max-width:650px){.funnel-stages{grid-template-columns:1fr}.discovery-funnel{padding:16px}.funnel-actions li{align-items:flex-start}}@media(prefers-reduced-motion:no-preference){.discovery-funnel{animation:funnel-reveal .24s cubic-bezier(.2,.8,.2,1)}@keyframes funnel-reveal{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}}
</style>
