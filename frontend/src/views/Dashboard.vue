<template>
  <div>
    <h2>主体自律评价研究工作台</h2>
    <el-alert title="当前已提供阶段2文档与检索入口；第三章指数和生成评价尚未接入。" type="info" :closable="false" />
    <el-row :gutter="20" style="margin-top:24px">
      <el-col :span="8"><el-card><h3>{{ companyCount }}</h3><p>已录入公司</p></el-card></el-col>
      <el-col :span="8"><el-card><h3>{{ documentCount }}</h3><p>已登记文档</p></el-card></el-col>
      <el-col :span="8"><el-card><h3>{{ readyIndexes }}</h3><p>就绪索引版本</p></el-card></el-col>
    </el-row>
    <el-card style="margin-top:24px">
      <p>先登记公司并导入年报与法规，再解析、选择批次构建索引，最后分别检索两类证据并核对原文。</p>
      <el-button type="primary" @click="$router.push('/documents')">进入双源文档中心</el-button>
      <p style="color:#64748b">指数结果未接入。这里暂不显示指数雷达图或等级分布。</p>
    </el-card>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import request from '../api/request'
const companyCount=ref('—'),documentCount=ref('—'),readyIndexes=ref('—')
onMounted(async()=>{
  const [companies,documents,indexes]=await Promise.all([request.get('/companies'),request.get('/documents'),request.get('/indexes')])
  companyCount.value=companies.data.total
  documentCount.value=documents.data.total
  readyIndexes.value=indexes.data.filter(row=>row.status==='ready').length
})
</script>
