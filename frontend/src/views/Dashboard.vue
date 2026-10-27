<template>
  <div class="page-shell">
    <section class="keynote-hero" aria-label="双源评价工作空间">
      <div class="hero-copy"><h1>从原文出发。<br>让评价有据可循。</h1><p>连接年报披露与法规依据，在每一次运行中保存证据、结论与限制。</p><div class="hero-actions"><el-button type="primary" @click="$router.push(stepTarget('/evaluations/new'))">创建评价</el-button><el-button @click="$router.push(stepTarget('/documents'))">管理双源文档</el-button></div></div>
      <div class="hero-stage"><p class="hero-stage-title">双源评价流程</p><div class="source-scene" aria-hidden="true"><div class="source-sheet"><SceneIcon name="document" /><span>公司年报</span></div><span class="scene-link" /><div class="scene-core"><SceneIcon name="layers" /></div><span class="scene-link" /><div class="source-sheet"><SceneIcon name="report" /><span>法规依据</span></div></div><div class="hero-tabs" role="tablist" aria-label="了解评价流程"><button v-for="(item,index) in scenes" :id="'scene-tab-'+index" :key="item.title" type="button" role="tab" :aria-selected="sceneIndex===index" :tabindex="sceneIndex===index ? 0 : -1" aria-controls="scene-description" @click="sceneIndex=index" @keydown.right.prevent="selectScene(index+1)" @keydown.left.prevent="selectScene(index-1)">{{ item.title }}</button></div><transition name="detail-swap" mode="out-in"><p id="scene-description" class="hero-explanation" role="tabpanel" :aria-labelledby="'scene-tab-'+sceneIndex" :key="sceneIndex">{{ scenes[sceneIndex].text }}</p></transition></div>
    </section>
    <div class="page-heading"><div><h2>主体自律评价研究工作台</h2><p class="muted">从双源文档到可回放评价，再到固定版本报告。</p></div><el-button :loading="loading" @click="load">刷新统计</el-button></div>
    <el-card class="section">
      <el-form :inline="true" @submit.prevent="applyScope">
        <el-form-item label="公司"><CompanySelect data-testid="dashboard-company" v-model="companyId" value-field="id" placeholder="全部公司" /></el-form-item>
        <el-form-item label="报告年度"><el-input-number data-testid="dashboard-year" v-model="year" :min="1990" :max="2100" :precision="0" :controls="false" placeholder="全部年度" /></el-form-item>
        <el-form-item><el-button data-testid="dashboard-apply" type="primary" native-type="submit" :loading="loading">应用筛选</el-button><el-button data-testid="dashboard-reset" @click="resetScope">全部公司与年度</el-button></el-form-item>
      </el-form>
      <p class="muted" data-testid="dashboard-scope">当前统计：{{ scopeLabel }}。年报、索引与评价按此范围统计；法规属于共享资料库，不随公司或报告年度缩小。公司数量仅受公司筛选影响。</p>
      <el-alert v-if="invalidScope" title="网址中的公司或年度参数不合法，请调整筛选后重新应用。" type="error" :closable="false" show-icon />
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    </el-card>
    <div class="summary-stats section" v-loading="loading">
      <el-card v-for="item in stats" :key="item.key"><span :data-testid="'dashboard-count-'+item.key" class="stat-number">{{ dashboard?.counts?.[item.key] ?? '—' }}</span><p class="muted">{{ item.name }}</p></el-card>
    </div>
    <el-card class="section">
      <template #header><div class="page-heading"><span>任务状态</span><el-button link @click="$router.push('/tasks')">查看任务</el-button></div></template>
      <div class="task-stats" data-testid="dashboard-job-statuses"><span v-for="item in jobStatuses" :key="item.status"><el-tag :type="statusType(item.status)">{{ statusName(item.status) }}</el-tag> {{ item.count ?? '—' }}</span></div>
      <p class="muted">任务按当前公司与年度关联的年报、索引、上下文及评价计次，并保留共享法规解析任务。重试形成新任务，分别计数。</p>
    </el-card>
    <el-card class="section">
      <template #header>四维证据覆盖</template>
      <p class="muted" data-testid="dashboard-coverage-note" v-if="coverage">在当前范围 {{ coverage.total_runs }} 次评价中，检查最新 {{ coverage.inspected_runs }} 次运行（上限 {{ coverage.run_limit }} 次）。{{ coverage.omitted_runs ? '还有 '+coverage.omitted_runs+' 次较早运行未纳入本表。' : '' }}重复评价及重试按运行次数计数。片段入选、机械引用有效均不代表语义支持、法规适用或评价质量；没有质量评分。</p>
      <p class="muted" v-else>证据覆盖统计暂不可用。缺失值显示为 —。</p>
      <el-alert v-if="coverage?.truncated" title="当前范围的运行数量超过检查上限，以下为部分运行统计。" type="warning" :closable="false" />
      <el-alert v-if="coverage?.issues?.length" :title="coverage.issues.length+' 次运行的快照无法可靠校验，已计入未知；没有作为零质量结果。'" type="warning" :closable="false" class="section" />
      <el-table data-testid="dashboard-coverage" :data="dimensionRows" class="section" v-loading="loading">
        <el-table-column label="维度" fixed min-width="180"><template #default="{row}">{{ row.code }} {{ row.name }}</template></el-table-column>
        <el-table-column v-for="column in coverageColumns" :key="column.key" :label="column.name" :min-width="column.width || 105"><template #default="{row}">{{ row[column.key] ?? '—' }}</template></el-table-column>
      </el-table>
      <p class="muted">来源与引用计数仅包含已校验的成功终态答案。待完成题目、中止或失败题目以及无法校验的题目分别列示；“资料不足回答”包含模型自报或系统因证据缺失而记录的不足。各状态题数相加为总题数。</p>
      <ul v-if="coverage?.issues?.length" class="limit-list">
        <li v-for="issue in coverage.issues.slice(0,10)" :key="issue.run_id"><router-link :to="'/evaluations/'+issue.run_id">{{ short(issue.run_id) }}</router-link>：{{ issue.error_code }}</li>
        <li v-if="coverage.issues.length>10">另有 {{ coverage.issues.length-10 }} 次运行无法校验。</li>
      </ul>
    </el-card>
    <el-card class="section"><template #header>开始一次可追溯评价</template><div class="workflow"><div v-for="(step,index) in steps" :key="step.path" class="workflow-step"><span class="step-number">{{ index+1 }}</span><h3>{{ step.name }}</h3><p class="muted">{{ step.description }}</p><el-button :type="index===2 ? 'primary' : 'default'" @click="$router.push(stepTarget(step.path))">{{ step.action }}</el-button></div></div></el-card>
    <el-row :gutter="18" class="section">
      <el-col :xs="24" :lg="14"><el-card><template #header>最近评价运行</template><el-table data-testid="dashboard-recent-runs" :data="dashboard?.recent_runs || []" :empty-text="error || invalidScope ? '统计暂不可用' : '当前公司与年度暂无评价运行'"><el-table-column label="公司与年度" min-width="130"><template #default="{row}">{{ row.stock_code }} · {{ row.report_year }}</template></el-table-column><el-table-column label="模式" min-width="105"><template #default="{row}">{{ modeName(row.mode) }}</template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column><el-table-column label="运行详情" min-width="100"><template #default="{row}"><router-link :to="'/evaluations/'+row.id">{{ short(row.id) }}</router-link></template></el-table-column></el-table></el-card></el-col>
      <el-col :xs="24" :lg="10"><el-card><template #header>模型与研究边界</template><p v-if="profileError" class="muted">模型配置暂不可用，请检查服务后刷新。</p><p v-for="profile in profiles" :key="profile.name" class="model-profile"><el-tag :type="profile.available ? 'info' : 'warning'">{{ profile.available ? '配置可用' : '尚未配置' }}</el-tag> {{ profile.model || profile.name }}</p><p class="muted">这里只检查模型配置，尚未探测外部服务连通性。模型支持判断、来源机械校验和人工核对分别展示；实际回答质量需结合引用原文判断。</p><el-alert title="第三章 AHP-Transformer 指数结果待接入，不显示演示分数。" type="info" :closable="false" /></el-card></el-col>
    </el-row>
  </div>
