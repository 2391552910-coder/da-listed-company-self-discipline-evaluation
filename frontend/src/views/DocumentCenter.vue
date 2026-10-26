<template>
  <div class="documents">
    <h2>双源文档中心</h2>
    <el-alert title="阶段 2：导入、解析、索引与原文定位。生成评价与第三章指数尚待接入。" type="info" :closable="false" />
    <el-row :gutter="20" class="section">
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header>导入文档</template>
          <el-form label-position="top">
            <el-form-item label="来源"><el-radio-group v-model="form.corpus_type"><el-radio value="annual_report">公司年报</el-radio><el-radio value="regulation">法规</el-radio></el-radio-group></el-form-item>
            <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
            <template v-if="form.corpus_type === 'annual_report'">
              <el-form-item label="公司"><el-select v-model="form.company_id" placeholder="选择公司"><el-option v-for="c in companies" :key="c.id" :value="c.id" :label="c.stock_code + ' ' + c.stock_name" /></el-select></el-form-item>
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
          <el-button class="section" @click="createCompany">保存公司</el-button>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="14">
        <el-card>
          <template #header><span>文档与版本</span><el-button class="right" @click="loadDocuments">刷新</el-button></template>
          <el-table :data="documents" @row-click="selectDocument" highlight-current-row>
            <el-table-column prop="title" label="标题" /><el-table-column prop="corpus_type" label="来源" width="130" /><el-table-column prop="report_year" label="年度" width="80" />
          </el-table>
          <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadDocuments" />
          <div v-for="v in versions" :key="v.id" class="version">
            <strong>{{ v.version_label }} · {{ v.filename }}</strong>
            <p class="muted">{{ v.id }} · SHA256 {{ v.file_sha256 }}</p>
            <el-button size="small" @click="parse(v.id)">排队解析</el-button>
            <a :href="fileUrl(v.id)" target="_blank" rel="noopener">查看原件</a>
            <div v-for="p in v.parse_runs" :key="p.id" class="section">
              <el-checkbox v-if="['done','partial'].includes(p.status)" v-model="selectedParses" :value="p.id">用于索引</el-checkbox>
              <el-tag :type="p.status === 'failed' ? 'danger' : 'info'">{{ p.status }}</el-tag>
              <span class="muted">{{ p.error_code || p.id }}</span>
              <el-button v-if="['done','partial'].includes(p.status)" link @click="showChunks(v.id, p.id)">片段与位置</el-button>
            </div>
          </div>
          <el-empty v-if="!versions.length" description="选择文档查看版本" />
        </el-card>
        <el-card class="section">
          <template #header>研究索引与双源检索</template>
          <el-select v-model="profile"><el-option value="qwen_cuda" label="Qwen3 CUDA（正式检索档）" /><el-option value="bm25_diagnostic" label="BM25 离线诊断档" /></el-select>
          <el-button :disabled="selectedParses.length < 2" @click="buildIndex">构建所选解析批次</el-button>
          <p class="muted">索引须包含年报和法规。GPU 模型由独立 Worker 执行。</p>
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
      <el-table :data="chunks"><el-table-column prop="physical_page" label="物理页" width="80" /><el-table-column prop="content" label="原文片段" /><el-table-column width="100"><template #default="{row}"><el-button link @click="showSource(row.id)">核对位置</el-button></template></el-table-column></el-table>
    </el-dialog>
    <el-dialog v-model="sourceOpen" title="原文与定位核查" width="85%">
      <template v-if="source">
        <el-alert :title="source.source_available ? '原文件完整性与文本帧区间校验通过；未认证语义支持或法规适用性。' : '原文件不可用，正在显示历史文本快照。'" :type="source.source_available ? 'info' : 'warning'" :closable="false" />
        <p>{{ source.title }} · {{ source.physical_page ? '物理第'+source.physical_page+'页' : '正文字符区间' }} · [{{ source.char_start }}, {{ source.char_end }})</p>
        <p class="muted">来源：{{ source.text_origin }} · {{ source.metadata.identity_status }} · {{ source.metadata.applicability_status }}</p>
        <a v-if="source.source_available" :href="fileUrl(source.version_id) + (source.physical_page ? '#page='+source.physical_page : '')" target="_blank" rel="noopener">打开原件对应页</a>
        <pre class="frame">{{ source.frame_text.slice(0,source.char_start) }}<mark>{{ source.content }}</mark>{{ source.frame_text.slice(source.char_end) }}</pre>
      </template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import request from '../api/request'
