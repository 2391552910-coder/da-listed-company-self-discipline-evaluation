<template>
  <div class="page-shell" v-loading="loading">
    <div class="page-heading"><div><h2>评价详情 <span class="muted">{{ short(runId) }}</span></h2><p class="muted">{{ run?.request?.stock_code || run?.stock_code }} · {{ run?.request?.report_year || run?.report_year }} 年度</p></div><div class="actions"><el-button @click="refresh(true)">刷新</el-button><el-button @click="$router.push('/evaluations')">运行列表</el-button><el-button v-if="snapshot?.answers?.length" type="primary" @click="$router.push({path:'/reports-export',query:{run_id:runId}})">报告导出</el-button></div></div>
    <template v-if="run">
      <div class="run-banner"><el-tag :type="statusType(run.status)">{{ statusName(run.status) }}</el-tag><span v-if="job?.cancel_requested">取消请求已登记</span><span class="muted">运行版本 {{ run.id }} · 创建于 {{ formatDate(run.created_at) }}（北京时间）</span></div>
      <el-alert v-if="pollError" title="自动刷新已暂停。检查连接后点击刷新恢复，持久化任务仍保留。" type="warning" :closable="false" class="section" />
      <el-alert v-if="['queued','running'].includes(run.status)" title="任务尚在处理中。已发出的模型调用可能无法撤销；取消后不发布未完成结果。" type="info" :closable="false" class="section" />
      <el-alert v-if="run.error_code || job?.error_code" :title="'运行信息：'+(run.error_code || job.error_code)" type="warning" :closable="false" class="section" />
      <div class="actions section"><el-button v-if="['queued','running'].includes(run.status)" type="warning" :loading="acting" @click="cancel">请求取消</el-button><el-button v-if="['failed','cancelled','budget_exhausted'].includes(run.status)" :loading="acting" @click="retry">新建重试运行</el-button><router-link :to="{path:'/tasks',query:{job_id:run.job_id}}">查看任务账本</router-link></div>
      <template v-if="snapshot">
        <el-card class="section"><template #header>四维完成情况与汇总</template>
          <el-alert title="这里展示各题真实执行状态与证据限制。模型的支持判断未经人工确认；没有数值指数或自动质量评分。" type="info" :closable="false" />
          <p class="muted">问题范围：{{ summary?.question_set_is_custom ? '自定义问题快照' : '版本化四维问题集' }} · 四维覆盖：{{ summary?.coverage_complete ? '均有完成的评价' : '存在未评价或未完成维度' }}</p>
          <el-table :data="summary?.dimensions || []" class="section">
            <el-table-column label="维度" min-width="160"><template #default="{row}">{{ row.code }} {{ row.name }}</template></el-table-column>
            <el-table-column label="状态" min-width="110"><template #default="{row}">{{ dimensionStatus(row.status) }}</template></el-table-column>
            <el-table-column prop="question_count" label="题数" width="70" /><el-table-column prop="succeeded_count" label="成功执行" width="100" /><el-table-column prop="failed_count" label="技术失败" width="100" /><el-table-column prop="evidence_insufficient_count" label="证据不足" width="100" />
          </el-table>
          <ul v-if="summary?.limitations?.length" class="limit-list"><li v-for="(item,index) in summary.limitations" :key="index">{{ item }}</li></ul>
        </el-card>
        <el-card v-for="answer in snapshot.answers || []" :key="answer.id" class="section answer-card">
          <template #header><div class="answer-heading"><strong>{{ answer.dimension }} · {{ answer.question_text }}</strong><el-tag :type="statusType(answer.status)">{{ statusName(answer.status) }}</el-tag></div></template>
          <p class="muted">问题 {{ answer.question_key }} · {{ evidenceName(answer.evidence_status) }}<span v-if="answer.error_code"> · 错误码 {{ answer.error_code }}</span></p>
          <p data-testid="detail-answer-text" class="answer-text">{{ answer.answer_text || '此题未产生可发布正文。' }}</p>
          <ul v-if="answer.limitations?.length" class="limit-list"><li v-for="(item,index) in answer.limitations" :key="index">{{ item }}</li></ul>
          <el-collapse>
            <el-collapse-item :title="'论断与引用 · '+(answer.claims?.length || 0)+' 条论断'" :name="answer.id+'-claims'">
              <div v-for="claim in answer.claims || []" :key="claim.id" data-testid="detail-claim" class="claim">
                <p><strong>{{ claim.id }}</strong> {{ claim.text }}</p>
                <p class="muted">模型支持判断：{{ supportName(claim.support_status) }} · 语义独立核实：{{ claim.semantic_support_verified ? '已核实（见核实记录）' : '未核实' }} · 人工复核：{{ claim.human_review_status==='confirmed' ? '已确认' : claim.human_review_status==='rejected' ? '已拒绝' : '未复核' }}</p>
                <el-empty v-if="!claim.citations?.length" description="本论断没有可核对引用" :image-size="50" />
                <div v-for="(citation,index) in claim.citations || []" :key="citation.evidence_id+'-'+index" class="citation">
                  <el-button data-testid="citation-open" link type="primary" @click="showCitation(citation,answer)">[{{ citation.evidence_id }}] 查看引文与原件</el-button><span class="muted">机械校验：{{ citation.mechanical_status==='valid' ? '通过' : citation.mechanical_status==='invalid' ? '未通过' : '见校验记录' }} · 逐字匹配：{{ citation.exact_quote_match===true ? '是' : citation.exact_quote_match===false ? '否' : '未确定' }} · 来源完整性：{{ citation.source_integrity_status==='verified' ? '已核对' : citation.source_integrity_status==='invalid' ? '无效' : '未核对' }}</span><blockquote>{{ citation.quote || '未提供逐字引文' }}</blockquote>
                </div>
              </div>
              <el-empty v-if="!answer.claims?.length" description="没有结构化论断，请查看本题执行与校验信息" :image-size="60" />
            </el-collapse-item>
            <el-collapse-item :title="'冻结证据 · '+(answer.context?.source_records?.length || 0)+' 个完整片段'" :name="answer.id+'-evidence'">
              <el-table :data="answer.context?.source_records || []" empty-text="没有入选证据">
                <el-table-column prop="evidence_id" label="引用号" width="85" /><el-table-column label="来源" width="100"><template #default="{row}">{{ sourceRole(row.source_role) }}</template></el-table-column><el-table-column prop="title" label="标题" min-width="180" /><el-table-column label="定位" min-width="160"><template #default="{row}">{{ row.physical_page ? '物理第'+row.physical_page+'页' : '正文帧' }} [{{ row.char_start }},{{ row.char_end }})</template></el-table-column><el-table-column width="90"><template #default="{row}"><el-button link @click="showEvidence(row)">核对</el-button></template></el-table-column>
              </el-table>
              <p class="muted">实际参考计数 {{ answer.context?.reference_token_count ?? '未知' }} · {{ answer.context?.budget_unit || '计数单位见分词器身份' }}；省略 {{ answer.context?.omitted_records?.length || 0 }} 个候选。</p>
              <el-collapse><el-collapse-item title="省略记录与组装诊断" name="omissions"><pre class="json-panel">{{ pretty({omissions:answer.context?.omitted_records,diagnostics:answer.context?.diagnostics}) }}</pre></el-collapse-item><el-collapse-item title="冻结上下文全文" name="context"><pre class="text-panel">{{ answer.context?.context_text }}</pre></el-collapse-item></el-collapse>
            </el-collapse-item>
            <el-collapse-item title="引文机械校验与本题执行记录" :name="answer.id+'-trace'">
              <p class="muted">校验 {{ answer.validation?.status || '未执行' }}；机械有效引用 {{ answer.validation?.mechanical_citation_valid_count ?? 0 }} / {{ answer.validation?.citation_count ?? 0 }}。机械有效只核对编号、来源和逐字引文。</p>
              <pre class="json-panel">{{ pretty({validation:answer.validation,retrieval:answer.retrieval,prompt:answer.prompt,model_call:answer.model_call}) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </template>
      <el-empty v-else :description="['queued','running'].includes(run.status) ? '运行尚未提交完整结果快照' : '当前运行没有可发布结果，请查看任务错误与调用记录'" />
      <el-card class="section"><template #header>版本、配置与调用账本</template><p class="muted hash">结果快照 SHA256：{{ run.snapshot_sha256 || '尚未产生' }}</p><p v-if="run.parent_run_id" class="muted">重试来源 <router-link :to="'/evaluations/'+run.parent_run_id">{{ run.parent_run_id }}</router-link></p><el-collapse><el-collapse-item title="冻结请求" name="request"><pre class="json-panel">{{ pretty(run.request) }}</pre></el-collapse-item><el-collapse-item title="模型调用与任务账本（含未知用量或中断）" name="calls"><pre class="json-panel">{{ pretty({model_calls:run.model_calls || snapshot?.model_calls,job}) }}</pre></el-collapse-item></el-collapse></el-card>
    </template>
    <EvidenceViewer v-model="evidenceOpen" :record="evidence" :quote="evidenceQuote" />
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import EvidenceViewer from '../components/EvidenceViewer.vue'
import { evidenceName, formatDate, pretty, short, sourceRole, statusName, statusType, supportName } from '../utils/presentation'
import { newRequestKey } from '../utils/idempotency'
const route=useRoute(),router=useRouter(),run=ref(null),job=ref(null),loading=ref(false),acting=ref(false),pollError=ref(false)
const evidence=ref(null),evidenceQuote=ref(''),evidenceOpen=ref(false)
const runId=computed(()=>String(route.params.id || '')),snapshot=computed(()=>run.value?.snapshot),summary=computed(()=>snapshot.value?.summary)
const dimensionStatus=value=>({missing:'未评价',not_evaluated:'未评价',not_run:'未评价',incomplete:'未完整完成',insufficient:'证据不足'}[value] || statusName(value))
let timer=null,alive=false,sequence=0
const retryKeys=new Map()
async function refresh(manual=false){
  clearTimeout(timer)
  if(manual){loading.value=true;pollError.value=false}
  const current=++sequence,id=runId.value
  try { const response=await request.get('/evaluations/'+encodeURIComponent(id));if(!alive || current!==sequence)return;run.value=response.data;if(run.value.job_id){const jr=await request.get('/jobs/'+encodeURIComponent(run.value.job_id));if(current===sequence)job.value=jr.data} }
  catch { if(current===sequence)pollError.value=true }
  finally { if(current===sequence){loading.value=false;if(alive && !pollError.value && ['queued','running'].includes(run.value?.status))timer=setTimeout(()=>refresh(),3000)} }
}
async function cancel(){acting.value=true;try{await request.post('/jobs/'+encodeURIComponent(run.value.job_id)+'/cancel');ElMessage.success('取消请求已登记');await refresh()}finally{acting.value=false}}
async function retry(){const jobId=run.value.job_id;acting.value=true;if(!retryKeys.has(jobId))retryKeys.set(jobId,newRequestKey());try{const response=await request.post('/jobs/'+encodeURIComponent(jobId)+'/retry',undefined,{headers:{'Idempotency-Key':retryKeys.get(jobId)}});retryKeys.delete(jobId);ElMessage.success('已创建新的重试运行');if(response.data.run_id)await router.push('/evaluations/'+response.data.run_id);else await router.push('/tasks')}finally{acting.value=false}}
function showEvidence(record,quote=''){evidence.value=record;evidenceQuote.value=quote;evidenceOpen.value=true}
function showCitation(citation,answer){const record=citation.source_record || citation.source || answer.context?.source_records?.find(row=>row.evidence_id===citation.evidence_id);if(record)showEvidence(record,citation.quote);else ElMessage.warning('该引用未找到有效来源，请查看校验错误。')}
watch(runId,()=>{run.value=null;job.value=null;pollError.value=false;if(alive)refresh(true)})
onMounted(()=>{alive=true;refresh(true)})
onUnmounted(()=>{alive=false;sequence++;clearTimeout(timer)})
</script>
