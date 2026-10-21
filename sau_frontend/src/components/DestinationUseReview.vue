<template>
  <section class="use-review" aria-label="平台用途核对" data-testid="destination-use-review">
    <div class="use-heading"><strong>{{ gate.allowed ? destinationUseLabel(target.config) : '先确认平台用途' }}</strong><el-tag size="small" :type="gate.allowed?'info':'warning'">{{ gate.allowed?'用途已核对':'仅保留研究与记录' }}</el-tag><el-button text :disabled="busy || saving" @click="open">{{ gate.allowed?'修改用途':'核对用途与规则' }}</el-button></div>
    <p v-if="!gate.allowed" role="status">{{ gate.reason }}</p>
    <el-dialog v-model="visible" title="先判断用途，再准备账号" width="min(600px,92vw)" :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving">
      <p class="dialog-intro">{{ target.config.name }} · AI 引用这个来源，不代表它允许商业投放。先确定这次用途，再决定是否需要登录。</p>
      <el-form label-position="top"><DestinationUseFields v-model="form" :policy="target.publication_policy" :disabled="busy || saving || scopeChanged"/></el-form>
      <p v-if="scopeChanged" class="use-error" role="alert">当前流程或平台配置已变化，请关闭后重新核对。</p>
      <p v-if="saveError" class="use-error" role="alert">{{ saveError }}</p>
      <template #footer><el-button :disabled="saving" @click="visible=false">关闭</el-button><el-button type="primary" :loading="saving" :disabled="busy || scopeChanged" @click="save">保存用途</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { geoContentApi } from '@/api/geoContent'
import DestinationUseFields from '@/components/DestinationUseFields.vue'
import { destinationPublicationGate, destinationUseLabel, destinationUseValues, destinationUseRequest, destinationUseScope, sameDestinationUseScope } from '@/utils/geoDestinationUse'
const props=defineProps({cycle:{type:Object,required:true},target:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['updated'])
const visible=ref(false),saving=ref(false),form=ref({}),snapshot=ref(null),saveError=ref('')
let epoch=0
const gate=computed(()=>destinationPublicationGate(props.target))
const scopeChanged=computed(()=>!sameDestinationUseScope(snapshot.value,props.cycle,props.target))
const open=()=>{if(props.busy || saving.value)return;snapshot.value=destinationUseScope(props.cycle,props.target);form.value=destinationUseValues(props.target.config);saveError.value='';visible.value=true}
const save=async()=>{
  if(props.busy || saving.value || scopeChanged.value)return
  const scope=snapshot.value,request=++epoch,payload=destinationUseRequest(props.target,form.value)
  saving.value=true;saveError.value=''
  try {
    const result=(await geoContentApi.saveDestination(scope.cycleId,scope.targetId,payload)).data
    if(request!==epoch || !sameDestinationUseScope(scope,props.cycle,props.target))return
    if(result?.cycle?.id!==scope.cycleId || result?.target?.id!==scope.targetId)throw new Error('保存结果与当前平台不一致，请刷新核对。')
    emit('updated',result.cycle);visible.value=false
  } catch(error){if(request===epoch)saveError.value=error?.response?.data?.msg || error.message || '尚未确认保存结果，请刷新后核对；不会自动重试。'}
  finally{if(request===epoch)saving.value=false}
}
watch(()=>`${props.cycle.id}:${props.target.id}`,()=>{epoch++;visible.value=false;saving.value=false;snapshot.value=null;saveError.value=''},{flush:'sync'})
</script>

<style scoped>
.use-review{padding:12px 16px;margin:12px 0;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:var(--geo-surface,#fff);font-size:13px;line-height:1.7}.use-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.use-heading strong{color:var(--geo-ink,#173e3b);font-weight:500}.use-heading .el-button{margin-left:auto}.use-review>p,.dialog-intro{margin:8px 0;color:var(--geo-muted,#63736f);font-size:12px}.use-error{color:#a6412d;font-size:13px}
</style>