const form = reactive({ corpus_type:'annual_report', title:'', company_id:null, report_year:2023, document_id:'', version_label:'1' })
const metadata=ref('{}'), file=ref(null), companies=ref([]), documents=ref([]), versions=ref([]), indexes=ref([])
const selectedParses=ref([]), indexId=ref(''), profile=ref('qwen_cuda'), page=ref(1), total=ref(0)
const companyCode=ref(''), companyName=ref(''), stockCode=ref(''), year=ref(2023), query=ref('')
const jobs=ref([]), results=ref(null), source=ref(null), sourceOpen=ref(false), chunksOpen=ref(false), chunks=ref([])
const uploading=ref(false), searching=ref(false), activeDocument=ref(null)
const fileUrl=id=>'/api/v1/document-versions/'+id+'/file'
const onFile=change=>{ file.value=change.raw }
async function loadCompanies(){ companies.value=(await request.get('/companies')).data.items }
async function loadDocuments(){ const r=(await request.get('/documents',{params:{page:page.value}})).data; documents.value=r.items;total.value=r.total;indexes.value=(await request.get('/indexes')).data }
async function selectDocument(row){activeDocument.value=row.id;versions.value=(await request.get('/documents/'+row.id+'/versions')).data}
async function createCompany(){await request.post('/companies',{stock_code:companyCode.value,stock_name:companyName.value});await loadCompanies();ElMessage.success('公司已保存')}
async function upload(){
  let parsed;try{parsed=JSON.parse(metadata.value)}catch{ElMessage.error('元数据不是合法JSON');return}
  const data=new FormData();data.append('file',file.value);data.append('corpus_type',form.corpus_type);data.append('title',form.title);data.append('version_label',form.version_label);data.append('metadata',JSON.stringify(parsed))
  if(form.document_id)data.append('document_id',form.document_id)
  if(form.corpus_type==='annual_report'){if(!form.company_id){ElMessage.error('请选择公司');return}data.append('company_id',form.company_id);data.append('report_year',form.report_year)}
  uploading.value=true;try{await request.post('/documents/upload',data);await loadDocuments();ElMessage.success('版本已保存')}finally{uploading.value=false}
}
async function parse(id){jobs.value.unshift((await request.post('/document-versions/'+id+'/parse',{})).data)}
async function buildIndex(){jobs.value.unshift((await request.post('/indexes',{parse_ids:selectedParses.value,profile:profile.value,rerank:true})).data)}
async function showChunks(id,parseId){chunks.value=(await request.get('/document-versions/'+id+'/chunks',{params:{parse_id:parseId}})).data.items;chunksOpen.value=true}
async function search(){searching.value=true;try{results.value=(await request.post('/retrieval/search',{index_id:indexId.value,query:query.value,stock_code:stockCode.value,report_year:year.value,top_k:3})).data}finally{searching.value=false}}
async function showSource(id){source.value=(await request.get('/chunks/'+id+'/source')).data;sourceOpen.value=true}
let timer=null
async function poll(){try{let changed=false;for(let i=0;i<jobs.value.length;i++){if(['queued','running'].includes(jobs.value[i].status)){const updated=(await request.get('/jobs/'+jobs.value[i].id)).data;changed ||= updated.status!==jobs.value[i].status;jobs.value[i]=updated}}if(changed){await loadDocuments();if(activeDocument.value)await selectDocument({id:activeDocument.value})}}finally{timer=setTimeout(poll,3000)}}
onMounted(async()=>{await Promise.all([loadCompanies(),loadDocuments()]);poll()})
onUnmounted(()=>clearTimeout(timer))
</script>
<style scoped>
.documents{max-width:1450px;margin:auto}.section{margin-top:16px}.right{float:right}.muted{color:#64748b;font-size:12px;overflow-wrap:anywhere}.version,.hit{border-bottom:1px solid #e2e8f0;padding:14px 0}.frame{white-space:pre-wrap;line-height:1.8;background:#f8fafc;padding:20px;max-height:60vh;overflow:auto}a{margin-left:12px;color:#2563eb}.el-select{min-width:220px;max-width:100%}mark{background:#fef08a}
</style>
