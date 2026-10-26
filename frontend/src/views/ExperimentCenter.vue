<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>实验与对照</h2><p class="muted">冻结协议、独立运行、分开记录人工与自动评分</p></div><el-button :loading="loading" @click="load(true)">刷新</el-button></div>
    <el-alert title="运行完成不等于质量更高。固定与 Agent 的实际调用、证据和失败分别保留；未评分显示为空，历史缓存不冒充新实跑。" type="info" :closable="false" />
    <el-alert v-if="loadError" title="实验列表或基础资料暂不可读取。检查连接后点击刷新，服务器记录仍会保留。" type="warning" :closable="false" class="section" />
    <el-row :gutter="20" class="section">
      <el-col :xs="24" :lg="10"><el-card><template #header>创建冻结实验协议</template>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="实验名称"><el-input data-testid="experiment-name" v-model="form.name" maxlength="160" placeholder="为这次对照命名" /></el-form-item>
          <div v-for="(item,index) in cases" :key="item.clientKey" class="experiment-case">
            <div class="actions"><strong>资料范围 {{ index+1 }}</strong><el-button v-if="cases.length>1" link type="danger" @click="cases.splice(index,1)">移除此范围</el-button></div>
            <el-form-item label="范围标识（字母、数字、下划线或短横线）"><el-input v-model="item.key" maxlength="64" /></el-form-item>
            <el-form-item label="公司"><CompanySelect :data-testid="'experiment-case-company-'+index" class="full" v-model="item.stock_code" /></el-form-item>
            <el-form-item label="报告年度"><el-input-number v-model="item.report_year" :min="1990" :max="2100" /></el-form-item>
            <el-form-item label="冻结索引"><el-select :data-testid="'experiment-case-index-'+index" class="full" v-model="item.index_id"><el-option v-for="index in indexes.filter(row=>row.status==='ready')" :key="index.id" :value="index.id" :label="index.profile+' · '+short(index.id)" /></el-select></el-form-item>
          </div>
          <el-button link :disabled="cases.length>=8" @click="addCase">再添加一个公司／年度范围（最多8个）</el-button>
          <el-form-item label="对照方法"><el-checkbox-group v-model="form.modes"><el-checkbox value="dual_fixed">固定双源</el-checkbox><el-checkbox value="dual_agent">Agent 扩展</el-checkbox></el-checkbox-group></el-form-item>
          <el-form-item label="问题范围"><el-radio-group v-model="scope"><el-radio value="single">同一自定义单题</el-radio><el-radio value="four">四维基础问题集</el-radio></el-radio-group></el-form-item>
          <template v-if="scope==='single'"><el-form-item label="题目维度"><el-select v-model="dimension" class="full"><el-option v-for="dim in dimensions" :key="dim.code" :value="dim.code" :label="dim.code+' '+dim.name" /></el-select></el-form-item><el-form-item label="对照问题"><el-input data-testid="experiment-question" type="textarea" :rows="3" v-model="question" maxlength="4000" placeholder="每种方法使用同一冻结问题" /></el-form-item></template>
          <el-form-item v-else label="问题集版本"><el-select v-model="form.question_set_version" class="full"><el-option v-for="set in questionSets" :key="set.version" :value="set.version" :label="set.version" /></el-select></el-form-item>
          <el-form-item label="生成模型"><el-select v-model="form.model" class="full"><el-option v-for="profile in profiles" :key="profile.name" :disabled="!profile.available" :value="profile.model || ''" :label="profile.model || '未配置'" /></el-select></el-form-item>
          <el-collapse>
            <el-collapse-item title="共同检索、模型与总调用预算" name="common">
              <el-form-item label="参考计数"><el-select data-testid="experiment-tokenizer" v-model="retrieval.tokenizer_kind" class="full"><el-option value="qwen3" label="Qwen3 冻结参考分词器" /><el-option value="utf8_diagnostic" label="UTF-8 字节诊断（不是 tokens）" /></el-select></el-form-item>
              <el-form-item label="每题参考计数上限"><el-input-number v-model="retrieval.max_reference_tokens" :min="1" :max="32000" :step="500" /></el-form-item>
              <el-form-item label="每个运行的最多模型调用"><el-input-number v-model="budget.max_model_calls" :min="minimumCalls" :max="40" /></el-form-item>
              <el-form-item label="每个运行的已观测 tokens 阈值"><el-input-number v-model="budget.max_observed_tokens" :min="1000" :max="1000000" :step="10000" /></el-form-item>
              <el-form-item label="单次输出 tokens 上限"><el-input-number v-model="budget.max_output_tokens" :min="128" :max="16000" :step="100" /></el-form-item>
              <el-form-item label="单次超时（秒）"><el-input-number v-model="budget.request_timeout_seconds" :min="1" :max="600" /></el-form-item>
              <el-form-item label="每个运行时限（秒）"><el-input-number v-model="budget.max_runtime_seconds" :min="1" :max="7200" :step="60" /></el-form-item>
              <el-form-item label="实验总调用预算"><el-input-number v-model="form.max_total_model_calls" :min="Math.min(reservedCalls,128)" :max="128" :step="16" /></el-form-item>
              <p class="muted">{{ plannedRuns }} 个新运行，共预留 {{ reservedCalls }} 次模型调用。所有运行按相同共同参数冻结，各方法实际消费分别统计。创建实验会排队这些真实模型任务。</p>
              <el-alert v-if="reservedCalls>128" title="预留调用超过单次实验上限128。请减少资料范围、对照方法或每个运行的调用预算。" type="warning" :closable="false" />
            </el-collapse-item>
            <el-collapse-item v-if="form.modes.includes('dual_agent')" title="Agent 规划与补检索约束" name="agent">
              <el-form-item label="最多规划轮次"><el-input-number v-model="agent.max_rounds" :min="1" :max="3" /></el-form-item>
              <el-form-item label="每个运行补检索次数上限"><el-input-number v-model="agent.max_supplemental_calls" :min="0" :max="12" /></el-form-item>
              <el-form-item label="允许一次修订"><el-switch v-model="agent.allow_revision" /></el-form-item>
              <p class="muted">补检索不扩展公司、年度或索引范围。核验是模型自评，新增调用不自动形成质量提升结论。</p>
            </el-collapse-item>
          </el-collapse>
          <el-button data-testid="experiment-create" type="primary" class="full section" :loading="creating" :disabled="!canCreate" @click="createExperiment">冻结协议并创建对照运行</el-button>
        </el-form>
      </el-card></el-col>
      <el-col :xs="24" :lg="14"><el-card><template #header>实验记录</template>
        <el-table :data="experiments" empty-text="尚未创建实验" v-loading="loading"><el-table-column label="实验" min-width="170"><template #default="{row}"><router-link :to="{path:'/experiments',query:{experiment_id:row.id}}">{{ row.name || short(row.id) }}</router-link></template></el-table-column><el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column><el-table-column label="创建时间（北京时间）" min-width="170"><template #default="{row}">{{ formatDate(row.created_at) }}</template></el-table-column></el-table>
        <el-pagination class="section" v-model:current-page="page" :total="total" :page-size="10" layout="total,prev,pager,next" @current-change="load" />
      </el-card>
      <el-card v-if="selected" class="section" data-testid="experiment-detail"><template #header><strong>{{ selected.name }}</strong></template>
        <div class="run-banner"><el-tag :type="statusType(selected.status)">{{ statusName(selected.status) }}</el-tag><span>实际运行 {{ selected.progress?.total ?? selected.items?.length ?? 0 }}</span><span>已完成 {{ selected.progress?.succeeded ?? 0 }}</span><span>部分完成 {{ selected.progress?.partial ?? 0 }}</span><span>失败 {{ selected.progress?.failed ?? 0 }}</span></div>
        <p class="muted hash">协议SHA256：{{ selected.protocol_sha256 }}</p>
        <el-alert v-if="pollError" title="自动刷新已暂停；任务仍在服务器保留，点击刷新恢复。" type="warning" :closable="false" />
        <div class="actions section"><el-button :loading="acting" @click="dispatch">恢复未排队单元</el-button><el-button v-if="activeExperiment" :loading="acting" type="warning" @click="cancel">请求取消所有未完成单元</el-button><el-button data-testid="experiment-export-json" :loading="exporting==='json'" @click="downloadExport('json')">下载 JSON 快照</el-button><el-button data-testid="experiment-export-csv" :loading="exporting==='csv'" @click="downloadExport('csv')">下载 CSV 对照表</el-button></div>
        <el-tabs class="section">
          <el-tab-pane label="运行矩阵">
            <el-table :data="selected.items || []" empty-text="暂无运行单元"><el-table-column prop="case_key" label="资料范围" min-width="110" /><el-table-column label="方法" min-width="110"><template #default="{row}">{{ modeName(row.mode) }}</template></el-table-column><el-table-column label="来源" min-width="120"><template #default="{row}">{{ row.origin==='existing_run' ? '关联已有运行' : '新建运行' }}</template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column><el-table-column label="运行／错误" min-width="160"><template #default="{row}"><router-link v-if="row.run_id" :to="'/evaluations/'+row.run_id">{{ short(row.run_id) }}</router-link><span v-if="row.error_code"> · {{ row.error_code }}</span></template></el-table-column></el-table>
            <ul class="limit-list"><li v-for="(item,index) in selected.comparison?.limitations || []" :key="index">{{ item }}</li></ul>
          </el-tab-pane>
          <el-tab-pane label="可比条件与实际消费">
            <el-alert title="只在冻结问题、资料范围、索引和模型参数等条件相容时对照；运行成功率与引用机械通过率不等于语义质量。" type="info" :closable="false" />
            <el-table :data="selected.comparison?.groups || []" class="section" empty-text="尚无可建立可比组的运行"><el-table-column label="公司／年度" min-width="130"><template #default="{row}">{{ row.stock_code }} · {{ row.report_year }}</template></el-table-column><el-table-column label="冻结条件" min-width="170"><template #default="{row}">{{ row.comparable ? '可按已记录条件对照' : '条件不足／无法配对' }}<p v-for="(reason,index) in row.reasons || []" :key="index" class="muted">{{ reason }}</p></template></el-table-column><el-table-column label="两种方法的实际运行" min-width="160"><template #default="{row}"><p v-for="method in ['dual_fixed','dual_agent']" :key="method">{{ modeName(method) }}：{{ row.modes?.[method]?.length || 0 }} 个</p></template></el-table-column><el-table-column label="允许差异" min-width="150"><template #default="{row}">{{ (row.controlled_differences || []).join('、') }}</template></el-table-column></el-table>
            <el-table :data="selected.comparison?.runs || []" class="section" empty-text="尚无实际用量"><el-table-column label="运行" min-width="110"><template #default="{row}"><router-link :to="'/evaluations/'+row.run_id">{{ short(row.run_id) }}</router-link></template></el-table-column><el-table-column label="方法" min-width="110"><template #default="{row}">{{ modeName(row.mode) }}</template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{row}">{{ statusName(row.status) }}</template></el-table-column><el-table-column prop="model_call_count" label="实际模型调用" width="115" /><el-table-column label="已报告 tokens" min-width="120"><template #default="{row}">{{ row.observed_total_tokens ?? '未知' }}<span v-if="row.provider_usage_complete===false">（用量不完整）</span></template></el-table-column><el-table-column label="质量分数" min-width="130"><template #default="{row}">{{ row.quality_score ?? '未自动计算' }}</template></el-table-column></el-table>
            <el-collapse class="section"><el-collapse-item title="完整可比签名与运行快照" name="comparison"><pre class="json-panel">{{ pretty({groups:selected.comparison?.groups,runs:selected.comparison?.runs}) }}</pre></el-collapse-item></el-collapse>
          </el-tab-pane>
          <el-tab-pane label="独立评分记录">
            <el-alert title="评分为追加记录，绑定具体回答版本。人工、自动与历史评分分开；未评分为空。自动评分结果须注明方法版本，本页面不会自动调用评审模型。" type="info" :closable="false" />
            <el-form label-position="top" class="section" @submit.prevent>
              <el-form-item label="实际运行"><el-select data-testid="rating-run" v-model="rating.run_id" class="full" @change="loadRatingRun"><el-option v-for="item in (selected.items || []).filter(row=>row.run_id)" :key="item.id" :value="item.run_id" :label="item.case_key+' · '+modeName(item.mode)+' · '+short(item.run_id)" /></el-select></el-form-item>
              <el-form-item label="已完成回答"><el-select data-testid="rating-answer" v-model="rating.answer_id" class="full"><el-option v-for="answer in ratingAnswers" :key="answer.id" :value="answer.id" :label="answer.question_key+' · '+answer.dimension" /></el-select></el-form-item>
              <el-form-item label="评分来源"><el-radio-group v-model="rating.source_kind"><el-radio value="manual">人工评分</el-radio><el-radio value="automatic">外部自动评分结果</el-radio></el-radio-group></el-form-item>
              <el-form-item label="量表版本"><el-select v-model="rating.rubric_version" class="full"><el-option v-for="rubric in rubrics" :key="rubric.version" :value="rubric.version" :label="rubric.name+' · '+rubric.version" /></el-select></el-form-item>
              <el-row :gutter="10"><el-col v-for="item in ratingCriteria" :key="item.key" :xs="24" :sm="12"><el-form-item :label="item.label"><el-select :data-testid="'rating-'+item.key" v-model="rating.values[item.key]" class="full"><el-option :value="''" label="未评分（null）" /><el-option v-for="number in 5" :key="number" :value="number" :label="String(number)" /></el-select></el-form-item></el-col></el-row>
              <el-form-item :label="rating.source_kind==='manual' ? '评分人' : '自动评分执行者／系统'"><el-input data-testid="rating-reviewer" v-model="rating.reviewer" maxlength="160" /></el-form-item>
              <el-form-item label="评分方法版本"><el-input data-testid="rating-method" v-model="rating.method_version" maxlength="160" placeholder="例如人工复核规则版本或评审模型与提示词版本" /></el-form-item>
              <el-form-item label="说明"><el-input type="textarea" :rows="2" v-model="rating.comment" maxlength="4000" placeholder="记录依据、限制或自动评分来源" /></el-form-item>
              <el-form-item label="明确更正已有记录（可不选）"><el-select v-model="rating.supersedes_score_id" clearable class="full"><el-option v-for="score in (selected.ratings || []).filter(row=>row.answer_id===rating.answer_id && row.source_kind===rating.source_kind)" :key="score.id" :value="score.id" :label="short(score.id)+' · '+formatDate(score.created_at)" /></el-select></el-form-item>
              <el-button data-testid="rating-submit" type="primary" :disabled="!canRate" :loading="ratingSaving" @click="saveRating">追加评分记录</el-button>
            </el-form>
            <h4>人工评分</h4><ScoreTable :scores="(selected.ratings || []).filter(row=>row.source_kind==='manual')" />
            <h4>自动评分</h4><ScoreTable :scores="(selected.ratings || []).filter(row=>row.source_kind==='automatic')" />
            <el-collapse class="section"><el-collapse-item title="分组评分汇总及可比限制" name="scoregroups"><pre class="json-panel">{{ pretty(selected.comparison?.rating_groups) }}</pre></el-collapse-item></el-collapse>
          </el-tab-pane>
          <el-tab-pane label="已有运行与历史资料">
            <el-alert title="已有运行通过原始快照关联，历史资料单独导入；两者均不会触发新的模型调用。历史缓存不计为本实验新实跑，也不自动进入质量均值。" type="info" :closable="false" />
            <el-form label-position="top" class="section" @submit.prevent><h4>关联已有评价</h4><el-form-item label="已有运行版本ID"><el-input v-model="attach.run_id" maxlength="36" placeholder="评价详情中的运行版本ID" /></el-form-item><el-form-item label="关联说明"><el-input v-model="attach.label" maxlength="160" /></el-form-item><el-button :disabled="!attach.run_id.trim()" :loading="attaching" @click="attachRun">关联已有运行</el-button></el-form>
            <el-divider /><h4>导入历史资料</h4><p class="muted">历史记录JSON须明确record_key和mode，可记录公司、年度、question、answer、status、quality_scores与notes。status为failed或not_run时，质量分数须为空；资料和问题版本以来源说明为准，不推断其真实性。</p>
            <el-form label-position="top" @submit.prevent><el-form-item label="批次名称"><el-input v-model="history.label" maxlength="200" /></el-form-item><el-form-item label="来源身份与版本说明"><el-input v-model="history.source_identity" type="textarea" :rows="2" maxlength="500" placeholder="说明原始实验、模型、数据集、问题集版本及取得方式" /></el-form-item><el-form-item label="历史记录JSON数组（最多200条）"><el-input v-model="history.records_text" type="textarea" :rows="5" placeholder='[{"record_key":"historical-1","mode":"unknown","notes":"资料来源及限制"}]' /></el-form-item><el-button :disabled="!history.label.trim() || !history.source_identity.trim() || !history.records_text.trim()" :loading="importingHistory" @click="importHistory">保存为独立历史批次</el-button></el-form>
            <el-empty v-if="!selected.historical_records?.length" description="尚未导入历史资料" /><pre v-else class="json-panel">{{ pretty(selected.historical_records) }}</pre>
          </el-tab-pane>
          <el-tab-pane label="冻结协议"><pre class="json-panel">{{ pretty(selected.protocol) }}</pre></el-tab-pane>
        </el-tabs>
      </el-card><el-empty v-else class="section" description="选择实验查看冻结协议、对照运行与评分" />
      </el-col>
    </el-row>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import { formatDate, modeName, pretty, short, statusName, statusType } from '../utils/presentation'
