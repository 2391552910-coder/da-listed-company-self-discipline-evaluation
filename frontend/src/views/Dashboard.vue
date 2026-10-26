<template>
  <div class="page-shell">
    <div class="page-heading"><div><h2>主体自律评价研究工作台</h2><p class="muted">从双源文档到可回放评价，再到固定版本报告。</p></div><el-button :loading="loading" @click="load">刷新统计</el-button></div>
    <el-alert v-if="error" title="部分统计暂不可用，请检查服务后刷新。缺失项显示为 —。" type="warning" :closable="false" />
    <el-row :gutter="18" class="section">
      <el-col v-for="item in stats" :key="item.name" :xs="12" :md="6"><el-card><div class="stat-number">{{ item.value }}</div><p class="muted">{{ item.name }}</p></el-card></el-col>
    </el-row>
    <el-card><template #header>开始一次可追溯评价</template><div class="workflow"><div v-for="(step,index) in steps" :key="step.path" class="workflow-step"><span class="step-number">{{ index+1 }}</span><h3>{{ step.name }}</h3><p class="muted">{{ step.description }}</p><el-button :type="index===2 ? 'primary' : 'default'" @click="$router.push(step.path)">{{ step.action }}</el-button></div></div></el-card>
    <el-row :gutter="18" class="section"><el-col :xs="24" :lg="14"><el-card><template #header>最近评价运行</template><el-table :data="recentRuns" empty-text="暂无评价运行"><el-table-column label="公司与年度" min-width="130"><template #default="{row}">{{ row.stock_code }} · {{ row.report_year }}</template></el-table-column><el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ statusName(row.status) }}</el-tag></template></el-table-column><el-table-column label="运行详情" min-width="100"><template #default="{row}"><router-link :to="'/evaluations/'+row.id">{{ short(row.id) }}</router-link></template></el-table-column></el-table></el-card></el-col><el-col :xs="24" :lg="10"><el-card><template #header>模型与研究边界</template><p v-for="profile in profiles" :key="profile.name" class="model-profile"><el-tag :type="profile.available ? 'info' : 'warning'">{{ profile.available ? '配置可用' : '尚未配置' }}</el-tag> {{ profile.model || profile.name }}</p><p class="muted">模型支持判断、来源机械校验和人工复核分别展示。实际回答质量需结合引用原文核对。</p><el-alert title="第三章 AHP-Transformer 指数结果待接入，不显示演示分数。" type="info" :closable="false" /></el-card></el-col></el-row>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import request from '../api/request'
import { short, statusName, statusType } from '../utils/presentation'
const loading=ref(false),error=ref(false),companyCount=ref('—'),documentCount=ref('—'),readyIndexes=ref('—'),evaluationCount=ref('—'),recentRuns=ref([]),profiles=ref([])
const stats=computed(()=>[{name:'已登记公司',value:companyCount.value},{name:'双源文档',value:documentCount.value},{name:'就绪索引版本',value:readyIndexes.value},{name:'评价运行记录',value:evaluationCount.value}])
const steps=[{name:'公司与文档',description:'登记公司，导入完整年报与法规，保存原件和文档版本。',path:'/documents',action:'导入文档'},{name:'解析与索引',description:'核对解析来源，构建双源索引，在预算内准备证据。',path:'/contexts',action:'检索与核对'},{name:'单题或四维评价',description:'选择问题、模型与预算，运行后逐题核对论断和引用。',path:'/evaluations/new',action:'创建评价'},{name:'固定版本报告',description:'从已完成的结果快照生成完整 HTML 或中文 PDF。',path:'/reports-export',action:'查看报告'}]
async function load(){
  loading.value=true;error.value=false
  const responses=await Promise.allSettled([request.get('/companies'),request.get('/documents'),request.get('/indexes'),request.get('/evaluations',{params:{page:1,page_size:5}}),request.get('/model-profiles')])
  companyCount.value=responses[0].status==='fulfilled' ? responses[0].value.data.total : '—';documentCount.value=responses[1].status==='fulfilled' ? responses[1].value.data.total : '—';readyIndexes.value=responses[2].status==='fulfilled' ? responses[2].value.data.filter(row=>row.status==='ready').length : '—';evaluationCount.value=responses[3].status==='fulfilled' ? responses[3].value.data.total : '—';recentRuns.value=responses[3].status==='fulfilled' ? responses[3].value.data.items : [];profiles.value=responses[4].status==='fulfilled' ? responses[4].value.data.items : [];error.value=responses.some(response=>response.status==='rejected');loading.value=false
}
onMounted(load)
</script>
<style scoped>.stat-number{font-size:32px;color:#173a65;font-weight:650}.workflow{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}.workflow-step{padding:8px 12px}.workflow-step h3{font-size:15px}.step-number{display:inline-flex;width:29px;height:29px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:50%;align-items:center;justify-content:center;color:#2563eb;font-size:13px}.workflow-step p{min-height:52px}.model-profile{font-size:13px;overflow-wrap:anywhere}@media(max-width:1000px){.workflow{grid-template-columns:repeat(2,1fr)}}@media(max-width:550px){.workflow{grid-template-columns:1fr}.workflow-step p{min-height:auto}}</style>