</template>
<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SceneIcon from '../components/SceneIcon.vue'
import CompanySelect from '../components/CompanySelect.vue'
import request from '../api/request'
import { modeName, short, statusName, statusType } from '../utils/presentation'

const sceneIndex=ref(0)
const scenes=[{title:'年报披露',text:'限定公司与报告年度，保存文档版本、物理页码和原文字符位置。'},{title:'法规依据',text:'从共享法规库检索依据，保留版本与时点信息，具体适用性仍需核对。'},{title:'评价结论',text:'逐题保存回答、引用和资料缺口。模型判断、机械校验与人工复核分别记录。'}]
function selectScene(index){sceneIndex.value=(index+scenes.length)%scenes.length;document.getElementById('scene-tab-'+sceneIndex.value)?.focus()}
const route=useRoute(),router=useRouter(),companyId=ref(null),year=ref(null),scope=ref({company_id:null,year:null})
const loading=ref(false),error=ref(''),invalidScope=ref(false),dashboard=ref(null),profiles=ref([]),profileError=ref(false)
let alive=true,sequence=0
const stats=[{key:'companies',name:'当前公司数量'},{key:'annual_documents',name:'范围内年报文档'},{key:'regulation_documents',name:'共享法规文档'},{key:'ready_indexes',name:'包含范围内年报的就绪索引'},{key:'evaluation_runs',name:'范围内评价运行'}]
const scopeLabel=computed(()=>[(dashboard.value?.scope?.company_name ? dashboard.value.scope.stock_code+' '+dashboard.value.scope.company_name : scope.value.company_id ? '公司 ID '+scope.value.company_id : '全部公司'),scope.value.year ? scope.value.year+' 年报' : '全部报告年度'].join('，'))
const jobStatuses=computed(()=>dashboard.value?.job_statuses || ['queued','running','succeeded','partial','failed','cancelled','budget_exhausted'].map(status=>({status,count:null})))
const coverage=computed(()=>dashboard.value?.coverage)
const dimensionRows=computed(()=>coverage.value?.dimensions || [{code:'D1',name:'信息披露质量'},{code:'D2',name:'董事会治理有效性'},{code:'D3',name:'内部控制与合规性'},{code:'D4',name:'第三方声誉'}])
const coverageColumns=[{key:'total_answers',name:'总题数',width:85},{key:'succeeded_answers',name:'成功题数',width:90},{key:'failed_answers',name:'失败或中止',width:105},{key:'pending_answers',name:'待完成',width:90},{key:'unknown_answers',name:'无法校验',width:95},{key:'annual_context_answers',name:'年报片段入选',width:125},{key:'regulation_context_answers',name:'法规片段入选',width:125},{key:'both_context_answers',name:'双源片段入选',width:125},{key:'evidence_insufficient_answers',name:'资料不足回答',width:125},{key:'mechanically_valid_citation_answers',name:'机械引用有效',width:125}]
const steps=[{name:'公司与文档',description:'登记公司，导入完整年报与法规，保存原件和文档版本。',path:'/documents',action:'导入文档'},{name:'解析与索引',description:'核对解析来源，构建双源索引，在预算内准备证据。',path:'/contexts',action:'检索与核对'},{name:'单题或四维评价',description:'选择问题、模型与预算，运行后逐题核对论断和引用。',path:'/evaluations/new',action:'创建评价'},{name:'固定版本报告',description:'从已完成的结果快照生成完整 HTML 或中文 PDF。',path:'/reports-export',action:'查看报告'}]
function stepTarget(path){
  const query={}
  if(path==='/documents' && scope.value.company_id)query.company_id=scope.value.company_id
  if(['/documents','/evaluations/new'].includes(path)){
    if(scope.value.year)query.report_year=scope.value.year
    if(dashboard.value?.scope?.stock_code)query.stock_code=dashboard.value.scope.stock_code
  }
  return {path,query}
}
function parseQuery(value,min,max){
  if(value===undefined)return null
  if(typeof value!=='string' || !/^\d+$/.test(value))return NaN
  const parsed=Number(value)
  return Number.isSafeInteger(parsed) && parsed>=min && parsed<=max ? parsed : NaN
}
async function load(){
  const current=++sequence
  dashboard.value=null;error.value='';profiles.value=[];profileError.value=false
  if(!alive || invalidScope.value){loading.value=false;return}
  const params={...(scope.value.company_id ? {company_id:scope.value.company_id} : {}),...(scope.value.year ? {year:scope.value.year} : {})}
  loading.value=true
  const responses=await Promise.allSettled([request.get('/dashboard',{params}),request.get('/model-profiles')])
  if(!alive || current!==sequence)return
  if(responses[0].status==='fulfilled')dashboard.value=responses[0].value.data
  else error.value=responses[0].reason?.response?.status===404 ? '所选公司不存在，请重新选择公司。' : '总览统计加载失败，请检查服务后刷新；缺失项显示为 —。'
  if(responses[1].status==='fulfilled')profiles.value=responses[1].value.data.items
  else profileError.value=true
  loading.value=false
}
async function applyScope(){
  const next={...route.query}
  delete next.company_id;delete next.year
  if(companyId.value)next.company_id=String(companyId.value)
  if(year.value!==null && year.value!==undefined)next.year=String(year.value)
  if(next.company_id===route.query.company_id && next.year===route.query.year)load()
  else await router.replace({path:'/',query:next})
}
function resetScope(){companyId.value=null;year.value=null;applyScope()}
watch(()=>[route.query.company_id,route.query.year],()=>{
  const company=parseQuery(route.query.company_id,1,2147483647),reportYear=parseQuery(route.query.year,1990,2100)
  invalidScope.value=Number.isNaN(company) || Number.isNaN(reportYear)
  companyId.value=Number.isNaN(company) ? null : company;year.value=Number.isNaN(reportYear) ? null : reportYear
  scope.value={company_id:companyId.value,year:year.value}
  load()
},{immediate:true})
onUnmounted(()=>{alive=false;sequence++})
</script>
<style scoped>
.stat-number{font-size:36px;color:#1d1d1f;font-weight:600;letter-spacing:-1.5px;font-variant-numeric:tabular-nums}.summary-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:18px}.task-stats{display:flex;flex-wrap:wrap;gap:18px}.task-stats>span{font-size:14px}.workflow{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}.workflow-step{padding:8px 12px}.workflow-step h3{font-size:15px}.step-number{display:inline-flex;width:29px;height:29px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:50%;align-items:center;justify-content:center;color:#2563eb;font-size:13px}.workflow-step p{min-height:52px}.model-profile{font-size:13px;overflow-wrap:anywhere}@media(max-width:1100px){.summary-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:1000px){.workflow{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.summary-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.workflow{grid-template-columns:1fr}.workflow-step p{min-height:auto}}
</style>
