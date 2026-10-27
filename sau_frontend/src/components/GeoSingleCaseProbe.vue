<template>
  <section class="single-probe" aria-label="同题单题复测与对照" data-testid="single-case-probes">
    <header><div><h3>只看这一题的变化</h3><p>单题复测会发送一次独立新请求，其他问题不发送，原记录保留。</p></div></header>
    <div class="probe-controls"><el-select v-model="selectedCaseId" aria-label="单题复测原问题" placeholder="选择要复测的原问题" :disabled="busy || !canStart" data-testid="journey-probe-case"><el-option v-for="item in cases" :key="item.id" :value="item.id" :label="`${item.id} · ${category(item.category)} · ${excerpt(item.question, 48)}`"/></el-select><el-button type="primary" :disabled="!canRequest" data-testid="journey-single-case-probe" @click="requestProbe">只复测这一题</el-button></div>
    <p v-if="selectedCase" class="probe-question">{{ selectedCase.question }}</p>
    <p v-if="!canStart" class="probe-hint">单题复测暂不可用，已保存的对照记录仍可查看。</p>
    <p v-else-if="!cases.length" class="probe-hint">当前没有可复测的原问题，请先保存搜索基线。</p>
    <p class="probe-hint">名称或官网文字出现，不代表业务描述正确、推荐、索引，或某篇文章带来了效果。</p>
    <p v-if="!observations.length" class="probe-hint">尚无单题对照记录。取得最终回答后，可在这里查看原文与来源。</p>
    <article v-for="item in observations" :key="item.id" class="probe-record" data-testid="single-probe-record">
      <div class="probe-heading"><strong>{{ isExternal(item) ? `关联 ${item.case_id} · 外部反馈` : `${item.case_id} · ${category(item.category)}` }}</strong><el-tag size="small" effect="plain">{{ origin(item) }}</el-tag><span>{{ isExternal(item) ? `反馈登记：${date(item.created_at)}` : date(item.collected_at || item.created_at) }}</span></div>
      <template v-if="isExternal(item)"><p v-if="item.observed_question" class="probe-record-question">实际提问：{{ item.observed_question }}</p><p v-else class="probe-hint">实际提问未提供，不能判定为严格同题对照。</p><details><summary>查看本轮关联问题</summary><p class="probe-record-question">{{ item.question }}</p></details></template>
      <p v-else class="probe-record-question">{{ item.question }}</p>
      <p class="probe-hint">{{ questionScope(item) }} · {{ isExternal(item) ? '用户提供的外部观察，采集条件未由软件核验' : '仅这一题的独立观察' }}</p>
      <div v-if="contextAttestation(item)" class="probe-hint" data-testid="probe-context-attestation"><strong>用户确认：{{ contextAttestation(item).new_conversation ? '新建对话' : '已有对话' }} · {{ contextAttestation(item).first_message ? '首条提问' : '非首条提问' }}</strong><span v-if="contextAttestation(item).recorded_at"> · {{ date(contextAttestation(item).recorded_at) }}</span><p>这是用户对提问条件的声明，非软件采集核验；回答完整性、界面和模型仍按原记录展示。</p></div>
      <div class="probe-change"><span>回答出现官网地址</span><strong>{{ isExternal(item) ? presence(item.after,item) : `${presence(item.before)} → ${presence(item.after)}` }}</strong><small>只检查所提供文字中的官网域名，不等于核实引用链接。</small></div>
      <p class="probe-business-stage" :class="{'not-acknowledged':businessReview(item)?.status==='not_acknowledged'}" data-testid="probe-business-stage">{{ businessStage(item) }}</p><p class="probe-hint">交付能力与案例：未知，仍需独立核验。</p>
      <details v-if="businessReview(item)"><summary>查看业务资料核对依据</summary><blockquote>{{ businessReview(item).quote }}</blockquote><p class="probe-hint">{{ reviewer(businessReview(item)) }}对照了 {{ businessReview(item).reference_fact_ids.length }} 条已确认资料；这是对原文的核对。</p></details>
      <div class="probe-columns" :class="{'external-only':isExternal(item)}">
        <section v-for="side in sides(item)" :key="side.key" class="probe-answer" :aria-label="side.label">
          <div class="answer-heading"><h4>{{ side.label }}</h4><span :class="{unavailable:isExternal(item)||side.value?.answer_evaluable!==true}">{{ isExternal(item) ? '外部原文，采集条件未核验' : side.value?.answer_evaluable===true ? '已取得有效答案' : '未取得有效答案' }}</span></div>
          <p class="probe-hint">{{ date(side.value?.collected_at) }} · {{ mode(side.value?.client_mode) }} · {{ side.value?.model_label || '模型未知' }}</p>
          <p v-if="side.value?.answer_evaluable===false || !isExternal(item)&&side.value?.answer_evaluable!==true" class="probe-unavailable">{{ unavailable(side.value) }}</p><p v-else-if="isExternal(item)&&side.value?.answer_evaluable!==true" class="probe-hint">是否为完整最终回答尚未声明；文字观察不进入软件采集的有效题统计。</p>
          <p v-if="side.value?.response" class="probe-excerpt">{{ excerpt(side.value.response) }}</p>
          <p v-else class="probe-hint">尚无可展示的回答原文。</p>
          <details v-if="side.value?.response && side.value.response.length>280"><summary>查看完整回答</summary><p class="probe-full-answer">{{ side.value.response }}</p></details>
          <div class="probe-source-state"><span>官网来源：{{ observed(side.value,'official_source_present') }}</span><span>官网引用：{{ observed(side.value,'official_citation_present') }}</span></div>
          <ul v-if="sourceRows(side.value).length" class="probe-sources"><li v-for="(source,index) in sourceRows(side.value)" :key="`${source.kind}-${index}`"><span>{{ source.kind }} · </span><a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }}</a><span v-else>{{ source.title }} · 网址未知或不可公开核验</span></li></ul>
          <p v-else class="probe-hint">来源明细未提供，不能据此判断没有来源。</p>
        </section>
      </div>
      <p class="probe-hint probe-conditions">{{ conditions(item) }}</p>
      <details v-if="Array.isArray(item.limitations) && item.limitations.length"><summary>查看这次观察的限制</summary><p v-for="(note,index) in item.limitations" :key="index" class="probe-hint">{{ note }}</p></details>
    </article>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { journeyDate } from '@/utils/geoJourney'
