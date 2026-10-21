<template>
  <el-card shadow="never" data-testid="source-destinations">
    <div class="row"><h2>来源投放目标</h2><el-button :disabled="busy" @click="configure()">添加其他平台</el-button></div>
    <p class="hint">先看 AI 引用了哪个具体栏目，再核对能否投稿。排序依据是本次有效问题中的来源线索，不能代表收录概率。</p>
    <el-table :data="cycle.opportunities" data-testid="source-opportunities">
      <el-table-column label="观察来源" min-width="190"><template #default="{row}"><strong>{{ row.domain || '网址未确认' }}</strong><p>{{ surfaces[row.surface] || '待调研网站' }}</p><p class="hint">{{ row.case_ids.join('、') }} · {{ row.observed_pairs }} 条去重线索</p></template></el-table-column>
      <el-table-column label="接入判断" min-width="260"><template #default="{row}"><p>{{ row.note }}</p><a v-if="row.examples[0]?.url" :href="row.examples[0].url" target="_blank" rel="noopener noreferrer">核对观察网址</a><p v-for="match in row.matches" :key="match.id">已配置：{{ match.name }}（用户配置的网址范围）</p></template></el-table-column>
      <el-table-column label="下一步" min-width="170"><template #default="{row}">
        <el-button v-if="row.candidate && !row.matches.length" :disabled="busy" @click="configure(row.candidate)">接入{{ row.candidate.name }}</el-button>
        <el-button v-else-if="row.surface === 'unknown' && !row.matches.length" :disabled="busy" @click="configure(unknownCandidate(row))">核对后添加平台</el-button>
        <el-button v-for="match in row.matches" :key="match.id" :disabled="busy" @click="$emit('select', match.key)">为此平台写稿</el-button>
        <span v-if="row.surface === 'vendor_documentation'">先调研合作或其他开放栏目</span><span v-if="row.surface === 'url_unknown'">补采来源网址</span><span v-if="row.surface === 'owned_site'">对接网站 CMS</span>
        <div v-for="lead in row.title_leads" :key="lead.candidate.name" class="hint"><p>标题线索：{{ lead.source_ids.join('、') }}，原网址未核实</p><el-button :disabled="busy" @click="configure(lead.candidate)">调研接入{{ lead.candidate.name }}</el-button></div>
      </template></el-table-column>
    </el-table>
    <p class="hint">注册、实名、验证码及投稿申请需由账号持有人正常完成。首次入口和风格为接入起点，需要按平台现行规则核对。</p>
    <article v-for="target in cycle.destinations" :key="target.id" class="target" data-testid="distribution-target">
      <div class="row"><strong>{{ target.config.name }}</strong><el-tag>{{ states[target.state] || target.state }}</el-tag><el-tag type="info">{{ target.capability === 'platform_assisted_fill' ? '平台辅助填写' : target.capability === 'configured_fill' ? '已配置填写，待实站验证' : '登录与导出' }}</el-tag></div>
      <p class="hint">{{ target.config.editor_url }}<br/>内部阈值：每 24 小时 {{ target.config.daily_limit }} 次填写，至少间隔 {{ target.config.interval_hours }} 小时。该阈值不是平台官方额度，也不保证免封号。</p>
      <p v-if="target.blocked_reason" class="blocked">{{ target.blocked_reason }}</p>
      <DestinationSessionCard :cycle-id="cycle.id" :target="target" :task="task" :busy="busy" :native-available="nativeAvailable" @open="$emit('open', {target_id:target.id})" @checked="$emit('checked')"/>
      <div class="row"><el-button :disabled="busy" @click="$emit('select', target.key)">选择此平台写稿</el-button><el-button :disabled="busy" @click="configure(target.config, target)">编辑接入配置</el-button><el-button @click="events(target)">尝试记录</el-button><el-button v-if="target.blocked_reason || ['preparing','filled'].includes(target.state)" :disabled="busy" @click="review(target)">检查后恢复</el-button></div>
    </article>
    <p v-if="!nativeAvailable" class="hint">保存配置和写稿可在网页中操作；专用平台浏览器需使用桌面软件。</p>
    <el-dialog v-model="show" :title="editing ? '编辑平台接入' : '接入一个平台'" width="760px" :close-on-click-modal="false">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="平台名称"><el-input v-model="form.name" aria-label="接入平台名称" maxlength="80"/></el-form-item>
        <div class="grid"><el-form-item label="平台首页 HTTPS"><el-input v-model="form.home_url" aria-label="平台首页网址"/></el-form-item><el-form-item label="用于匹配来源的具体网址范围"><el-input v-model="form.source_scope" aria-label="来源网址范围" placeholder="https://平台/开放栏目/"/></el-form-item><el-form-item label="正常注册 / 登录入口"><el-input v-model="form.login_url" aria-label="平台登录入口"/></el-form-item><el-form-item label="作者编辑器入口"><el-input v-model="form.editor_url" aria-label="平台编辑器入口"/></el-form-item></div>
        <el-form-item label="发布后公开网址范围，可与编辑器、来源栏目不同"><el-input v-model="form.publication_scope" aria-label="发布后公开网址范围" placeholder="留空沿用来源范围，例如 https://zhuanlan.zhihu.com/"/></el-form-item>
        <el-form-item label="平台风格、受众与内容要求（需核对，AI 不会自行验证平台规则）"><el-input v-model="form.style" type="textarea" :rows="3" aria-label="平台内容规范" maxlength="3000"/></el-form-item>
        <el-form-item label="平台规则原文网址，可留空"><el-input v-model="form.rules_url" aria-label="平台规则网址"/></el-form-item>
        <div class="grid"><el-form-item label="标题字数上限"><el-input-number v-model="form.title_limit" :min="10" :max="180"/></el-form-item><el-form-item label="正文字数上限"><el-input-number v-model="form.body_limit" :min="100" :max="30000"/></el-form-item><el-form-item label="本软件每 24 小时填写上限"><el-input-number v-model="form.daily_limit" :min="1" :max="20"/></el-form-item><el-form-item label="两次填写最小间隔（小时）"><el-input-number v-model="form.interval_hours" :min="1" :max="168"/></el-form-item></div>
        <el-collapse><el-collapse-item title="高级：配置自动填写（当前由实施人员配置 CSS 编辑框）" name="selectors">
          <p v-if="editing?.capability === 'platform_assisted_fill'" class="hint">此平台已有专用辅助填写，编辑框配置可以留空，无需填写 CSS。</p>
          <p class="hint">仅支持唯一、空白、可编辑的普通输入框或 contenteditable。复杂富文本、iframe 和图片上传需专用适配器。填写可能触发网站自动保存；不会自动点发布。</p>
          <el-form-item label="标题编辑框 CSS"><el-input v-model="form.title_selector" aria-label="标题编辑框选择器" placeholder="例如 input[name=title]"/></el-form-item><el-form-item label="正文编辑框 CSS"><el-input v-model="form.body_selector" aria-label="正文编辑框选择器" placeholder="例如 textarea[name=content]"/></el-form-item>
        </el-collapse-item></el-collapse>
      </el-form>
      <template #footer><el-button @click="show=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存平台接入</el-button></template>
    </el-dialog>
    <el-dialog v-model="showEvents" title="平台尝试记录" width="740px"><p class="hint">这些是登录与填写记录，不能作为已发布证明。</p><el-table :data="eventRows"><el-table-column prop="created_at" label="时间" min-width="160"/><el-table-column prop="kind" label="操作" width="100"/><el-table-column label="状态" width="100"><template #default="{row}">{{ states[row.state] || row.state }}</template></el-table-column><el-table-column prop="message" label="记录" min-width="260"/></el-table></el-dialog>
  </el-card>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { geoContentApi } from '@/api/geoContent'
