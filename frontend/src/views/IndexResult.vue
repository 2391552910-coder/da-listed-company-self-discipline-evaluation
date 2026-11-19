<template>
  <div class="page-shell">
    <div class="page-heading">
      <div><h2>主体自律指数</h2><p class="muted">使用已发布的标准化指标值和最终权重，按公司及报告年度查看结果。</p></div>
      <el-button :loading="checking" @click="checkStatus">刷新服务状态</el-button>
    </div>
    <el-card class="section" v-loading="checking">
      <el-alert v-if="state" :title="statusText" :type="available ? 'success' : 'info'" :closable="false" show-icon />
      <template v-if="available">
        <el-form label-position="top" class="section" @submit.prevent="loadResult">
          <el-row :gutter="16">
            <el-col :xs="24" :sm="12"><el-form-item label="股票代码"><el-input v-model="stockCode" maxlength="6" placeholder="六位股票代码" /></el-form-item></el-col>
            <el-col :xs="24" :sm="12"><el-form-item label="报告年度">
              <el-select v-model="year" class="full"><el-option v-for="item in state.years" :key="item" :value="item" :label="String(item)" /></el-select>
            </el-form-item></el-col>
          </el-row>
          <el-button type="primary" native-type="submit" :disabled="!valid" :loading="loading">查看指数</el-button>
        </el-form>
        <p v-if="lookupStatus && !result" class="muted" role="status">{{ stateLabels[lookupStatus] || '暂时无法读取指数结果' }}</p>
      </template>
      <div v-else-if="state" class="actions section">
        <router-link to="/experiments">进入实验与对照</router-link>
        <router-link to="/reports-export">进入报告导出</router-link>
      </div>
    </el-card>

    <el-card v-if="result" class="section" aria-live="polite">
      <template #header><strong>{{ result.stock_code }} · {{ result.report_year }} 年度</strong></template>
      <div class="measurement-score"><strong>{{ format(result.score) }}</strong><span> / 100 分</span></div>
      <p class="muted">数据版本 {{ snapshot.dataset_version }} · 权重版本 {{ snapshot.weights_version }}</p>
      <el-table :data="dimensions" class="section">
        <el-table-column prop="name" label="维度" min-width="180" />
        <el-table-column label="维度得分（满分 100）"><template #default="{ row }">{{ format(row.score) }}</template></el-table-column>
      </el-table>
      <el-collapse class="section">
        <el-collapse-item title="指标、权重和来源" name="indicators">
          <el-table :data="indicators">
            <el-table-column prop="code" label="指标" width="100" />
            <el-table-column label="标准化值"><template #default="{ row }">{{ format(row.value) }}</template></el-table-column>
            <el-table-column label="最终权重"><template #default="{ row }">{{ format(row.weight * 100) }}%</template></el-table-column>
          </el-table>
          <p class="muted hash">数据 SHA256 {{ snapshot.dataset_sha256 }}</p>
          <p class="muted hash">权重 SHA256 {{ snapshot.weights_sha256 }}</p>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import request from '../api/request'

const stateLabels = {
  ready: '测度服务可用，正式指标和权重已通过校验。',
  disabled: '指数测度服务未启用，文档、检索、实验和报告可照常使用。',
  unavailable: '指数测度服务暂不可达，文档、检索、实验和报告可照常使用。',
  inputs_missing: '等待接入正式指标数据和权重，可继续使用其他功能。',
  inputs_invalid: '指数输入校验未通过，请核对数据版本、指标和权重。',
  not_found: '所选公司和年度缺少正式指标记录。',
}
const dimensionNames = { D1: '信息披露质量', D2: '董事会治理有效性', D3: '内部控制与合规性', D4: '第三方声誉' }
const state = ref(null), snapshot = ref(null), stockCode = ref(''), year = ref(null)
const checking = ref(false), loading = ref(false), lookupStatus = ref('')
let lookupVersion = 0
const available = computed(() => state.value?.status === 'ready')
const statusText = computed(() => stateLabels[state.value?.status] || stateLabels.unavailable)
const valid = computed(() => available.value && /^[0-9]{6}$/.test(stockCode.value.trim()) && Number.isInteger(year.value))
const result = computed(() => snapshot.value?.result)
const dimensions = computed(() => Object.entries(result.value?.dimensions || {}).map(([key, score]) => ({ name: `${key} ${dimensionNames[key] || ''}`, score })))
const indicators = computed(() => Object.entries(result.value?.indicator_scores || {}).sort(([a], [b]) => a.localeCompare(b))
  .map(([code, value]) => ({ code, value, weight: result.value.weights[code] })))
const format = value => Number.isFinite(value) ? value.toFixed(2) : '—'

function clearResult() {
  lookupVersion++
  snapshot.value = null
  lookupStatus.value = ''
  loading.value = false
}
watch([stockCode, year], clearResult, { flush: 'sync' })

async function checkStatus() {
  clearResult()
  checking.value = true
  try {
    state.value = (await request.get('/measurement/status', { timeout: 5000 })).data
    const years = state.value.years || []
    if (!years.includes(year.value)) year.value = years.at(-1) ?? null
  } catch {
    state.value = { status: 'unavailable' }
  } finally {
    checking.value = false
  }
}

async function loadResult() {
  if (!valid.value || loading.value || checking.value) return
  clearResult()
  const version = lookupVersion
  loading.value = true
  try {
    const response = await request.get('/measurement/results/' + stockCode.value.trim(),
      { params: { report_year: year.value }, timeout: 5000 })
    if (version !== lookupVersion) return
    snapshot.value = response.data
    lookupStatus.value = response.data.status
    if (!['ready', 'not_found'].includes(response.data.status)) state.value = response.data
  } catch {
    if (version === lookupVersion) lookupStatus.value = 'unavailable'
  } finally {
    if (version === lookupVersion) loading.value = false
  }
}

onMounted(checkStatus)
</script>

<style scoped>
.measurement-score strong { font-size: 2rem; color: var(--el-color-primary); }
.hash { overflow-wrap: anywhere; }
</style>