import { publicationReviewUrl } from '@/utils/geoPublicationReview'

const props=defineProps({cycle:{type:Object,required:true},busy:Boolean,canStart:Boolean})
const emit=defineEmits(['probe'])
const selectedCaseId=ref('')
const identifier=value=>typeof value==='string'&&/^[\w-]{1,100}$/.test(value)
const cases=computed(()=>{
  const rows=Array.isArray(props.cycle?.baseline?.cases)?props.cycle.baseline.cases:[]
  return rows.filter(item=>identifier(item?.id)&&typeof item.question==='string'&&item.question.trim()
    &&rows.filter(other=>other?.id===item.id).length===1)
})
const selectedCase=computed(()=>cases.value.find(item=>item.id===selectedCaseId.value)||null)
const canRequest=computed(()=>props.canStart===true&&!props.busy&&Boolean(selectedCase.value))
const requestProbe=()=>{if(canRequest.value)emit('probe',{cycleId:props.cycle.id,caseId:selectedCase.value.id})}
const observations=computed(()=>(Array.isArray(props.cycle?.probes)?props.cycle.probes:[])
  .filter(item=>identifier(item?.id)&&item.cycle_id===props.cycle.id&&item.selected_count===1
    &&typeof item.question==='string'&&identifier(item.case_id)))
