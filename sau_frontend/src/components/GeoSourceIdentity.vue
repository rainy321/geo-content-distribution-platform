<template>
  <span class="source-identity" :class="`source-identity-${view.classification}`" :title="view.explanation" data-testid="source-identity">
    <span class="source-identity-label">{{ view.label }}</span><span v-if="view.matchedName" class="source-identity-name"> · {{ view.matchedName }}</span>
    <small v-if="!compact">{{ view.explanation }}</small>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { sourceIdentityView } from '@/utils/geoSourceIdentity'

const props = defineProps({ identity: { type: Object, default: null }, compact: Boolean })
const view = computed(() => sourceIdentityView(props.identity))
</script>

<style scoped>
.source-identity{display:block;margin:5px 0;color:var(--geo-muted,#63736f);font-size:12px;line-height:1.8;overflow-wrap:anywhere}.source-identity-label{display:inline-block;padding:1px 7px;border:1px solid var(--geo-border,#dce6e2);border-radius:5px;background:#f6f9f8}.source-identity-target .source-identity-label{background:#eff7f3;color:var(--geo-primary,#0a756e)}.source-identity-other .source-identity-label{background:#fff7ec;color:#825c2b}.source-identity small{display:block;font-size:11px}
</style>
