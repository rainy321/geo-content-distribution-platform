<template>
  <aside class="saved-cycles" aria-label="继续已有内容" data-testid="journey-saved-cycles">
    <div class="saved-heading"><h3>继续已有内容</h3><el-button text :disabled="busy || state.loading || Boolean(state.selecting)" @click="refresh">刷新</el-button></div>
    <p class="saved-identity">{{ entityLabel(project) }} · {{ project.name }}<span>{{ project.website || '网址待核对' }}</span></p>
    <p v-if="state.loading" class="saved-hint" role="status" aria-live="polite">正在读取这个对象已保存的内容…</p>
    <p v-else-if="state.error" class="saved-error" role="alert">{{ state.error }}</p>
    <p v-else-if="!state.items.length" class="saved-hint">这个对象还没有已保存的内容。可继续下面的搜索测试。</p>
    <template v-if="state.items.length">
      <el-select v-model="selectedId" aria-label="选择已保存的内容周期" placeholder="选择一轮，查看基线报告与稿件数量" :disabled="busy || state.loading || Boolean(state.selecting)">
        <el-option v-for="item in state.items" :key="item.id" :value="item.id" :label="savedCycleLabel(item)">{{ savedCycleLabel(item) }}{{ item.id===currentCycleId ? ' · 当前内容' : '' }}</el-option>
      </el-select>
      <div class="saved-actions"><p class="saved-hint">继续这一轮的基线、分析和稿件，不会另建内容。</p><el-button type="primary" plain :disabled="busy || state.loading || Boolean(state.selecting) || !selectedId" :loading="Boolean(state.selecting)" @click="reader.select(selectedId)">继续这轮内容</el-button></div>
    </template>
  </aside>
</template>

<script setup>
import { onUnmounted, reactive, ref, watch } from 'vue'
import { geoContentApi } from '@/api/geoContent'
import { entityLabel } from '@/utils/geoJourney'
import { createSavedCyclesReader, savedCycleLabel } from '@/utils/geoSavedCycles'

const props=defineProps({project:{type:Object,required:true},busy:Boolean,currentCycleId:String})
const emit=defineEmits(['selected'])
const selectedId=ref(''),state=reactive({items:[],loading:false,selecting:'',error:''})
const reader=createSavedCyclesReader({api:geoContentApi,getProject:()=>props.project,isBusy:()=>props.busy,
  onState:value=>Object.assign(state,value),onSelected:value=>emit('selected',value)})
const refresh=()=>{if(props.busy || state.selecting)return;selectedId.value='';return reader.refresh()}
watch(()=>[props.project.id,props.project.name,props.project.website,props.project.entity_type],()=>{
  selectedId.value='';reader.changeProject(props.project)
},{immediate:true,flush:'sync'})
watch(()=>props.busy,value=>{if(value)reader.invalidateSelection()},{flush:'sync'})
watch(()=>props.currentCycleId,()=>reader.invalidateSelection(),{flush:'sync'})
onUnmounted(()=>reader.dispose())
</script>

<style scoped>
.saved-cycles{margin:0 0 20px;padding:16px 18px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:var(--geo-surface,#fff)}.saved-heading,.saved-actions{display:flex;align-items:center;justify-content:space-between;gap:12px}.saved-heading h3{margin:0;font-size:15px;color:var(--geo-ink,#173e3b)}.saved-identity{margin:5px 0 12px;color:var(--geo-ink,#173e3b);font-size:13px;overflow-wrap:anywhere}.saved-identity span{display:block;margin-top:3px;color:var(--geo-muted,#63736f);font-size:12px}.saved-hint,.saved-error{margin:9px 0;font-size:12px;line-height:1.7}.saved-hint{color:var(--geo-muted,#63736f)}.saved-error{color:#a6412d}.saved-cycles .el-select{width:100%}.saved-actions{margin-top:8px}@media(max-width:600px){.saved-cycles{padding:14px}.saved-actions{align-items:flex-start;flex-direction:column}.saved-actions .el-button{align-self:flex-end}}
</style>