const category=value=>({discovery:'自然选型',brand_identity:'品牌核验',website_guided:'官网核验'}[value]||'题型待确认')
const excerpt=(value,limit=280)=>{const text=typeof value==='string'?value:'';return text.length>limit?`${text.slice(0,limit)}…`:text}
const date=value=>journeyDate(value)
const mode=value=>value==='work'?'豆包工作':value==='consumer'?'豆包对话':'界面未知'
const isExternal=item=>item?.source==='user_reported_external'
const contextAttestation=item=>{
  const history=item?.context_attestations
  if(!isExternal(item)||!Array.isArray(history)||!history.length)return null
  const value=history.at(-1),hash=item.response_sha256||item.after?.response_sha256
  if(!value||typeof value!=='object'||Array.isArray(value)||value.basis!=='user_statement'
    ||value.native_verified!==false||value.independent_verification!==false
    ||typeof value.new_conversation!=='boolean'||typeof value.first_message!=='boolean'
    ||typeof hash!=='string'||!/^[a-f0-9]{64}$/.test(hash)||value.response_sha256!==hash
    ||item.response_sha256&&item.after?.response_sha256&&item.response_sha256!==item.after.response_sha256
    ||typeof value.question!=='string'||!value.question.trim()||value.question!==item.observed_question)return null
  return value
}
const origin=item=>isExternal(item)?'用户外部反馈':item.source==='research_prototype_import'?'研究结果导入':item.source==='native_collector'&&item.native_verified===true?'软件采集':'采集来源待确认'
const sides=item=>isExternal(item)?[{key:'after',label:'用户提供原文',value:item.after}]:[{key:'before',label:'原基线回答',value:item.before},{key:'after',label:'本次单题回答',value:item.after}]
const valueFor=(side,key,item)=>((isExternal(item)&&side===item.after&&key==='website_presence')||side?.answer_evaluable===true)&&typeof side?.[key]==='boolean'?side[key]:null
const presence=(side,item)=>valueFor(side,'website_presence',item)===true?'出现':valueFor(side,'website_presence',item)===false?'未出现':'未知'
const observed=(side,key)=>valueFor(side,key)===true?'已观察到':valueFor(side,key)===false?'未观察到':'未知'
const unavailable=side=>({service_capacity_unavailable:'本次办公能力不可用，未取得有效答案。',background_task_not_final:'仍是等待调研的提示，尚未取得最终答案。',private_account_context:'回答声称使用账号资料或历史对话记录，已排除公开统计；原文保留，请用空白新对话另测。',human_verification_required:'验证尚未完成，未取得有效答案。',login_required:'登录尚未完成，未取得有效答案。'}[side?.answer_error]||'尚未确认完整有效回答，不能按“未搜到”统计。')
const questionScope=item=>{
  if(isExternal(item))return !item.observed_question?'实际提问的网址条件未知':item.website_in_question===true?'实际提问提供了官网地址':item.website_in_question===false&&item.website_test_without_url===true?'实际提问未提供官网地址':'实际提问的网址条件未知'
  return item.website_in_question===true||item.category==='website_guided'?'原题提供了官网地址':item.website_in_question===false&&item.website_test_without_url===true?'原题未提供官网地址':'题目是否带官网地址待确认'
}
const conditions=item=>{
  const value={...item.conditions};if(isExternal(item)&&!item.observed_question)value.same_question=null
  if(isExternal(item)&&(!item.before?.client_mode||!item.after?.client_mode))value.client_mode_match=null
  if(isExternal(item)&&(!item.before?.model_label||!item.after?.model_label))value.model_label_match=null
  const label=(field,yes,no,unknown)=>value[field]===true?yes:value[field]===false?no:unknown
  return [label('same_question','沿用同一原题','问题发生变化，不作同题对照','问题一致性未知'),label('client_mode_match','界面一致','界面不同','界面是否一致未知'),label('model_label_match','模型标签一致','模型标签不同','模型是否一致未知')].join(' · ')
}
const reviewer=value=>value?.by==='assistant'?'助手核对':'人工核对'
const businessReview=item=>{
  const value=item?.business_relationship_review, response=item?.after?.response
  if(!value||!['not_acknowledged','acknowledged'].includes(value.status)||!['assistant','operator'].includes(value.by)
    ||item?.after?.answer_evaluable===false||item?.after?.answer_error||!isExternal(item)&&item?.after?.answer_evaluable!==true||typeof response!=='string'||typeof value.quote!=='string'||!value.quote.trim()||!response.includes(value.quote)
    ||!Array.isArray(value.reference_fact_ids)||!value.reference_fact_ids.length||new Set(value.reference_fact_ids).size!==value.reference_fact_ids.length
    ||!value.reference_fact_ids.every(id=>(props.cycle?.facts||[]).some(fact=>fact.id===id&&fact.confirmed===true)))return null
  return value
}
const businessStage=item=>{
  const value=businessReview(item)
  if(!value)return '公开业务资料识别情况待核对。'
  if(value.status==='not_acknowledged')return `${valueFor(item.after,'website_presence',item)===true?'官网地址出现，但':''}公开业务资料未被识别（${reviewer(value)}）`
  return `答复原文已提及所核对的公开业务关系（${reviewer(value)}），不代表整体优化已达标。`
}
const sourceRows=side=>[
  ...(Array.isArray(side?.citations)?side.citations:[]).map(item=>({item,kind:'引用'})),
  ...(Array.isArray(side?.displayed_sources)?side.displayed_sources:[]).map(item=>({item,kind:'来源面板'}))
].filter(row=>row.item&&typeof row.item==='object').slice(0,6).map(({item,kind})=>({kind,
  title:typeof item.title==='string'&&item.title.trim()?item.title.slice(0,240):'来源条目',url:publicationReviewUrl(item.url)||null}))
