<template>
  <div class="documents">
    <div class="page-heading"><h2>双源文档中心</h2><div class="actions"><el-button @click="goEvaluate">进入评价工作台</el-button><el-button @click="$router.push('/tasks')">任务中心</el-button></div></div>
    <el-alert title="导入完整年报与法规、保留版本并核对原文位置。索引就绪后可进入单题或四维评价。" type="info" :closable="false" />
    <el-row :gutter="20" class="section">
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header>导入文档</template>
          <el-form label-position="top">
            <el-form-item label="来源"><el-radio-group v-model="form.corpus_type"><el-radio value="annual_report">公司年报</el-radio><el-radio value="regulation">法规</el-radio></el-radio-group></el-form-item>
            <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
            <template v-if="form.corpus_type === 'annual_report'">
              <el-form-item label="公司"><CompanySelect v-model="form.company_id" value-field="id" data-testid="document-upload-company" placeholder="搜索股票代码或公司名称" /></el-form-item>
              <el-form-item label="报告年度"><el-input-number v-model="form.report_year" :min="1990" :max="2100" /></el-form-item>
            </template>
            <el-form-item label="文档 ID（增加法规版本时填写）"><el-input v-model="form.document_id" placeholder="新文档留空" /></el-form-item>
            <el-form-item label="版本标签"><el-input v-model="form.version_label" /></el-form-item>
            <el-form-item label="法规元数据 JSON（未知值留空）"><el-input v-model="metadata" type="textarea" :rows="3" /></el-form-item>
            <el-upload :auto-upload="false" :limit="1" :on-change="onFile" :on-remove="() => file = null" accept=".pdf,.txt,.docx"><el-button>选择原文件</el-button></el-upload>
            <el-button class="section" type="primary" :loading="uploading" :disabled="!file || !form.title" @click="upload">上传并保留版本</el-button>
          </el-form>
          <el-divider>录入公司</el-divider>
          <el-input v-model="companyCode" placeholder="六位股票代码" />
          <el-input class="section" v-model="companyName" placeholder="公司名称" />
          <el-button class="section" :loading="savingCompany" :disabled="!companyCode.trim() || !companyName.trim()" @click="createCompany">保存公司</el-button>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="14">
        <el-card>
          <template #header><span>文档与版本</span><el-button class="right" @click="loadDocuments">刷新</el-button></template>
          <el-form :inline="true" class="document-filters" @submit.prevent="applyFilters">
            <el-form-item label="来源"><el-select v-model="filters.corpus_type" clearable data-testid="document-filter-source" placeholder="全部来源"><el-option value="annual_report" label="公司年报" /><el-option value="regulation" label="法规" /></el-select></el-form-item>
            <el-form-item label="公司"><CompanySelect v-model="filters.company_id" value-field="id" :disabled="filters.corpus_type === 'regulation'" data-testid="document-filter-company" placeholder="全部公司（仅年报）" /></el-form-item>
            <el-form-item label="年度"><el-input-number v-model="filters.report_year" :disabled="filters.corpus_type === 'regulation'" :min="1990" :max="2100" :value-on-clear="null" controls-position="right" data-testid="document-filter-year" placeholder="全部年度" /></el-form-item>
            <el-form-item><el-button native-type="submit" type="primary" :loading="documentsLoading" data-testid="document-filter-apply">查询文档</el-button><el-button @click="resetFilters" data-testid="document-filter-reset">重置</el-button></el-form-item>
          </el-form>
          <p class="muted">公司与年度筛选仅用于年报。法规属于共享资料库，选择法规时不按公司或报告年度过滤。</p>
          <el-alert v-if="documentsError" title="文档列表加载失败，请重试。" type="error" :closable="false" />
          <el-table :data="documents" v-loading="documentsLoading" @row-click="selectDocument" highlight-current-row data-testid="document-table" :empty-text="documentsError ? '本页加载失败' : '当前筛选下暂无文档'">
            <el-table-column prop="title" label="标题" /><el-table-column label="来源" width="110"><template #default="{row}">{{ row.corpus_type === 'annual_report' ? '公司年报' : '法规' }}</template></el-table-column><el-table-column prop="report_year" label="年度" width="80" />
          </el-table>
          <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" :disabled="documentsLoading" data-testid="document-pagination" @current-change="loadDocuments" />
          <el-alert v-if="versionsError" title="文档版本加载失败，请重新选择文档。" type="error" :closable="false" />
          <p v-if="versionsLoading" class="muted">正在读取所选文档的版本…</p>
          <div v-for="v in versions" :key="v.id" class="version">
            <strong>{{ v.version_label }} · {{ v.filename }}</strong>
            <p class="muted">{{ v.id }} · SHA256 {{ v.file_sha256 }}</p>
            <el-button size="small" @click="parse(v.id)">排队解析</el-button>
            <a :href="fileUrl(v.id)" target="_blank" rel="noopener">查看原件</a>
            <div v-for="p in v.parse_runs" :key="p.id" class="section">
              <el-checkbox v-if="['done','partial'].includes(p.status)" v-model="selectedParses" :value="p.id">用于索引</el-checkbox>
              <el-tag :type="p.status === 'failed' ? 'danger' : 'info'">{{ p.status }}</el-tag>
              <span class="muted">{{ p.error_code || p.id }}</span>
              <el-button v-if="['done','partial'].includes(p.status)" link :data-testid="'chunks-open-'+p.id" @click="showChunks(v.id, p.id)">片段与位置</el-button>
            </div>
          </div>
          <el-empty v-if="!versions.length && !versionsLoading && !versionsError" description="选择文档查看版本" />
        </el-card>
        <el-card class="section">
          <template #header>研究索引与双源检索</template>
          <el-select v-model="profile"><el-option value="qwen_cuda" label="Qwen3 CUDA（正式检索档）" /><el-option value="bm25_diagnostic" label="BM25 离线诊断档" /></el-select>
          <el-button :disabled="selectedParses.length < 2" @click="buildIndex">构建所选解析批次</el-button>
          <p class="muted">索引须包含年报和法规。GPU 模型由独立 Worker 执行。已选择 {{ selectedParses.length }} 个解析批次。</p>
          <el-alert v-if="indexesError" title="索引列表加载失败。" type="warning" :closable="false"><el-button link @click="loadIndexes">重新读取索引</el-button></el-alert>
          <el-select v-model="indexId" placeholder="选择 ready 索引"><el-option v-for="i in indexes" :key="i.id" :value="i.id" :disabled="i.status !== 'ready'" :label="i.profile + ' · ' + i.status + ' · ' + i.id.slice(0,8)" /></el-select>
          <el-input class="section" v-model="stockCode" placeholder="指定公司六位股票代码" />
          <el-input-number class="section" v-model="year" :min="1990" :max="2100" />
          <el-input class="section" v-model="query" type="textarea" placeholder="输入评价问题" />
          <el-button class="section" :loading="searching" :disabled="!indexId || !stockCode || !query" @click="search">分别检索两类证据</el-button>
          <el-alert v-if="results?.warning" :title="results.warning" type="warning" :closable="false" />
          <div v-for="role in ['annual_report','regulation']" :key="role">
            <h4>{{ role === 'annual_report' ? '公司年报证据' : '法规依据' }}</h4>
            <div v-for="hit in results?.[role] || []" :key="hit.chunk_id" class="hit">
              <el-button link @click="showSource(hit.chunk_id)">{{ hit.title }} · {{ hit.physical_page ? '物理第'+hit.physical_page+'页' : '正文字符区间' }}</el-button>
              <p>{{ hit.content.slice(0,220) }}…</p>
            </div>
          </div>
        </el-card>
        <el-card v-if="jobs.length" class="section">
          <template #header>持久化任务</template>
          <p v-for="j in jobs" :key="j.id">{{ j.kind }} · {{ j.status }} · {{ j.error_code || j.id }}</p>
        </el-card>
      </el-col>
    </el-row>
    <el-dialog v-model="chunksOpen" title="解析片段" width="80%">
      <p class="muted hash" data-testid="chunks-identity">版本 {{ chunksContext?.version_id }}；解析批次 {{ chunksContext?.parse_id }}</p>
      <el-alert v-if="chunksError" :title="'第 '+chunksPage+' 页片段加载失败，未显示上一页内容。'" type="error" :closable="false"><el-button link @click="loadChunks" data-testid="chunks-retry">重新加载本页</el-button></el-alert>
      <el-table :data="chunks" v-loading="chunksLoading" data-testid="chunks-table" :empty-text="chunksError ? '本页加载失败' : '当前解析批次暂无片段'"><el-table-column prop="physical_page" label="物理页" width="80" /><el-table-column prop="content" label="原文片段" /><el-table-column width="100"><template #default="{row}"><el-button link @click="showSource(row.id)">核对位置</el-button></template></el-table-column></el-table>
      <el-pagination class="section" v-model:current-page="chunksPage" :total="chunksTotal" :page-size="20" layout="total, prev, pager, next" :disabled="chunksLoading" data-testid="chunks-pagination" @current-change="loadChunks" />
    </el-dialog>
    <el-dialog v-model="sourceOpen" title="原文与定位核查" width="85%">
      <p v-if="sourceLoading" class="muted">正在校验原文与定位…</p>
      <el-alert v-if="sourceError" title="原文加载或校验失败，请重新点击核对位置。" type="error" :closable="false" />
      <template v-if="source">
        <el-alert :title="source.source_available ? '原文件完整性与文本帧区间校验通过；未认证语义支持或法规适用性。' : '原文件不可用，正在显示历史文本快照。'" :type="source.source_available ? 'info' : 'warning'" :closable="false" />
        <p>{{ source.title }} · {{ source.physical_page ? '物理第'+source.physical_page+'页' : '正文字符区间' }} · [{{ source.char_start }}, {{ source.char_end }})</p>
        <p class="muted">来源：{{ source.text_origin }} · {{ source.metadata?.identity_status }} · {{ source.metadata?.applicability_status }}</p>
        <a v-if="source.source_available" :href="fileUrl(source.version_id) + (source.physical_page ? '#page='+source.physical_page : '')" target="_blank" rel="noopener">打开原件对应页</a>
        <pre class="frame">{{ source.frame_text.slice(0,source.char_start) }}<mark>{{ source.content }}</mark>{{ source.frame_text.slice(source.char_end) }}</pre>
      </template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import request from '../api/request'
