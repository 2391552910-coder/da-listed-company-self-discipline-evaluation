<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>评价工作台</h2><p class="muted">固定双源检索流程 · 冻结配置与证据 · 单题或四维评价</p></div><el-button @click="$router.push('/documents')">文档与索引</el-button></div>
    <el-alert title="评价调用已配置的真实模型服务。模型判断、引文机械校验与人工复核分开记录；当前不生成第三章数值指数。" type="info" :closable="false" />
    <el-row :gutter="20" class="section">
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header>新建评价</template>
          <el-form label-position="top" @submit.prevent>
            <el-form-item label="公司"><el-select data-testid="evaluation-company" class="full" v-model="form.stock_code" filterable placeholder="先在公司管理录入"><el-option v-for="company in companies" :key="company.id" :label="company.stock_code+' '+company.stock_name" :value="company.stock_code" /></el-select></el-form-item>
            <el-form-item label="报告年度"><el-input-number v-model="form.report_year" :min="1990" :max="2100" /></el-form-item>
            <el-form-item label="冻结索引版本"><el-select data-testid="evaluation-index" v-model="form.index_id" class="full" placeholder="选择包含该公司年度的就绪索引"><el-option v-for="index in indexes" :key="index.id" :value="index.id" :disabled="index.status!=='ready'" :label="index.profile+' · '+statusName(index.status)+' · '+short(index.id)" /></el-select></el-form-item>
            <el-alert v-if="!indexes.some(row=>row.status==='ready')" title="暂无就绪索引。请导入年报和法规，解析后构建索引。" type="warning" :closable="false" />
            <el-form-item label="评价范围"><el-radio-group v-model="scope"><el-radio data-testid="evaluation-scope-single" value="single">单题验证</el-radio><el-radio value="four">完整四维</el-radio></el-radio-group></el-form-item>
            <template v-if="scope==='single'">
              <el-form-item label="维度"><el-select data-testid="evaluation-dimension" v-model="dimension" class="full"><el-option v-for="dim in dimensions" :key="dim.code" :value="dim.code" :label="dim.code+' '+dim.name" /></el-select></el-form-item>
              <el-form-item label="评价问题"><el-input data-testid="evaluation-question" v-model="question" type="textarea" :rows="4" maxlength="4000" show-word-limit placeholder="请输入需要年报披露与法规依据共同回答的问题" /></el-form-item>
            </template>
            <template v-else>
              <el-form-item label="版本化问题集"><el-select v-model="form.question_set_version" class="full"><el-option v-for="set in questionSets" :key="set.version" :value="set.version" :label="set.version" /></el-select></el-form-item>
              <ol class="question-list"><li v-for="item in selectedSet?.questions || []" :key="item.key"><strong>{{ item.dimension }}</strong> {{ item.text }}</li></ol>
              <p class="muted">每题分别检索与生成，汇总明确列出失败题和证据不足维度，不计算数值分数。</p>
            </template>
            <el-form-item label="真实生成模型"><el-select class="full" v-model="model"><el-option v-for="profile in profiles" :key="profile.name" :value="profile.model || ''" :disabled="!profile.available" :label="(profile.model || profile.name)+(profile.available ? ' · 可用配置' : ' · '+(profile.reason || '未配置'))" /></el-select></el-form-item>
            <el-alert v-if="!providerAvailable" title="生成服务未配置或不可用，请在服务端配置模型后刷新。" type="warning" :closable="false" />
            <el-collapse class="section">
              <el-collapse-item title="检索与法规时点" name="retrieval">
                <el-row :gutter="12"><el-col :span="12"><el-form-item label="年报候选上限"><el-input-number v-model="retrieval.annual_top_k" :min="1" :max="20" class="full" /></el-form-item></el-col><el-col :span="12"><el-form-item label="法规候选上限"><el-input-number v-model="retrieval.regulation_top_k" :min="1" :max="20" class="full" /></el-form-item></el-col></el-row>
                <el-form-item label="参考分词器"><el-select data-testid="evaluation-tokenizer" v-model="retrieval.tokenizer_kind" class="full"><el-option value="qwen3" label="Qwen3 冻结参考分词器" /><el-option value="utf8_diagnostic" label="UTF-8 字节诊断（不是 tokens）" /></el-select></el-form-item>
                <el-form-item :label="retrieval.tokenizer_kind==='qwen3' ? '每题参考 tokens 上限' : '每题参考 UTF-8 字节上限'"><el-input-number v-model="retrieval.max_reference_tokens" :min="1" :max="32000" :step="500" /></el-form-item>
                <el-form-item label="按明确日期筛选法规"><el-switch v-model="retrieval.temporal_filter" /></el-form-item>
                <template v-if="retrieval.temporal_filter">
                  <el-form-item label="事项／评价日期"><el-date-picker v-model="targetDate" type="date" value-format="YYYY-MM-DD" placeholder="不可用报告年度代替" class="full" /></el-form-item>
                  <el-form-item label="日期角色"><el-select v-model="dateRole" class="full"><el-option value="event_date" label="事项日期" /><el-option value="evaluation_date" label="评价日期" /></el-select></el-form-item>
                  <el-form-item label="期间未知的法规"><el-radio-group v-model="retrieval.unknown_temporal"><el-radio value="retain">保留并标记未知</el-radio><el-radio value="exclude">排除</el-radio></el-radio-group></el-form-item>
                  <p v-if="scope==='four'" class="muted">当前日期将写入四维问题的运行快照；基础问题集版本保留，运行标记为自定义问题快照。</p>
                </template>
              </el-collapse-item>
              <el-collapse-item title="模型调用与运行预算" name="budget">
                <el-form-item label="最多模型调用次数"><el-input-number v-model="budget.max_model_calls" :min="1" :max="32" /><span class="field-note">本次至少 {{ plannedCalls }} 次</span></el-form-item>
                <el-form-item label="最大已观测 tokens"><el-input-number v-model="budget.max_observed_tokens" :min="1000" :max="1000000" :step="10000" /></el-form-item>
                <el-form-item label="单次输出 tokens 上限"><el-input-number v-model="budget.max_output_tokens" :min="128" :max="16000" :step="100" /></el-form-item>
                <el-form-item label="单次请求超时（秒）"><el-input-number v-model="budget.request_timeout_seconds" :min="1" :max="600" /></el-form-item>
                <el-form-item label="整个运行时限（秒）"><el-input-number v-model="budget.max_runtime_seconds" :min="1" :max="7200" :step="60" /></el-form-item>
                <p class="muted">参考证据计数与服务商实际用量分别记录。外部请求中断不保证撤销服务商计费。</p>
              </el-collapse-item>
            </el-collapse>
            <el-button data-testid="evaluation-submit" class="full section" type="primary" :loading="submitting" :disabled="!canSubmit" @click="submit">创建评价任务</el-button>
            <p class="muted">任务在服务端持续运行，关闭或刷新本页后可从任务记录恢复。</p>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="14">
        <el-card><template #header><span>评价运行记录</span><el-button class="right" :loading="loading" @click="load">刷新</el-button></template>
          <el-table :data="runs" v-loading="loading" empty-text="尚无评价运行">
            <el-table-column label="公司／年度" min-width="130"><template #default="{row}">{{ row.stock_code }} · {{ row.report_year }}</template></el-table-column>
            <el-table-column label="运行" min-width="110"><template #default="{row}"><router-link :to="'/evaluations/'+row.id">{{ short(row.id) }}</router-link></template></el-table-column>
            <el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="创建时间（北京时间）" min-width="175"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column>
          </el-table>
          <el-pagination class="section" v-model:current-page="page" :total="total" :page-size="10" layout="total, prev, pager, next" @current-change="load" />
          <el-button class="section" @click="$router.push('/tasks')">全部任务与重试</el-button>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import { formatDate, short, statusName, statusType } from '../utils/presentation'
