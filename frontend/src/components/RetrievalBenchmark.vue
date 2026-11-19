<template>
  <el-card class="section benchmark">
    <template #header><strong>检索方法对照</strong></template>
    <el-form label-position="top" @submit.prevent="create">
      <el-row :gutter="16">
        <el-col :xs="24" :md="12"><el-form-item label="实验名称"><el-input v-model="name" maxlength="160" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="冻结索引">
          <el-select v-model="indexId" class="full">
            <el-option v-for="index in ready" :key="index.id" :label="index.profile + ' · ' + index.id" :value="index.id" />
          </el-select>
        </el-form-item></el-col>
      </el-row>
      <el-form-item label="检索方法">
        <el-checkbox-group v-model="methods">
          <el-checkbox v-for="(label, method) in methodNames" :key="method" :value="method"
            :disabled="method !== 'bm25' && selectedIndex?.profile !== 'qwen_cuda'">{{ label }}</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-row :gutter="16">
        <el-col :xs="24" :sm="8"><el-form-item label="返回 Top K"><el-input-number v-model="topK" :min="1" :max="20" /></el-form-item></el-col>
        <el-col :xs="24" :sm="8"><el-form-item label="每路候选上限"><el-input-number v-model="candidateK" :min="topK || 1" :max="100" /></el-form-item></el-col>
        <el-col :xs="24" :sm="8"><el-form-item label="任务时限（秒）"><el-input-number v-model="timeout" :min="1" :max="7200" /></el-form-item></el-col>
      </el-row>
      <el-form-item label="人工标注题集（JSON 数组）">
        <el-input v-model="source" type="textarea" :rows="5" :placeholder="example" />
        <div class="actions">
          <el-button link type="primary" @click="saveFile('retrieval-cases.json', example, 'application/json')">下载题集模板</el-button>
          <label class="file-label">导入 JSON <input type="file" accept=".json,application/json" @change="loadFile" /></label>
        </div>
        <p class="muted">填入该索引中实际的年报片段 ID。负例可选，提供正例和负例后才计算已标注准确率。</p>
      </el-form-item>
      <el-button native-type="submit" type="primary" :disabled="!canCreate" :loading="submitting">运行检索对照</el-button>
    </el-form>
    <div v-if="job" class="section" aria-live="polite">
      <p>任务 {{ job.id }} · {{ statusName(job.status) }}<span v-if="job.error_code"> · {{ job.error_code }}</span></p>
      <p v-if="running && job.progress?.total" class="muted">已完成 {{ job.progress.completed }} / {{ job.progress.total }} 个对照单元</p>
      <el-button v-if="running" @click="cancel">取消任务</el-button>
      <template v-if="job.result">
        <p class="muted">按成功题目宏平均。查看各组已评测分母后再比较方法。</p>
        <el-table :data="job.result.summary">
          <el-table-column label="方法" min-width="190"><template #default="{row}">{{ methodNames[row.method] }}</template></el-table-column>
          <el-table-column label="维度" width="75"><template #default="{row}">{{ row.dimension === 'all' ? '总体' : row.dimension }}</template></el-table-column>
          <el-table-column label="已评测 / 计划" min-width="110"><template #default="{row}">{{ row.scored }} / {{ row.planned }}</template></el-table-column>
          <el-table-column v-for="(label,key) in metricNames" :key="key" :label="label" min-width="105">
            <template #default="{row}">{{ row[key] == null ? '—' : (row[key] * 100).toFixed(1) + '%' }}</template>
          </el-table-column>
        </el-table>
        <div class="actions section">
          <el-button v-for="(label,format) in formats" :key="format" @click="download(format)">{{ label }}</el-button>
        </div>
      </template>
    </div>
  </el-card>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api/request'
import { newRequestKey } from '../utils/idempotency'
import { statusName } from '../utils/presentation'

const props = defineProps({ indexes: { type: Array, required: true } })
const route = useRoute(), router = useRouter()
const methodNames = { bm25: 'BM25', dense: '向量检索', rrf: 'BM25 + 向量（RRF）',
  bm25_rerank: 'BM25 + 重排', rrf_rerank: 'BM25 + 向量（RRF）+ 重排' }
