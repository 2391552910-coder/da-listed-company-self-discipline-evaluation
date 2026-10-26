<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>公司管理</h2><p class="muted">登记公司身份，按股票代码关联年报、索引与评价。</p></div><el-button :loading="loading" @click="load">刷新</el-button></div>
    <el-card class="section"><el-form :inline="true" @submit.prevent><el-form-item label="股票代码"><el-input v-model="form.stock_code" maxlength="9" placeholder="六位代码或交易所后缀" /></el-form-item><el-form-item label="公司名称"><el-input v-model="form.stock_name" maxlength="128" /></el-form-item><el-form-item><el-button type="primary" :loading="saving" :disabled="!form.stock_code.trim() || !form.stock_name.trim()" @click="create">保存公司</el-button></el-form-item></el-form><p class="muted">公司与历史文档、评价运行保持关联。重复股票代码由服务端核对身份。</p></el-card>
    <el-card class="section"><el-input v-model="query" clearable placeholder="筛选股票代码或公司名称" style="max-width:340px" /><el-table :data="filtered" class="section" v-loading="loading" empty-text="请先登记公司"><el-table-column prop="stock_code" label="股票代码" width="150" /><el-table-column prop="stock_name" label="公司名称" /><el-table-column label="资料与评价" min-width="200"><template #default="{row}"><router-link :to="{path:'/documents',query:{company_id:row.id,stock_code:row.stock_code}}">导入年报</router-link><router-link class="link-gap" :to="{path:'/evaluations/new',query:{stock_code:row.stock_code}}">创建评价</router-link></template></el-table-column></el-table><p class="muted">{{ filtered.length }} / {{ companies.length }} 家已登记公司</p></el-card>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '../api/request'
const form=reactive({stock_code:'',stock_name:''}),companies=ref([]),query=ref(''),loading=ref(false),saving=ref(false)
const filtered=computed(()=>companies.value.filter(row=>(row.stock_code+' '+row.stock_name).toLowerCase().includes(query.value.toLowerCase().trim())))
async function load(){loading.value=true;try{companies.value=(await request.get('/companies')).data.items}finally{loading.value=false}}
async function create(){saving.value=true;try{await request.post('/companies',{stock_code:form.stock_code.trim(),stock_name:form.stock_name.trim()});form.stock_code='';form.stock_name='';await load();ElMessage.success('公司身份已保存')}finally{saving.value=false}}
onMounted(load)
</script>
<style scoped>.link-gap{margin-left:18px}</style>