import { newRequestKey } from '../utils/idempotency'
import ScoreTable from '../components/ScoreTable.vue'
import CompanySelect from '../components/CompanySelect.vue'
const route=useRoute(),router=useRouter(),indexes=ref([]),questionSets=ref([]),profiles=ref([]),rubrics=ref([])
const experiments=ref([]),total=ref(0),page=ref(1),selected=ref(null),loading=ref(false),creating=ref(false),acting=ref(false),exporting=ref(null),pollError=ref(false),loadError=ref(false)
const form=reactive({name:'',modes:['dual_fixed','dual_agent'],question_set_version:'four-dim-v1',model:'',max_total_model_calls:128})
const cases=ref([{clientKey:newRequestKey(),key:'C1',index_id:'',stock_code:'',report_year:2024}]),scope=ref('single'),dimension=ref('D1'),question=ref('')
const retrieval=reactive({annual_top_k:5,regulation_top_k:5,max_reference_tokens:6000,tokenizer_kind:'qwen3',temporal_filter:false,unknown_temporal:'retain'})
const budget=reactive({max_model_calls:32,max_observed_tokens:80000,max_runtime_seconds:3600,max_output_tokens:8000,request_timeout_seconds:600}),agent=reactive({max_rounds:1,max_supplemental_calls:4,allow_revision:true})
const selectedSet=computed(()=>questionSets.value.find(row=>row.version===form.question_set_version)),dimensions=computed(()=>selectedSet.value?.dimensions || [])
const minimumCalls=computed(()=>(scope.value==='single' ? 1 : selectedSet.value?.questions?.length || 4)*(form.modes.includes('dual_agent') ? (Number.isInteger(agent.max_rounds) ? agent.max_rounds : 1)+2+Number(agent.allow_revision) : 1))
const plannedRuns=computed(()=>cases.value.length*form.modes.length),reservedCalls=computed(()=>plannedRuns.value*budget.max_model_calls)
const integer=(value,min,max)=>Number.isInteger(value) && value>=min && value<=max
const canCreate=computed(()=>Boolean(form.name.trim() && form.modes.length && profiles.value.some(row=>row.available && row.model===form.model) && cases.value.length>=1 && cases.value.length<=8 && cases.value.every(item=>/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(item.key.trim()) && item.stock_code && item.index_id && integer(item.report_year,1990,2100)) && new Set(cases.value.map(item=>item.key.trim())).size===cases.value.length && (scope.value==='single' ? question.value.trim() && dimension.value : selectedSet.value) && integer(budget.max_model_calls,minimumCalls.value,40) && integer(budget.max_observed_tokens,1,1000000) && integer(budget.max_output_tokens,128,16000) && integer(budget.request_timeout_seconds,1,600) && integer(budget.max_runtime_seconds,1,7200) && integer(retrieval.max_reference_tokens,1,32000) && (!form.modes.includes('dual_agent') || integer(agent.max_rounds,1,3) && integer(agent.max_supplemental_calls,0,12)) && reservedCalls.value<=128 && integer(form.max_total_model_calls,reservedCalls.value,128)))
const activeExperiment=computed(()=>Boolean(selected.value?.items?.some(item=>['pending','queued','running'].includes(item.status))))
const rating=reactive({run_id:'',answer_id:'',source_kind:'manual',rubric_version:'evidence-quality-v1',values:{faithfulness:'',coverage:'',professionalism:'',citation_quality:''},reviewer:'',method_version:'',comment:'',supersedes_score_id:null}),ratingAnswers=ref([]),ratingSaving=ref(false)
const ratingCriteria=[{key:'faithfulness',label:'事实忠实性（1—5）'},{key:'coverage',label:'证据覆盖（1—5）'},{key:'professionalism',label:'专业性（1—5）'},{key:'citation_quality',label:'引用质量（1—5）'}]
const canRate=computed(()=>Boolean(selected.value && rating.answer_id && rating.reviewer.trim() && rating.method_version.trim() && Object.values(rating.values).some(value=>value!=='' && value!==null)))
const attach=reactive({run_id:'',label:''}),attaching=ref(false),history=reactive({label:'',source_identity:'',records_text:''}),importingHistory=ref(false)
let alive=false,timer=null,selectionSequence=0,listSequence=0,ratingSequence=0,caseSequence=1
const pendingKeys=new Map()
function keyFor(kind,payload){const signature=kind+':'+(kind==='create' ? '' : selected.value?.id || '')+':'+JSON.stringify(payload);if(!pendingKeys.has(signature))pendingKeys.set(signature,newRequestKey());return {signature,key:pendingKeys.get(signature)}}
function addCase(){if(cases.value.length<8)cases.value.push({clientKey:newRequestKey(),key:'C'+(++caseSequence),index_id:'',stock_code:'',report_year:2024})}
watch(minimumCalls,value=>{if(budget.max_model_calls<value)budget.max_model_calls=value})
watch(reservedCalls,value=>{if(form.max_total_model_calls<value)form.max_total_model_calls=Math.min(128,value)})
async function load(manual=false){if(manual)pollError.value=false;loading.value=true;loadError.value=false;const sequence=++listSequence;try{const responses=await Promise.all([request.get('/indexes'),request.get('/question-sets'),request.get('/model-profiles'),request.get('/experiments/rubrics'),request.get('/experiments',{params:{page:page.value,page_size:10}})]);if(!alive || sequence!==listSequence)return;indexes.value=responses[0].data;questionSets.value=responses[1].data.items;profiles.value=responses[2].data.items;rubrics.value=responses[3].data.items;experiments.value=responses[4].data.items;total.value=responses[4].data.total;if(!form.model)form.model=profiles.value.find(row=>row.available)?.model || '';await loadSelected()}catch{if(sequence===listSequence)loadError.value=true}finally{if(sequence===listSequence)loading.value=false}}
async function loadSelected(){clearTimeout(timer);const id=String(route.query.experiment_id || ''),sequence=++selectionSequence;if(!id){selected.value=null;return}try{const response=await request.get('/experiments/'+encodeURIComponent(id));if(!alive || sequence!==selectionSequence)return;selected.value=response.data;pollError.value=false;if(activeExperiment.value)timer=setTimeout(()=>loadSelected(),3000)}catch{if(sequence===selectionSequence)pollError.value=true}}
async function createExperiment(){if(!canCreate.value)return;const payload={name:form.name.trim(),cases:cases.value.map(({clientKey,...item})=>({...item,key:item.key.trim()})),modes:[...form.modes],question_set_version:form.question_set_version,questions:scope.value==='single' ? [{key:'experiment-1',dimension:dimension.value,text:question.value.trim()}] : null,retrieval:{...retrieval},budget:{...budget},agent:{...agent},model:form.model || null,max_total_model_calls:form.max_total_model_calls};const pending=keyFor('create',payload);creating.value=true;try{const response=await request.post('/experiments',payload,{headers:{'Idempotency-Key':pending.key}});pendingKeys.delete(pending.signature);ElMessage.success('实验协议已冻结，运行任务已创建');await router.push({path:'/experiments',query:{experiment_id:response.data.id}});await load()}finally{creating.value=false}}
async function dispatch(){acting.value=true;try{await request.post('/experiments/'+encodeURIComponent(selected.value.id)+'/dispatch');await loadSelected();ElMessage.success('未排队单元已检查，原运行不会覆盖')}finally{acting.value=false}}
async function cancel(){acting.value=true;try{await request.post('/experiments/'+encodeURIComponent(selected.value.id)+'/cancel');await loadSelected();ElMessage.success('取消请求已登记，已发出的模型请求消费仍可能发生')}finally{acting.value=false}}
async function loadRatingRun(){const id=rating.run_id,sequence=++ratingSequence;rating.answer_id='';ratingAnswers.value=[];rating.supersedes_score_id=null;if(!id)return;const response=await request.get('/evaluations/'+encodeURIComponent(id));if(sequence===ratingSequence && id===rating.run_id)ratingAnswers.value=(response.data.answers || []).filter(answer=>answer.status==='succeeded' && answer.answer_text)}
async function saveRating(){if(!canRate.value)return;const payload={...rating,values:Object.fromEntries(Object.entries(rating.values).map(([key,value])=>[key,value==='' || value===null ? null:Number(value)])),reviewer:rating.reviewer.trim(),method_version:rating.method_version.trim(),supersedes_score_id:rating.supersedes_score_id || null};const pending=keyFor('rating',payload);ratingSaving.value=true;try{await request.post('/experiments/'+encodeURIComponent(selected.value.id)+'/ratings',payload,{headers:{'Idempotency-Key':pending.key}});pendingKeys.delete(pending.signature);await loadSelected();ElMessage.success('评分已追加，原记录保留')}finally{ratingSaving.value=false}}
async function attachRun(){const payload={run_id:attach.run_id.trim(),label:attach.label.trim()},pending=keyFor('attach',payload);attaching.value=true;try{await request.post('/experiments/'+encodeURIComponent(selected.value.id)+'/runs',payload,{headers:{'Idempotency-Key':pending.key}});pendingKeys.delete(pending.signature);await loadSelected();ElMessage.success('已有运行已关联，实际来源与可比限制保留')}finally{attaching.value=false}}
async function importHistory(){let records;try{records=JSON.parse(history.records_text);if(!Array.isArray(records) || records.length<1 || records.length>200)throw new Error()}catch{ElMessage.warning('历史记录须为1至200条的有效JSON数组，未保存');return}const payload={label:history.label.trim(),source_identity:history.source_identity.trim(),records},pending=keyFor('history',payload);importingHistory.value=true;try{await request.post('/experiments/'+encodeURIComponent(selected.value.id)+'/history',payload,{headers:{'Idempotency-Key':pending.key}});pendingKeys.delete(pending.signature);await loadSelected();ElMessage.success('历史批次已独立保存，未标记为新模型运行')}finally{importingHistory.value=false}}
async function downloadExport(format){const id=selected.value.id;exporting.value=format;try{const response=await fetch('/api/v1/experiments/'+encodeURIComponent(id)+'/exports?format='+format);const contentType=(response.headers.get('content-type') || '').toLowerCase();if(!response.ok || !(format==='json' ? contentType.includes('application/json') : contentType.includes('text/csv'))){let message='导出失败，未保存文件';try{message=(await response.json()).message || message}catch{}throw new Error(message)}const blob=await response.blob(),url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download='experiment-'+id+'.'+format;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(error){ElMessage.error(error.message || '导出失败')}finally{exporting.value=null}}
watch(()=>route.query.experiment_id,()=>{selected.value=null;rating.run_id='';rating.answer_id='';ratingAnswers.value=[];if(alive)loadSelected()})
watch(()=>rating.answer_id,()=>{rating.supersedes_score_id=null;for(const key of Object.keys(rating.values))rating.values[key]=''})
watch(()=>rating.source_kind,()=>{rating.supersedes_score_id=null;rating.reviewer='';rating.method_version='';for(const key of Object.keys(rating.values))rating.values[key]=''})
onMounted(()=>{alive=true;if(typeof route.query.attach_run==='string')attach.run_id=route.query.attach_run;load()})
onUnmounted(()=>{alive=false;selectionSequence++;listSequence++;ratingSequence++;clearTimeout(timer)})
</script>
<style scoped>
.experiment-case{border:1px solid #e2e8f0;border-radius:6px;padding:14px;margin-bottom:14px}
</style>