const metricNames = { precision: '精确率', recall: '召回率', hit_rate: '命中率', mrr: 'MRR', judged_accuracy: '已标注准确率' }
const formats = { md: '下载 Markdown 报告', csv: '下载 CSV 指标', json: '下载完整 JSON' }
const example = JSON.stringify([{ key: 'annual-01', dimension: 'D2', query: '审计委员会召开了几次会议？',
  stock_code: '000001', report_year: 2024, relevant_chunk_ids: ['替换为实际片段ID'], irrelevant_chunk_ids: [] }], null, 2)
const name = ref(''), indexId = ref(''), methods = ref(['bm25']), source = ref('')
const topK = ref(10), candidateK = ref(50), timeout = ref(1800), job = ref(null), submitting = ref(false)
const ready = computed(() => props.indexes.filter(index => index.status === 'ready'))
const selectedIndex = computed(() => ready.value.find(index => index.id === indexId.value))
const running = computed(() => ['queued', 'running'].includes(job.value?.status))
const canCreate = computed(() => Boolean(name.value.trim() && selectedIndex.value && methods.value.length && source.value.trim()
  && Number.isInteger(topK.value) && Number.isInteger(candidateK.value) && candidateK.value >= topK.value
  && Number.isInteger(timeout.value) && !running.value))
let timer, searchVersion = 0, pending
watch(selectedIndex, index => { if (index?.profile !== 'qwen_cuda') methods.value = methods.value.filter(m => m === 'bm25') })
watch(() => route.query.benchmark_id, id => {
  clearTimeout(timer)
  const version = ++searchVersion
  if (typeof id === 'string' && id) refresh(id, version)
  else job.value = null
}, { immediate: true })
onBeforeUnmount(() => { searchVersion++; clearTimeout(timer) })

async function refresh(id, version) {
  try {
    const response = await request.get('/retrieval-benchmarks/' + encodeURIComponent(id))
    if (version !== searchVersion) return
    job.value = response.data
    if (running.value) timer = setTimeout(() => refresh(id, version), 2000)
  } catch { /* API client displays errors. */ }
}

async function create() {
  if (!canCreate.value || submitting.value) return
  let cases
  try { cases = JSON.parse(source.value) } catch { ElMessage.error('题集不是有效的 JSON'); return }
  if (!Array.isArray(cases) || !cases.length) { ElMessage.error('请输入非空题集数组'); return }
  const payload = { name: name.value.trim(), index_id: indexId.value, methods: [...methods.value],
    top_k: topK.value, candidate_k: candidateK.value, max_runtime_seconds: timeout.value, cases }
  const signature = JSON.stringify(payload)
  if (pending?.signature !== signature) pending = { signature, key: newRequestKey() }
  submitting.value = true
  try {
    const response = await request.post('/retrieval-benchmarks', payload, { headers: { 'Idempotency-Key': pending.key } })
    pending = null
    job.value = response.data
    await router.replace({ query: { ...route.query, benchmark_id: response.data.id } })
  } catch { /* API client displays errors. */ }
  finally { submitting.value = false }
}

async function loadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try { source.value = await file.text() } catch { ElMessage.error('无法读取题集文件') }
  event.target.value = ''
}

async function cancel() {
  try {
    await request.post('/jobs/' + encodeURIComponent(job.value.id) + '/cancel')
    clearTimeout(timer)
    await refresh(job.value.id, ++searchVersion)
  } catch { /* API client displays errors. */ }
}

function saveFile(name, content, type) {
  const blob = content instanceof Blob ? content : new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function download(format) {
  const id = job.value.id
  try {
    const response = await fetch('/api/v1/retrieval-benchmarks/' + encodeURIComponent(id) + '/report?format=' + format)
    if (!response.ok) throw new Error('报告下载失败')
    saveFile('retrieval-' + id + '.' + format, await response.blob())
  } catch (error) { ElMessage.error(error.message) }
}
</script>

<style scoped>
.file-label { cursor: pointer; color: var(--el-color-primary); }
.file-label input { max-width: 13rem; margin-inline-start: .5rem; }
.benchmark p { margin-block: .5rem; }
</style>
