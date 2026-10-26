<template>
  <div class="workbench">
    <div class="page-heading">
      <div><h2>上下文与任务</h2><p class="muted">按公司与年度检索双源证据，在预算内组装完整片段，并保存可回放快照。</p></div>
      <el-button :loading="loading" @click="refreshManually">刷新任务与运行</el-button>
    </div>
    <el-alert title="本页准备检索上下文，不调用生成 API。来源定位与时间元数据筛选均不代表已认证语义支持或法规适用。" type="info" :closable="false" />
    <el-alert v-if="pollingPaused" class="section" title="自动刷新已暂停，请检查连接后点击刷新恢复。" type="warning" :closable="false" />
    <el-row :gutter="20" class="section">
      <el-col :xs="24" :lg="9">
        <el-card>
          <template #header>创建上下文任务</template>
          <el-form label-position="top" @submit.prevent>
            <el-form-item label="冻结索引版本">
              <el-select v-model="form.index_id" placeholder="选择就绪索引" class="full">
                <el-option v-for="index in indexes" :key="index.id" :value="index.id" :disabled="index.status !== 'ready'" :label="index.profile + ' · ' + index.status + ' · ' + index.id.slice(0,8)" />
              </el-select>
            </el-form-item>
            <el-form-item label="股票代码"><el-input v-model="form.stock_code" placeholder="六位代码，例如 603288" maxlength="9" /></el-form-item>
            <el-form-item label="报告年度"><el-input-number v-model="form.report_year" :min="1990" :max="2100" /></el-form-item>
            <el-form-item label="问题"><el-input v-model="form.query" type="textarea" :rows="4" maxlength="4000" show-word-limit placeholder="输入需要年报披露与法规依据共同回答的问题" /></el-form-item>
            <el-row :gutter="12">
              <el-col :span="12"><el-form-item label="年报候选上限"><el-input-number v-model="form.annual_top_k" :min="1" :max="20" class="full" /></el-form-item></el-col>
              <el-col :span="12"><el-form-item label="法规候选上限"><el-input-number v-model="form.regulation_top_k" :min="1" :max="20" class="full" /></el-form-item></el-col>
            </el-row>
            <el-form-item label="预算计数方式">
              <el-select v-model="form.tokenizer_kind" class="full">
                <el-option value="qwen3" label="冻结 Qwen3 参考分词器" />
                <el-option value="utf8_diagnostic" label="UTF-8 字节数，仅作诊断" />
              </el-select>
            </el-form-item>
            <el-form-item :label="form.tokenizer_kind === 'qwen3' ? '参考 tokens 上限' : 'UTF-8 字节上限（不是 tokens）'">
              <el-input-number v-model="form.max_reference_tokens" :min="1" :max="32000" :step="500" class="full" />
            </el-form-item>
            <p class="muted">完整片段若超出预算会被跳过，之后的较短片段仍可入选。实际用量以冻结快照为准。</p>
            <el-form-item label="最长运行时间（秒）"><el-input-number v-model="form.max_runtime_seconds" :min="1" :max="3600" :step="60" /></el-form-item>
            <el-divider />
            <el-form-item label="按明确日期筛选法规"><el-switch v-model="form.temporal_filter" /></el-form-item>
            <template v-if="form.temporal_filter">
              <el-form-item label="目标日期"><el-date-picker v-model="form.target_date" type="date" value-format="YYYY-MM-DD" placeholder="不能用报告年度代替" class="full" /></el-form-item>
              <el-form-item label="日期角色">
                <el-select v-model="form.target_date_role" class="full"><el-option value="event_date" label="事项日期" /><el-option value="evaluation_date" label="评价日期" /></el-select>
              </el-form-item>
              <el-form-item label="有效期间未知的法规">
                <el-radio-group v-model="form.unknown_temporal"><el-radio value="retain">保留并标记未知</el-radio><el-radio value="exclude">排除</el-radio></el-radio-group>
              </el-form-item>
              <p class="muted">筛选仅按已登记的生效起点与排他结束日期执行；缺少边界不会被自动视为无限有效。</p>
            </template>
            <el-button type="primary" class="full section" :loading="submitting" :disabled="!canSubmit" @click="submit">创建持久化任务</el-button>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="15">
        <el-card>
          <template #header>任务记录<span class="muted header-note">刷新页面后从服务端恢复</span></template>
          <el-table :data="jobs" v-loading="loading" empty-text="暂无任务">
            <el-table-column label="任务" min-width="130"><template #default="{row}"><div>{{ kindName(row.kind) }}</div><span class="muted">{{ short(row.id) }}</span></template></el-table-column>
            <el-table-column label="状态" min-width="125"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag><div v-if="row.cancel_requested" class="muted">已请求取消</div></template></el-table-column>
            <el-table-column prop="error_code" label="错误码" min-width="140" />
            <el-table-column label="操作" min-width="170"><template #default="{row}">
              <el-button v-if="['queued','running'].includes(row.status)" link type="warning" :loading="activeAction === row.id" @click="cancelJob(row)">取消</el-button>
              <el-button v-if="['failed','cancelled','budget_exhausted'].includes(row.status)" link type="primary" :loading="activeAction === row.id" @click="retryJob(row)">新建重试</el-button>
              <el-button v-if="row.run_id && row.kind==='context'" link @click="loadRun(row.run_id)">查看上下文</el-button>
              <router-link v-if="row.run_id && row.kind==='evaluation'" :to="'/evaluations/'+row.run_id">查看评价</router-link>
            </template></el-table-column>
          </el-table>
          <el-pagination class="section" v-model:current-page="jobPage" :total="jobTotal" :page-size="10" layout="total, prev, pager, next" @current-change="refreshManually" />
          <p class="muted">取消请求可能需等当前操作结束；重试创建新的任务与运行，保留原记录。</p>
        </el-card>
        <el-card class="section">
          <template #header>上下文运行</template>
          <el-table :data="runs" highlight-current-row @row-click="row => loadRun(row.id)" empty-text="暂无上下文运行">
            <el-table-column label="运行" min-width="130"><template #default="{row}"><el-button link @click.stop="loadRun(row.id)">{{ short(row.id) }}</el-button></template></el-table-column>
            <el-table-column label="状态" min-width="120"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="问题" min-width="200"><template #default="{row}">{{ row.request?.query || row.query || '点击查看冻结请求' }}</template></el-table-column>
          </el-table>
          <el-pagination class="section" v-model:current-page="runPage" :total="runTotal" :page-size="10" layout="total, prev, pager, next" @current-change="refreshManually" />
        </el-card>
        <el-card v-if="selectedRun" class="section">
          <template #header><span>运行快照 · {{ short(selectedRun.id) }}</span><el-tag class="header-note" :type="statusType(selectedRun.status)">{{ statusName(selectedRun.status) }}</el-tag></template>
          <p class="muted hash">快照 SHA256：{{ selectedRun.snapshot_sha256 || '尚未产生快照' }}</p>
          <el-collapse>
            <el-collapse-item title="冻结请求与预算" name="request"><pre class="json-panel">{{ pretty(selectedRun.request) }}</pre></el-collapse-item>
          </el-collapse>
          <template v-if="context">
            <div class="stats"><span>实际计数：<strong>{{ context.reference_token_count }}</strong></span><span>单位：{{ context.budget_unit || '见计数器身份' }}</span><span>入选证据：{{ records.length }}</span><span>省略候选：{{ omissions.length }}</span></div>
            <el-tabs>
              <el-tab-pane label="冻结上下文">
                <pre class="context-text">{{ context.context_text || '预算内没有可用证据，请查看省略原因与诊断。' }}</pre>
              </el-tab-pane>
              <el-tab-pane label="入选证据">
                <el-table :data="records" empty-text="无入选证据">
                  <el-table-column prop="evidence_id" label="编号" width="80" />
                  <el-table-column label="来源" width="95"><template #default="{row}">{{ row.source_role === 'annual_report' ? '公司年报' : row.source_role === 'regulation' ? '法规' : row.source_role }}</template></el-table-column>
                  <el-table-column prop="title" label="标题" />
                  <el-table-column label="定位" min-width="150"><template #default="{row}">{{ row.physical_page ? '物理第'+row.physical_page+'页' : '正文字符区间' }} [{{ row.char_start }}, {{ row.char_end }})</template></el-table-column>
                  <el-table-column width="100"><template #default="{row}"><el-button link @click="showEvidence(row)">查看原文</el-button></template></el-table-column>
                </el-table>
              </el-tab-pane>
              <el-tab-pane label="省略原因">
                <el-table :data="omissions" empty-text="没有被省略的候选">
                  <el-table-column prop="chunk_id" label="片段 ID" min-width="190" />
                  <el-table-column label="原因" min-width="170"><template #default="{row}">{{ row.reason || row.omission_reason || row.code }}</template></el-table-column>
                </el-table>
              </el-tab-pane>
              <el-tab-pane label="检索与诊断">
                <el-collapse>
                  <el-collapse-item title="组装诊断与分词器身份" name="diagnostics"><pre class="json-panel">{{ pretty({diagnostics:context.diagnostics,tokenizer_identity:context.tokenizer_identity}) }}</pre></el-collapse-item>
                  <el-collapse-item title="双源检索与时点筛选记录" name="retrieval"><pre class="json-panel">{{ pretty(selectedRun.snapshot?.retrieval) }}</pre></el-collapse-item>
                </el-collapse>
              </el-tab-pane>
            </el-tabs>
          </template>
          <el-empty v-else :description="selectedRun.error_code || (['queued','running'].includes(selectedRun.status) ? '任务处理中，尚未提交完整快照' : '该运行没有成功保存的上下文快照')" />
        </el-card>
      </el-col>
    </el-row>
    <el-dialog v-model="evidenceOpen" title="冻结证据内容" width="80%">
      <template v-if="selectedEvidence">
        <p><strong>{{ selectedEvidence.evidence_id }} · {{ selectedEvidence.title }}</strong></p>
        <p class="muted hash">片段：{{ selectedEvidence.chunk_id }} · 文件版本：{{ selectedEvidence.version_id || '见定位记录' }}</p>
        <p class="muted">本窗口展示保存时的证据文本；打开原件时服务端会重新检查来源完整性。</p>
        <a v-if="selectedEvidence.version_id" :href="originalFileUrl(selectedEvidence)" target="_blank" rel="noopener">打开原始文件{{ selectedEvidence.physical_page ? '对应物理页' : '' }}</a>
        <pre class="context-text">{{ selectedEvidence.content }}</pre>
        <el-collapse><el-collapse-item title="冻结定位与来源元数据" name="locator"><pre class="json-panel">{{ pretty(selectedEvidence) }}</pre></el-collapse-item></el-collapse>
      </template>
    </el-dialog>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '../api/request'

