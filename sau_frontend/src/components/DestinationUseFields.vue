<template>
  <div class="use-fields" data-testid="destination-use-fields">
    <el-form-item label="这次如何使用该平台">
      <el-radio-group :model-value="values.publication_use" :disabled="disabled" aria-label="平台用途" @update:model-value="changeUse">
        <el-radio v-for="item in publicationUses" :key="item.value" :value="item.value">{{ item.label }}</el-radio>
      </el-radio-group>
    </el-form-item>
    <Transition name="use-reveal" mode="out-in">
      <p v-if="values.publication_use==='research_only'" key="research" class="use-hint">保留来源线索，不启动该平台的写稿、登录或填稿。已有稿件仍可编辑和导出。</p>
      <div v-else key="publication">
        <p v-if="policy?.note && !restriction" class="use-hint">{{ policy.note }} <a v-if="safeResearchLink(policy.rules_url)" :href="safeResearchLink(policy.rules_url)" target="_blank" rel="noopener noreferrer">查看平台规则</a></p>
        <div v-if="restriction" class="use-restriction" role="status"><p>{{ restriction }} <a v-if="safeResearchLink(policy?.rules_url)" :href="safeResearchLink(policy.rules_url)" target="_blank" rel="noopener noreferrer">查看平台规则</a></p><el-button :disabled="disabled" text @click="changeUse('research_only')">仅保留来源参考</el-button></div>
        <template v-else>
          <el-form-item label="平台规则网址（可选）"><el-input :model-value="values.rules_url" :disabled="disabled" aria-label="平台规则网址" placeholder="填写实际核对的规则页面" @update:model-value="changeRules"/></el-form-item>
          <el-checkbox :model-value="values.rule_review.state==='suitable' && values.rule_review.operator_confirmed" :disabled="disabled" @update:model-value="confirm">我已核对该栏目的规则，本次用途符合要求</el-checkbox>
          <p class="use-hint">这是你对用途的核对记录；账号权限、AI 内容声明和文章审核仍以平台为准。</p>
          <el-form-item label="需遵守的条件（可选）"><el-input :model-value="values.rule_review.note" :disabled="disabled" type="textarea" :rows="2" maxlength="400" show-word-limit placeholder="例如允许哪些内容、哪些内容不应投递" @update:model-value="changeNote"/></el-form-item>
        </template>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { destinationUseValues, publicationUses, publicationUseRestriction, safeResearchLink } from '@/utils/geoDestinationUse'
const props=defineProps({modelValue:{type:Object,required:true},policy:{type:Object,default:null},disabled:Boolean})
const emit=defineEmits(['update:modelValue'])
const values=computed(()=>destinationUseValues(props.modelValue))
const restriction=computed(()=>publicationUseRestriction(props.policy,values.value.publication_use))
const update=changes=>emit('update:modelValue',{...props.modelValue,...changes})
const changeUse=use=>{if(props.disabled || use===values.value.publication_use)return;update({publication_use:use,rule_review:{...values.value.rule_review,state:'unknown',operator_confirmed:false}})}
const changeRules=url=>{if(props.disabled || url===values.value.rules_url)return;update({rules_url:url,rule_review:{...values.value.rule_review,state:'unknown',operator_confirmed:false}})}
const confirm=value=>{if(!props.disabled && !restriction.value)update({rule_review:{...values.value.rule_review,state:value?'suitable':'unknown',operator_confirmed:value===true}})}
const changeNote=note=>{if(!props.disabled)update({rule_review:{...values.value.rule_review,note}})}
</script>

<style scoped>
.use-fields{font-size:13px;line-height:1.7}.use-hint{margin:8px 0 12px;color:var(--geo-muted,#63736f);font-size:12px}.use-restriction{padding:10px 14px;background:#fff6ec;border-radius:8px;color:#91551d}.use-restriction p{margin:0}.el-radio-group{gap:0 14px}.el-radio{margin:0;white-space:normal}.el-checkbox{height:auto;min-height:32px;max-width:100%}.el-checkbox:deep(.el-checkbox__label){white-space:normal;line-height:1.7}a{color:var(--geo-primary,#0a756e)}.use-reveal-enter-active,.use-reveal-leave-active{transition:opacity 160ms cubic-bezier(.2,.8,.2,1),transform 160ms cubic-bezier(.2,.8,.2,1)}.use-reveal-enter-from,.use-reveal-leave-to{opacity:0;transform:translateY(4px)}@media(prefers-reduced-motion:reduce){.use-reveal-enter-active,.use-reveal-leave-active{transition:none}}
</style>
