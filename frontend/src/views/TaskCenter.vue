<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>任务中心</h2><p class="muted">解析、索引、上下文与评价统一追踪。重试新建任务并保留原始记录。</p></div><el-button :loading="loading" @click="refresh(true)">刷新</el-button></div>
    <el-alert v-if="paused" title="自动刷新已暂停，请检查连接后点击刷新恢复。" type="warning" :closable="false" />
    <el-card class="section">
      <div class="actions"><el-select v-model="status" clearable placeholder="全部任务状态" @change="changeFilter"><el-option v-for="value in ['queued','running','succeeded','partial','failed','cancelled','budget_exhausted']" :key="value" :value="value" :label="statusName(value)" /></el-select><span class="muted">时间均为北京时间</span></div>
      <el-table :data="jobs" v-loading="loading" class="section" empty-text="没有符合条件的任务" @row-click="showJob">
        <el-table-column label="类型与任务" min-width="155"><template #default="{row}">{{ kindName(row.kind) }}<br><el-button link @click.stop="showJob(row)">{{ short(row.id) }}</el-button></template></el-table-column>
        <el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag><p v-if="row.cancel_requested" class="muted">已请求取消</p></template></el-table-column>
        <el-table-column label="进度" min-width="125"><template #default="{row}">{{ progressText(row.progress) }}</template></el-table-column>
        <el-table-column prop="error_code" label="错误码" min-width="130" />
        <el-table-column label="创建时间" min-width="175"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" min-width="190"><template #default="{row}"><el-button v-if="['queued','running'].includes(row.status)" link type="warning" :loading="acting===row.id" @click.stop="cancel(row)">取消</el-button><el-button v-if="['failed','cancelled','budget_exhausted'].includes(row.status)" link type="primary" :loading="acting===row.id" @click.stop="retry(row)">新建重试</el-button><router-link v-if="row.kind==='evaluation' && row.run_id" :to="'/evaluations/'+row.run_id" @click.stop>查看评价</router-link><router-link v-if="row.kind==='context'" to="/contexts" @click.stop>上下文</router-link></template></el-table-column>
      </el-table>
      <el-pagination class="section" v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="refresh(true)" />
    </el-card>
    <el-card v-if="selected" class="section"><template #header>任务详情 · {{ selected.id }}</template><p class="muted">尝试次数 {{ selected.attempt_count ?? '见任务记录' }} · {{ statusName(selected.status) }}</p><p v-if="selected.parent_job_id" class="muted">重试来源 {{ selected.parent_job_id }}</p><pre class="json-panel">{{ pretty(selected) }}</pre></el-card>
  </div>
</template>
<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import { formatDate, kindName, pretty, short, statusName, statusType } from '../utils/presentation'
import { newRequestKey } from '../utils/idempotency'
const route=useRoute(),router=useRouter(),jobs=ref([]),page=ref(1),total=ref(0),status=ref(''),selected=ref(null),loading=ref(false),paused=ref(false),acting=ref(null)
let alive=false,timer=null,refreshSequence=0,selectionSequence=0
const retryKeys=new Map()
function progressText(progress){if(!progress || !Object.keys(progress).length)return '等待状态更新';return progress.phase || progress.stage || (progress.completed!=null ? progress.completed+' / '+(progress.total ?? '?') : '已有执行记录')}
async function refresh(manual=false){
  clearTimeout(timer);if(manual){loading.value=true;paused.value=false}const sequence=++refreshSequence
  try { const response=await request.get('/jobs',{params:{page:page.value,page_size:20,...(status.value ? {status:status.value} : {})}});if(!alive || sequence!==refreshSequence)return;jobs.value=response.data.items;total.value=response.data.total;if(selected.value){const id=selected.value.id,selectedVersion=selectionSequence;const detail=await request.get('/jobs/'+encodeURIComponent(id));if(sequence===refreshSequence && selectedVersion===selectionSequence && selected.value?.id===id)selected.value=detail.data} }
  catch { if(sequence===refreshSequence)paused.value=true }
  finally { if(sequence===refreshSequence){loading.value=false;if(alive && !paused.value)timer=setTimeout(()=>refresh(),4000)} }
}
async function showJob(row){const sequence=++selectionSequence;selected.value=row;const response=await request.get('/jobs/'+encodeURIComponent(row.id));if(alive && sequence===selectionSequence)selected.value=response.data}
function changeFilter(){page.value=1;refresh(true)}
async function cancel(row){acting.value=row.id;try{await request.post('/jobs/'+encodeURIComponent(row.id)+'/cancel');ElMessage.success('取消请求已登记');await refresh()}finally{acting.value=null}}
async function retry(row){acting.value=row.id;if(!retryKeys.has(row.id))retryKeys.set(row.id,newRequestKey());try{const response=await request.post('/jobs/'+encodeURIComponent(row.id)+'/retry',undefined,{headers:{'Idempotency-Key':retryKeys.get(row.id)}});retryKeys.delete(row.id);ElMessage.success('重试任务已新建');page.value=1;status.value='';await refresh();if(row.kind==='evaluation' && response.data.run_id)await router.push('/evaluations/'+response.data.run_id);else await showJob(response.data)}finally{acting.value=null}}
onMounted(async()=>{alive=true;try{if(typeof route.query.job_id==='string')await showJob({id:route.query.job_id})}finally{await refresh(true)}})
onUnmounted(()=>{alive=false;refreshSequence++;selectionSequence++;clearTimeout(timer)})
</script>