import DestinationSessionCard from '@/components/DestinationSessionCard.vue'
const props = defineProps({cycle:{type:Object,required:true},task:{type:Object,default:()=>({})},busy:Boolean,nativeAvailable:Boolean})
const emit = defineEmits(['updated','select','open','checked'])
const show=ref(false), saving=ref(false), editing=ref(null), showEvents=ref(false), eventRows=ref([])
const defaults = () => ({name:'',home_url:'',source_scope:'',publication_scope:'',login_url:'',editor_url:'',style:'先回答读者的问题，再说明依据、适用条件和局限。不要复制来源文章或编造体验；按平台现行规范核对商业内容。',rules_url:'',channel:'',title_limit:100,body_limit:15000,daily_limit:1,interval_hours:6,title_selector:'',body_selector:''})
const form = reactive(defaults())
const surfaces = {community_candidate:'开放内容平台候选，权限待核对',vendor_documentation:'厂商文档 / 自有资料',url_unknown:'仅有标题',owned_site:'客户官网',unknown:'未知网站，需调研'}
const states = {configured:'已配置',session_saved:'会话已保存，权限未验证',login_open:'等待平台操作',editor_ready:'编辑器已检查',preparing:'尝试未结束',filled:'已填写，发布未确认',need_action:'待检查',reviewed:'已人工检查'}
const configure = (config, target) => { editing.value=target || null; Object.assign(form,defaults(),config || {}); if (!form.publication_scope && form.channel === 'zhihu') form.publication_scope='https://zhuanlan.zhihu.com/'; show.value=true }
const unknownCandidate = row => {const home=`https://${row.domain}/`;return {name:row.domain,home_url:home,source_scope:home,login_url:home,editor_url:home}}
const save = async () => {
  saving.value=true
  try {const result=(await geoContentApi.saveDestination(props.cycle.id,editing.value?.id,{config:{...form},revision:editing.value?.revision})).data;emit('updated',result.cycle);show.value=false;ElMessage.success('平台已保存，登录与发布权限仍需核对')}
  finally {saving.value=false}
}
const events = async target => {eventRows.value=(await geoContentApi.destination(props.cycle.id,target.id)).data.target.events;showEvents.value=true}
const review = async target => {
  try {await ElMessageBox.confirm('请先检查该平台是否已有自动保存草稿或发布记录，并核对账号限制。恢复后仍保留原来的频率与尝试记录。','确认已检查平台状态',{confirmButtonText:'已检查，恢复',cancelButtonText:'取消'});await geoContentApi.reviewDestination(props.cycle.id,target.id);emit('updated',(await geoContentApi.get(props.cycle.id)).data)}
  catch(error) {if(error instanceof Error && !error.response) ElMessage.error(error.message)}
}
</script>

<style scoped>
h2{font-size:20px;margin:0}.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.hint{color:#59696e;line-height:1.8}.target{background:#f4f8f7;padding:14px;margin-top:14px;border-radius:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:0 20px}.blocked{color:#aa4a1c}a{color:#0a756e}@media(max-width:650px){.grid{grid-template-columns:1fr}}
</style>