watch(()=>props.cycle?.id,()=>{selectedCaseId.value=''})
</script>

<style scoped>
.probe-business-stage{padding:10px 13px;border-left:3px solid var(--geo-border,#dce6e2);background:#f3f7f5;font-size:13px;line-height:1.8}.probe-business-stage.not-acknowledged{border-color:#b78a46;background:#fff7ec;color:#825c2b}.probe-columns.external-only{grid-template-columns:1fr}.single-probe blockquote{margin:10px 0;padding:10px 14px;border-left:2px solid var(--geo-border,#dce6e2);white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
.single-probe{margin:24px 0;padding:22px 24px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:#fff;font-size:13px;line-height:1.8}.single-probe h3{font-size:16px;color:var(--geo-ink,#173e3b)}.single-probe header p,.probe-hint{margin:7px 0;color:var(--geo-muted,#63736f);font-size:12px}.probe-controls{display:flex;gap:12px;margin:18px 0 12px}.probe-controls .el-select{flex:1;min-width:0}.probe-question,.probe-record-question{white-space:pre-wrap;overflow-wrap:anywhere}.probe-question{padding:12px 14px;background:#f4f8f6;border-radius:8px}.probe-record{margin-top:24px;padding-top:20px;border-top:1px solid var(--geo-border,#dce6e2)}.probe-heading{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.probe-heading>span:last-child{font-size:11px;color:var(--geo-muted,#63736f)}.probe-record-question{margin-top:12px}.probe-change{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:12px 0}.probe-change strong{font-size:17px;color:var(--geo-primary,#0a756e)}.probe-change small{font-size:11px;color:var(--geo-muted,#63736f)}.probe-columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}.probe-answer{min-width:0;padding:16px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:#fafcfc}.answer-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.answer-heading h4{font-size:13px}.answer-heading>span{font-size:11px;color:var(--geo-primary,#0a756e)}.answer-heading .unavailable{color:#936632}.probe-excerpt,.probe-full-answer{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}.probe-excerpt{margin:12px 0}.probe-full-answer{margin-top:12px}.single-probe details{margin:10px 0}.single-probe summary{width:fit-content;cursor:pointer;color:var(--geo-primary,#0a756e);font-size:12px}.single-probe summary:focus-visible{outline:2px solid var(--geo-primary,#0a756e);outline-offset:3px}.probe-unavailable{padding:9px 12px;background:#fff7ec;color:#825c2b;border-radius:7px;font-size:12px}.probe-source-state{display:flex;gap:5px 16px;flex-wrap:wrap;margin-top:14px;font-size:11px;color:var(--geo-muted,#63736f)}.probe-sources{margin:10px 0 0;padding-left:17px;font-size:12px;overflow-wrap:anywhere}.probe-sources a{color:var(--geo-primary,#0a756e)}.probe-conditions{padding-top:7px}@media(max-width:800px){.single-probe{padding:18px 16px}.probe-columns{grid-template-columns:1fr}.probe-controls{flex-direction:column;align-items:stretch}}
</style>
