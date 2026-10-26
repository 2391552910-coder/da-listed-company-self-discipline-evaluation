<template>
  <el-table :data="scores || []" empty-text="暂无这一来源的评分，未评分不记为0">
    <el-table-column label="记录／回答" min-width="145"><template #default="{row}">{{ short(row.id) }}<br><span class="muted">{{ short(row.answer_id) }}</span></template></el-table-column>
    <el-table-column label="评分人／方法" min-width="160"><template #default="{row}">{{ row.reviewer }}<br><span class="muted">{{ row.method_version }}</span></template></el-table-column>
    <el-table-column label="量表" min-width="150"><template #default="{row}">{{ row.rubric_version }}</template></el-table-column>
    <el-table-column v-for="criterion in criteria" :key="criterion.key" :label="criterion.label" width="100"><template #default="{row}">{{ row.values?.[criterion.key] ?? '未评分' }}</template></el-table-column>
    <el-table-column label="来源及更正关系" min-width="150"><template #default="{row}">{{ row.source_kind==='manual' ? '人工' : '自动' }}<br><span v-if="row.supersedes_score_id" class="muted">更正 {{ short(row.supersedes_score_id) }}，原记录保留</span></template></el-table-column>
    <el-table-column label="说明／版本" min-width="190"><template #default="{row}">{{ row.comment || '—' }}<br><span class="muted hash">回答哈希 {{ row.answer_sha256 || row.answer_snapshot_sha256 || '见冻结评分记录' }}</span><br><span class="muted">{{ formatDate(row.created_at) }}</span></template></el-table-column>
  </el-table>
</template>
<script setup>
import { formatDate, short } from '../utils/presentation'
defineProps({scores:Array})
const criteria=[{key:'faithfulness',label:'忠实性'},{key:'coverage',label:'覆盖'},{key:'professionalism',label:'专业性'},{key:'citation_quality',label:'引用质量'}]
</script>