import CompanySelect from '../components/CompanySelect.vue'
const route=useRoute(),router=useRouter()
const form = reactive({ corpus_type:'annual_report', title:'', company_id:null, report_year:2023, document_id:'', version_label:'1' })
const metadata=ref('{}'), file=ref(null), documents=ref([]), versions=ref([]), indexes=ref([])
const selectedParses=ref([]), indexId=ref(''), profile=ref('qwen_cuda'), page=ref(1), total=ref(0)
const filters=reactive({corpus_type:'',company_id:null,report_year:null})
const appliedFilters=ref({})
const companyCode=ref(''), companyName=ref(''), stockCode=ref(''), year=ref(2023), query=ref('')
const jobs=ref([]), results=ref(null), source=ref(null), sourceOpen=ref(false), chunksOpen=ref(false), chunks=ref([])
const chunksContext=ref(null),chunksPage=ref(1),chunksTotal=ref(0),chunksLoading=ref(false),chunksError=ref(false)
const uploading=ref(false), searching=ref(false), savingCompany=ref(false), activeDocument=ref(null)
const documentsLoading=ref(false),documentsError=ref(false),versionsLoading=ref(false),versionsError=ref(false),indexesError=ref(false),sourceLoading=ref(false),sourceError=ref(false)
let alive=true,mounted=false,timer=null,listSequence=0,versionSequence=0,chunksSequence=0,sourceSequence=0,indexSequence=0
const fileUrl=id=>'/api/v1/document-versions/'+id+'/file'
const onFile=change=>{ file.value=change.raw }
watch(()=>filters.corpus_type,value=>{if(value==='regulation'){filters.company_id=null;filters.report_year=null}},{flush:'sync'})
watch(()=>filters.company_id,value=>{if(value && filters.corpus_type!=='regulation')filters.corpus_type='annual_report'},{flush:'sync'})
watch(()=>filters.report_year,value=>{if(Number.isInteger(value) && filters.corpus_type!=='regulation')filters.corpus_type='annual_report'},{flush:'sync'})
watch(chunksOpen,value=>{if(!value){chunksSequence++;chunksLoading.value=false}})
function clearDocumentSelection(){activeDocument.value=null;versions.value=[];versionsLoading.value=false;versionsError.value=false;versionSequence++}
async function loadDocuments(){
  const sequence=++listSequence
  documentsLoading.value=true;documentsError.value=false;documents.value=[]
  try{
    const result=(await request.get('/documents',{params:{...appliedFilters.value,page:page.value,page_size:20}})).data
    if(!alive || sequence!==listSequence)return
    documents.value=result.items;total.value=result.total
    if(activeDocument.value && !result.items.some(row=>row.id===activeDocument.value))clearDocumentSelection()
  }catch{if(alive && sequence===listSequence){documentsError.value=true;clearDocumentSelection()}}
  finally{if(alive && sequence===listSequence)documentsLoading.value=false}
}
async function applyFilters(){
  const params={}
  if(filters.corpus_type)params.corpus_type=filters.corpus_type
  if(filters.corpus_type!=='regulation'){
    if(filters.company_id){params.company_id=filters.company_id;params.corpus_type='annual_report'}
    if(Number.isInteger(filters.report_year)){params.report_year=filters.report_year;params.corpus_type='annual_report'}
  }
  appliedFilters.value=params;page.value=1;clearDocumentSelection();await loadDocuments()
}
async function resetFilters(){filters.corpus_type='';filters.company_id=null;filters.report_year=null;await applyFilters()}
async function loadIndexes(){
  const sequence=++indexSequence
  indexesError.value=false
  try{const result=(await request.get('/indexes')).data;if(alive && sequence===indexSequence)indexes.value=result}
  catch{if(alive && sequence===indexSequence){indexes.value=[];indexesError.value=true}}
}
async function selectDocument(row){
  const sequence=++versionSequence
  activeDocument.value=row.id;versions.value=[];versionsError.value=false;versionsLoading.value=true
  try{const result=(await request.get('/documents/'+row.id+'/versions')).data;if(alive && sequence===versionSequence)versions.value=result}
  catch{if(alive && sequence===versionSequence)versionsError.value=true}
  finally{if(alive && sequence===versionSequence)versionsLoading.value=false}
}
async function createCompany(){
  savingCompany.value=true
  try{const company=(await request.post('/companies',{stock_code:companyCode.value.trim(),stock_name:companyName.value.trim()})).data;if(!alive)return;form.corpus_type='annual_report';form.company_id=company.id;companyCode.value='';companyName.value='';ElMessage.success('公司已保存并选为上传对象')}
  finally{if(alive)savingCompany.value=false}
}
async function upload(){
  let parsed;try{parsed=JSON.parse(metadata.value)}catch{ElMessage.error('元数据不是合法JSON');return}
  const data=new FormData();data.append('file',file.value);data.append('corpus_type',form.corpus_type);data.append('title',form.title);data.append('version_label',form.version_label);data.append('metadata',JSON.stringify(parsed))
  if(form.document_id)data.append('document_id',form.document_id)
  if(form.corpus_type==='annual_report'){if(!form.company_id){ElMessage.error('请选择公司');return}data.append('company_id',form.company_id);data.append('report_year',form.report_year)}
  uploading.value=true;try{await request.post('/documents/upload',data);if(alive){await loadDocuments();ElMessage.success('版本已保存；文档列表保持当前筛选条件')}}finally{if(alive)uploading.value=false}
}
async function parse(id){const result=(await request.post('/document-versions/'+id+'/parse',{})).data;if(alive)jobs.value.unshift(result)}
async function buildIndex(){const result=(await request.post('/indexes',{parse_ids:selectedParses.value,profile:profile.value,rerank:true})).data;if(alive)jobs.value.unshift(result)}
async function showChunks(id,parseId){
  chunksContext.value={version_id:id,parse_id:parseId};chunksPage.value=1;chunksTotal.value=0;chunks.value=[];chunksError.value=false;chunksOpen.value=true
  await loadChunks()
}
async function loadChunks(){
  if(!chunksContext.value)return
  const sequence=++chunksSequence,context={...chunksContext.value},requestedPage=chunksPage.value
  chunksLoading.value=true;chunksError.value=false;chunks.value=[]
  try{
    const result=(await request.get('/document-versions/'+context.version_id+'/chunks',{params:{parse_id:context.parse_id,page:requestedPage,page_size:20}})).data
    if(!alive || sequence!==chunksSequence || !chunksOpen.value)return
    chunks.value=result.items;chunksTotal.value=result.total
  }catch{if(alive && sequence===chunksSequence && chunksOpen.value)chunksError.value=true}
  finally{if(alive && sequence===chunksSequence)chunksLoading.value=false}
}
async function search(){searching.value=true;try{const result=(await request.post('/retrieval/search',{index_id:indexId.value,query:query.value,stock_code:stockCode.value,report_year:year.value,top_k:3})).data;if(alive)results.value=result}finally{if(alive)searching.value=false}}
async function showSource(id){
  const sequence=++sourceSequence
  source.value=null;sourceOpen.value=true;sourceError.value=false;sourceLoading.value=true
  try{const result=(await request.get('/chunks/'+id+'/source')).data;if(alive && sequence===sourceSequence)source.value=result}
  catch{if(alive && sequence===sourceSequence)sourceError.value=true}
  finally{if(alive && sequence===sourceSequence)sourceLoading.value=false}
}
async function poll(){
  try{
    let changed=false
    const pending=jobs.value.filter(job=>['queued','running'].includes(job.status)).map(job=>({id:job.id,status:job.status}))
    for(const previous of pending){
      if(!alive)return
      const updated=(await request.get('/jobs/'+previous.id)).data
      if(!alive)return
      const position=jobs.value.findIndex(job=>job.id===previous.id)
      if(position!==-1){changed ||= updated.status!==previous.status;jobs.value[position]=updated}
    }
    if(changed && alive){await Promise.all([loadDocuments(),loadIndexes()]);if(alive && activeDocument.value)await selectDocument({id:activeDocument.value})}
  }catch{/* The HTTP adapter displays the failure; the next poll can recover. */}
  finally{if(alive)timer=setTimeout(poll,3000)}
}
function goEvaluate(){router.push({path:'/evaluations/new',query:{report_year:year.value,...(indexId.value ? {index_id:indexId.value} : {}),...(stockCode.value ? {stock_code:stockCode.value} : {})}})}
function applyRoute(){
  const company=Number(route.query.company_id),reportYear=Number(route.query.report_year)
  filters.corpus_type='';filters.company_id=null;filters.report_year=null
  if(Number.isSafeInteger(company) && company>0){form.company_id=company;filters.company_id=company}
  if(Number.isInteger(reportYear) && reportYear>=1990 && reportYear<=2100){form.report_year=reportYear;year.value=reportYear;filters.report_year=reportYear}
  if(typeof route.query.stock_code==='string')stockCode.value=route.query.stock_code
}
watch(()=>route.query,()=>{if(mounted && alive){applyRoute();applyFilters()}})
onMounted(async()=>{mounted=true;applyRoute();await Promise.all([applyFilters(),loadIndexes()]);if(alive)poll()})
onUnmounted(()=>{alive=false;clearTimeout(timer);listSequence++;versionSequence++;chunksSequence++;sourceSequence++;indexSequence++})
</script>
<style scoped>
.document-filters .el-form-item{margin-right:14px}.document-filters :deep(.el-select){min-width:170px;width:210px}.documents{max-width:1450px;margin:auto}.section{margin-top:16px}.right{float:right}.muted{color:#64748b;font-size:12px;overflow-wrap:anywhere}.version,.hit{border-bottom:1px solid #e2e8f0;padding:14px 0}.frame{white-space:pre-wrap;line-height:1.8;background:#f8fafc;padding:20px;max-height:60vh;overflow:auto}a{margin-left:12px;color:#2563eb}.el-select{min-width:220px;max-width:100%}mark{background:#fef08a}
</style>
