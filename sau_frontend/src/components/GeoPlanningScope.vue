<template>
  <section class="planning-scope" aria-label="内容策划范围" data-testid="planning-scope">
    <label>本次策划范围
      <select :value="selectedCaseId" :disabled="busy" aria-label="本次策划范围" @change="$emit('update:selectedCaseId',$event.target.value)">
        <option value="">全部原题</option>
        <option v-for="item in view.options" :key="item.id" :value="item.id" :title="item.question" :disabled="!item.usable">{{ item.id }} · {{ optionQuestion(item.question) }}{{ item.usable ? '' : '（回答不可用）' }}</option>
      </select>
    </label>
    <p v-if="view.selectedQuestion" class="scope-question">原题：{{ view.selectedQuestion.question }}</p>
    <p :role="view.allowed ? 'status' : 'alert'">{{ view.allowed ? view.message : view.blockedReason }}</p>
    <p v-if="notice" role="status" data-testid="planning-latest-scope">{{ notice }}</p>
    <el-button v-if="actionLabel" :disabled="busy || !view.allowed" data-testid="planning-scope-generate" @click="$emit('generate')">{{ actionLabel }}</el-button>
  </section>
</template>

<script setup>
import {computed} from 'vue'
import {planningScopeView,latestPlanningScopeNotice} from '@/utils/geoPlanningScope'
const props=defineProps({cycle:{type:Object,required:true},selectedCaseId:{type:String,default:''},busy:Boolean,actionLabel:{type:String,default:''}})
defineEmits(['update:selectedCaseId','generate'])
const view=computed(()=>planningScopeView(props.cycle,props.selectedCaseId))
const notice=computed(()=>latestPlanningScopeNotice(props.cycle))
const optionQuestion=text=>{const characters=Array.from(text);return characters.length>26?characters.slice(0,26).join('')+'…':text}
</script>

<style scoped>
.planning-scope{min-width:0;width:100%;max-width:100%;box-sizing:border-box;margin:16px 0;padding:16px;border:1px solid #cddedb;border-radius:10px;background:#f6faf9;font-size:13px;line-height:1.7}.planning-scope label{display:grid;grid-template-columns:minmax(0,1fr);min-width:0;width:100%;max-width:100%;box-sizing:border-box;gap:8px;font-weight:600}.planning-scope select{min-width:0;width:100%;max-width:100%;box-sizing:border-box;min-height:36px;padding:8px;border:1px solid #bccdca;border-radius:6px;background:white;color:inherit;font:inherit}.planning-scope p{min-width:0;margin:8px 0;overflow-wrap:anywhere}.scope-question{white-space:pre-wrap;font-weight:500}.planning-scope [role=alert]{color:#8d4520}
</style>
