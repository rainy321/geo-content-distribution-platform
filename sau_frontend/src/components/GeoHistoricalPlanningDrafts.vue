<template>
  <details v-if="items.length" class="historical-drafts" data-testid="historical-planning-drafts">
    <summary>历史选题稿件（{{ items.length }}）</summary>
    <p>以下稿件保留原选题与原生成依据，可阅读、编辑已有稿件或导出；不会绑定到本次新选题，也不会自动发布。</p>
    <article v-for="item in items" :key="item.draft.article_id" :data-article-id="item.draft.article_id">
      <h3>{{ item.draft.title }}</h3>
      <p>原选题 {{ item.brief.id }} · {{ item.brief.case_ids.join('、') }} · {{ item.brief.topic }}</p>
      <p>{{ item.draft.summary }}</p>
      <div class="history-actions"><el-button :disabled="busy || Boolean(directoryTargetForChannel(cycle,item.draft.platform))" @click="$emit('edit',item.draft)">编辑已有稿件</el-button><el-button :disabled="busy" @click="$emit('export',item.draft)">导出稿件</el-button></div>
      <details><summary>阅读原稿</summary><pre>{{ item.draft.content }}</pre></details>
    </article>
  </details>
</template>

<script setup>
import {computed} from 'vue'
import {historicalPlanningDrafts} from '@/utils/geoPlanningScope'
import {directoryTargetForChannel} from '@/api/geoDirectoryApplications'
const props=defineProps({cycle:{type:Object,required:true},busy:Boolean})
defineEmits(['edit','export'])
const items=computed(()=>historicalPlanningDrafts(props.cycle))
</script>

<style scoped>
.historical-drafts{margin:16px 0;padding:16px;border:1px solid #d9dfdf;border-radius:10px;font-size:13px;line-height:1.7}.historical-drafts>summary{font-size:15px;font-weight:600;cursor:pointer}.historical-drafts article{padding:12px 0;border-top:1px solid #dde4e3}.historical-drafts h3{margin:0 0 8px;font-size:15px}.history-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.historical-drafts pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;max-height:420px;overflow:auto}
</style>