const form = reactive({
  index_id:'', query:'', stock_code:'', report_year:2024,
  annual_top_k:5, regulation_top_k:5, max_reference_tokens:6000,
  tokenizer_kind:'qwen3', temporal_filter:false, target_date:null,
  target_date_role:'event_date', unknown_temporal:'retain', max_runtime_seconds:600,
})
const indexes=ref([]), jobs=ref([]), runs=ref([])
const jobPage=ref(1), jobTotal=ref(0), runPage=ref(1), runTotal=ref(0)
const selectedRun=ref(null), selectedRunId=ref(null), selectedEvidence=ref(null), evidenceOpen=ref(false)
const loading=ref(false), submitting=ref(false), activeAction=ref(null), pollingPaused=ref(false)
const context=computed(()=>selectedRun.value?.snapshot?.context || null)
const records=computed(()=>context.value?.source_records || [])
const omissions=computed(()=>context.value?.omitted_records || [])
const canSubmit=computed(()=>Boolean(form.index_id && form.stock_code.trim() && form.query.trim() && (!form.temporal_filter || (form.target_date && form.target_date_role))))
const short=value=>value ? String(value).slice(0,8) : '—'
const pretty=value=>JSON.stringify(value ?? null,null,2)
const statuses={queued:'排队中',running:'运行中',succeeded:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消',budget_exhausted:'预算耗尽'}
const statusName=value=>statuses[value] || value || '未知'
const statusType=value=>value==='succeeded' ? 'success' : ['failed','budget_exhausted'].includes(value) ? 'danger' : value==='cancelled' ? 'warning' : 'info'
const kindName=value=>({parse:'文档解析',index:'索引构建',context:'上下文组装',evaluation:'模型评价'}[value] || value)
const originalFileUrl=row=>'/api/v1/document-versions/'+encodeURIComponent(row.version_id)+'/file'+(row.physical_page ? '#page='+row.physical_page : '')
let alive=false, timer=null, runRequestSequence=0

async function loadRun(id) {
  if(!id)return
  selectedRunId.value=id
  const sequence=++runRequestSequence
  const response=await request.get('/retrieval/contexts/'+encodeURIComponent(id))
  if(alive && selectedRunId.value===id && sequence===runRequestSequence)selectedRun.value=response.data
}
async function loadLists() {
  const [jobResponse,runResponse,indexResponse]=await Promise.all([
    request.get('/jobs',{params:{page:jobPage.value,page_size:10}}),
    request.get('/retrieval/contexts',{params:{page:runPage.value,page_size:10}}),
    request.get('/indexes'),
  ])
  if(!alive)return
  jobs.value=jobResponse.data.items || []
  jobTotal.value=jobResponse.data.total || 0
  runs.value=runResponse.data.items || []
  runTotal.value=runResponse.data.total || 0
  indexes.value=indexResponse.data
  if(selectedRunId.value)await loadRun(selectedRunId.value)
}
function schedulePoll() {
  clearTimeout(timer)
  if(!alive || pollingPaused.value)return
  timer=setTimeout(async()=>{
    try { await loadLists() }
    catch { pollingPaused.value=true }
    finally { schedulePoll() }
  },4000)
}
async function refreshManually() {
  clearTimeout(timer)
  loading.value=true
  pollingPaused.value=false
  try { await loadLists() }
  catch { pollingPaused.value=true }
  finally { loading.value=false;schedulePoll() }
}
async function submit() {
  if(!canSubmit.value)return
  const payload={...form,stock_code:form.stock_code.trim(),query:form.query.trim(),
    target_date:form.temporal_filter ? form.target_date : null,
    target_date_role:form.temporal_filter ? form.target_date_role : null,
    unknown_temporal:form.temporal_filter ? form.unknown_temporal : 'retain'}
  submitting.value=true
  try {
    const response=await request.post('/retrieval/contexts',payload)
    ElMessage.success('上下文任务已创建，关闭页面不会删除任务')
    runPage.value=1;jobPage.value=1
    await loadLists()
    if(response.data.run_id && response.data.kind==='context')await loadRun(response.data.run_id)
  } finally { submitting.value=false }
}
async function cancelJob(job) {
  activeAction.value=job.id
  try {
    await request.post('/jobs/'+encodeURIComponent(job.id)+'/cancel')
    ElMessage.success('取消请求已登记')
    await loadLists()
  } finally { activeAction.value=null }
}
async function retryJob(job) {
  activeAction.value=job.id
  try {
    const response=await request.post('/jobs/'+encodeURIComponent(job.id)+'/retry')
    ElMessage.success('已新建重试任务，原记录保留')
    jobPage.value=1;runPage.value=1
    await loadLists()
    if(response.data.run_id && response.data.kind==='context')await loadRun(response.data.run_id)
  } finally { activeAction.value=null }
}
function showEvidence(record) { selectedEvidence.value=record;evidenceOpen.value=true }
onMounted(async()=>{alive=true;await refreshManually()})
onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>
<style scoped>
.workbench{max-width:1500px;margin:auto}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.section{margin-top:18px}.muted{color:#64748b;font-size:12px;line-height:1.6}.full{width:100%}.header-note{margin-left:12px}.hash{overflow-wrap:anywhere}.stats{display:flex;flex-wrap:wrap;gap:16px;padding:14px 0;font-size:13px}.context-text,.json-panel{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;background:#f8fafc;padding:18px;border-radius:4px;max-height:60vh;overflow:auto}.json-panel{font-size:12px}a{color:#2563eb}.el-col{margin-bottom:18px}
@media(max-width:700px){.page-heading{align-items:flex-start;flex-direction:column}.context-text,.json-panel{padding:12px}}
</style>
