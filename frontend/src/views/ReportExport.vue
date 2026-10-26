<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>报告导出</h2><p class="muted">从指定运行的冻结快照生成报告，正文、限制和证据附录保留完整内容。</p></div><el-button @click="$router.push('/evaluations')">选择评价运行</el-button></div>
    <el-card class="section">
      <el-form label-position="top" @submit.prevent><el-form-item label="评价运行版本"><el-select v-model="runId" class="full" filterable placeholder="选择已完成或部分完成的运行" @change="loadRun"><el-option v-for="row in runs" :key="row.id" :value="row.id" :disabled="!['succeeded','partial'].includes(row.status)" :label="row.stock_code+' · '+row.report_year+' · '+statusName(row.status)+' · '+short(row.id)" /></el-select></el-form-item></el-form>
      <el-button :loading="loading" @click="load">刷新运行与导出记录</el-button>
      <template v-if="run">
        <div class="stats"><span>状态：{{ statusName(run.status) }}</span><span>运行：{{ run.id }}</span></div>
        <p class="muted hash">冻结结果 SHA256：{{ run.snapshot_sha256 || '无结果快照' }}</p>
        <el-alert v-if="run.status!=='succeeded'" title="当前运行并非完整成功。允许导出时，报告将保留技术失败、证据不足与缺失维度的说明。" type="warning" :closable="false" />
        <el-alert title="第三章指数结果尚未接入。报告不会以 0 分代替缺失结果。" type="info" :closable="false" class="section" />
        <div class="actions section"><el-button data-testid="report-create-html" type="primary" :loading="exporting==='html'" :disabled="!canExport || !!exporting" @click="createExport('html')">生成 HTML 报告</el-button><el-button data-testid="report-create-pdf" type="primary" plain :loading="exporting==='pdf'" :disabled="!canExport || !!exporting" @click="createExport('pdf')">生成中文 PDF</el-button><router-link :to="'/evaluations/'+run.id">查看正文与证据</router-link></div>
      </template>
    </el-card>
    <el-card class="section"><template #header>固定版本导出记录</template>
      <el-table :data="exports" v-loading="loading" empty-text="当前运行暂无导出文件">
        <el-table-column prop="format" label="格式" width="90" /><el-table-column label="生成时间（北京时间）" min-width="180"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column><el-table-column prop="size_bytes" label="字节数" min-width="110" /><el-table-column prop="file_sha256" label="文件 SHA256" min-width="240" /><el-table-column label="操作" min-width="165"><template #default="{row}"><a v-if="row.format==='html'" :href="exportUrl(row.id)" target="_blank" rel="noopener noreferrer">预览</a><el-button :data-testid="'report-download-'+row.format" link type="primary" :loading="downloading===row.id" @click="download(row)">下载 {{ row.format?.toUpperCase() }}</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="exportTotal>20" class="section" v-model:current-page="exportPage" :total="exportTotal" :page-size="20" layout="prev,pager,next" @current-change="loadExports" />
      <p class="muted">下载前核对 HTTP 状态及文件类型。文件从保存的版本读取，不随以后重新评价而改变。</p>
    </el-card>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import { formatDate, short, statusName } from '../utils/presentation'
import { newRequestKey } from '../utils/idempotency'
const route=useRoute(),runs=ref([]),runId=ref(''),run=ref(null),exports=ref([]),exportPage=ref(1),exportTotal=ref(0),loading=ref(false),exporting=ref(null),downloading=ref(null)
const canExport=computed(()=>Boolean(run.value?.snapshot?.answers?.some(answer=>answer.status==='succeeded' && answer.answer_text) && ['succeeded','partial'].includes(run.value.status)))
const exportUrl=id=>'/api/v1/exports/'+encodeURIComponent(id)+'/file'
let exportSequence=0,submission=null
async function loadExports(){const id=runId.value,sequence=++exportSequence;if(!id){exports.value=[];exportTotal.value=0;return}const response=await request.get('/evaluations/'+encodeURIComponent(id)+'/exports',{params:{page:exportPage.value,page_size:20}});if(sequence===exportSequence && id===runId.value){exports.value=response.data.items;exportTotal.value=response.data.total}}
async function loadRun(){run.value=null;exports.value=[];exportPage.value=1;if(!runId.value)return;const id=runId.value;const response=await request.get('/evaluations/'+encodeURIComponent(id));if(id===runId.value){run.value=response.data;await loadExports()}}
async function load(){loading.value=true;try{const response=await request.get('/evaluations',{params:{page:1,page_size:200}});runs.value=response.data.items;await loadRun()}finally{loading.value=false}}
async function createExport(format){
  if(!canExport.value)return
  const signature=runId.value+':'+format;if(submission?.signature!==signature)submission={signature,key:newRequestKey()}
  exporting.value=format
  try { await request.post('/evaluations/'+encodeURIComponent(runId.value)+'/exports',{format},{headers:{'Idempotency-Key':submission.key}});submission=null;await loadExports();ElMessage.success('报告已生成，可预览或下载') }
  finally { exporting.value=null }
}
async function download(row){
  downloading.value=row.id
  try {
    const response=await fetch(exportUrl(row.id))
    const type=(response.headers.get('content-type') || '').toLowerCase()
    if(!response.ok || (row.format==='pdf' ? !type.includes('application/pdf') : !type.includes('text/html'))){let message='报告下载失败，未保存文件';try{const body=await response.json();message=body.message || message}catch{}throw new Error(message)}
    const blob=await response.blob(),url=URL.createObjectURL(blob),anchor=document.createElement('a')
    anchor.href=url;anchor.download=row.filename || '评价报告.'+row.format;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)
  } catch(error){ElMessage.error(error.message || '下载失败')}
  finally { downloading.value=null }
}
onMounted(async()=>{if(typeof route.query.run_id==='string')runId.value=route.query.run_id;await load()})
</script>
