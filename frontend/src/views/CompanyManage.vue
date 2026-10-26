<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>公司管理</h2><p class="muted">登记公司身份，按股票代码关联年报、索引与评价。</p></div><el-button :loading="loading" @click="refresh">刷新</el-button></div>
    <el-card class="section">
      <el-form :inline="true" @submit.prevent="create">
        <el-form-item label="股票代码"><el-input data-testid="company-code" v-model="form.stock_code" maxlength="9" placeholder="六位代码或交易所后缀" /></el-form-item>
        <el-form-item label="公司名称"><el-input data-testid="company-name" v-model="form.stock_name" maxlength="128" /></el-form-item>
        <el-form-item><el-button data-testid="company-save" type="primary" native-type="submit" :loading="saving" :disabled="!form.stock_code.trim() || !form.stock_name.trim()">保存公司</el-button></el-form-item>
      </el-form>
      <el-alert v-if="saveError" :title="saveError" type="error" :closable="false" />
      <p class="muted">公司与历史文档、评价运行保持关联。重复股票代码由服务端核对身份。</p>
    </el-card>
    <el-card class="section">
      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="搜索公司"><el-input data-testid="company-search" v-model="query" clearable maxlength="128" placeholder="股票代码或公司名称" style="width:min(340px,100%)" /></el-form-item>
        <el-form-item><el-button data-testid="company-search-submit" native-type="submit" :loading="loading">搜索</el-button></el-form-item>
      </el-form>
      <el-alert v-if="loadError" title="公司列表加载失败，请重试。当前筛选没有可展示的查询结果。" type="error" :closable="false" show-icon />
      <el-table data-testid="company-table" :data="companies" v-loading="loading" :empty-text="loadError ? '列表暂不可用' : query.trim() ? '没有匹配公司，请调整股票代码或名称' : '请先登记公司'">
        <el-table-column prop="stock_code" label="股票代码" width="150" />
        <el-table-column prop="stock_name" label="公司名称" min-width="150" />
        <el-table-column label="资料与评价" min-width="200"><template #default="{row}"><router-link :to="{path:'/documents',query:{company_id:row.id,stock_code:row.stock_code}}">导入年报</router-link><router-link class="link-gap" :to="{path:'/evaluations/new',query:{stock_code:row.stock_code}}">创建评价</router-link></template></el-table-column>
      </el-table>
      <p class="muted" data-testid="company-total">{{ loading || loadError ? '匹配数量暂不可用' : '共 '+total+' 家匹配公司；当前显示 '+companies.length+' 家' }}。搜索由服务端执行，结果按股票代码排序。</p>
      <el-pagination data-testid="company-pagination" v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[10,20,50,100,200]" :total="total" layout="total, sizes, prev, pager, next" :disabled="loading || loadError" @current-change="load" @size-change="resize" />
    </el-card>
  </div>
</template>
<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import request from '../api/request'

const form=reactive({stock_code:'',stock_name:''}),companies=ref([]),query=ref('')
const page=ref(1),pageSize=ref(20),total=ref(0),loading=ref(false),saving=ref(false),loadError=ref(false),saveError=ref('')
let alive=true,sequence=0,searchTimer=null
async function load(){
  if(!alive)return
  const current=++sequence
  const params={page:page.value,page_size:pageSize.value,...(query.value.trim() ? {q:query.value.trim()} : {})}
  loading.value=true;loadError.value=false;companies.value=[]
  try{
    const result=(await request.get('/companies',{params})).data
    if(!alive || current!==sequence)return
    companies.value=result.items;total.value=result.total
  }catch{
    if(alive && current===sequence)loadError.value=true
  }finally{
    if(alive && current===sequence)loading.value=false
  }
}
function search(){clearTimeout(searchTimer);page.value=1;load()}
function refresh(){clearTimeout(searchTimer);load()}
function resize(){page.value=1;load()}
watch(query,()=>{
  clearTimeout(searchTimer)
  // Invalidate an outstanding query immediately, before the next debounce fires.
  sequence++;companies.value=[];total.value=0;loadError.value=false;loading.value=true;page.value=1
  searchTimer=setTimeout(load,250)
})
async function create(){
  if(saving.value || !form.stock_code.trim() || !form.stock_name.trim())return
  saving.value=true;saveError.value=''
  try{
    await request.post('/companies',{stock_code:form.stock_code.trim(),stock_name:form.stock_name.trim()})
    if(!alive)return
    form.stock_code='';form.stock_name='';clearTimeout(searchTimer);page.value=1;await load();ElMessage.success('公司身份已保存')
  }catch{if(alive)saveError.value='公司未保存，请核对股票代码、名称及服务端提示后重试。'}finally{if(alive)saving.value=false}
}
onMounted(load)
onUnmounted(()=>{alive=false;sequence++;clearTimeout(searchTimer)})
</script>
<style scoped>.link-gap{margin-left:18px}</style>