import { newRequestKey } from '../utils/idempotency'
const route=useRoute(),router=useRouter()
const form=reactive({index_id:'',stock_code:'',report_year:2024,question_set_version:'four-dim-v1'})
const retrieval=reactive({annual_top_k:5,regulation_top_k:5,max_reference_tokens:6000,tokenizer_kind:'qwen3',temporal_filter:false,unknown_temporal:'retain'})
const budget=reactive({max_model_calls:8,max_observed_tokens:80000,max_runtime_seconds:1800,max_output_tokens:8000,request_timeout_seconds:600})
const scope=ref('single'),dimension=ref('D1'),question=ref(''),targetDate=ref(null),dateRole=ref('event_date'),model=ref('')
const companies=ref([]),indexes=ref([]),questionSets=ref([]),profiles=ref([]),runs=ref([]),page=ref(1),total=ref(0),loading=ref(false),submitting=ref(false)
const selectedSet=computed(()=>questionSets.value.find(row=>row.version===form.question_set_version))
const dimensions=computed(()=>selectedSet.value?.dimensions || [])
const providerAvailable=computed(()=>profiles.value.some(row=>row.available && row.model===model.value))
const plannedCalls=computed(()=>scope.value==='single' ? 1 : selectedSet.value?.questions?.length || 4)
const canSubmit=computed(()=>Boolean(form.index_id && form.stock_code && providerAvailable.value && budget.max_model_calls>=plannedCalls.value && (scope.value==='single' ? question.value.trim() && dimension.value : selectedSet.value) && (!retrieval.temporal_filter || targetDate.value)))
let pendingSubmission=null,loadSequence=0
async function load(){
  loading.value=true;const sequence=++loadSequence
  try { const responses=await Promise.all([request.get('/companies'),request.get('/indexes'),request.get('/question-sets'),request.get('/model-profiles'),request.get('/evaluations',{params:{page:page.value,page_size:10}})]);if(sequence!==loadSequence)return;companies.value=responses[0].data.items;indexes.value=responses[1].data;questionSets.value=responses[2].data.items;profiles.value=responses[3].data.items;runs.value=responses[4].data.items;total.value=responses[4].data.total;if(!model.value)model.value=profiles.value.find(row=>row.available)?.model || '' }
  finally { if(sequence===loadSequence)loading.value=false }
}
async function submit(){
  if(!canSubmit.value)return
  let questions=null
  if(scope.value==='single')questions=[{key:'custom-1',dimension:dimension.value,text:question.value.trim(),declared_target_date:retrieval.temporal_filter ? targetDate.value : null,target_date_role:retrieval.temporal_filter ? dateRole.value : null}]
  else if(retrieval.temporal_filter)questions=selectedSet.value.questions.map(item=>({...item,declared_target_date:targetDate.value,target_date_role:dateRole.value}))
  const payload={...form,questions,retrieval:{...retrieval},budget:{...budget},model:model.value || null}
  const signature=JSON.stringify(payload)
  if(!pendingSubmission || pendingSubmission.signature!==signature)pendingSubmission={signature,key:newRequestKey()}
  submitting.value=true
  try { const response=await request.post('/evaluations',payload,{headers:{'Idempotency-Key':pendingSubmission.key}});pendingSubmission=null;ElMessage.success('评价任务已创建');await router.push('/evaluations/'+response.data.run_id) }
  finally { submitting.value=false }
}
onMounted(async()=>{if(typeof route.query.stock_code==='string')form.stock_code=route.query.stock_code;if(typeof route.query.index_id==='string')form.index_id=route.query.index_id;const year=Number(route.query.report_year);if(Number.isInteger(year) && year>=1990 && year<=2100)form.report_year=year;await load()})
</script>
