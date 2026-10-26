<template>
  <div data-testid="agent-trace" v-if="trace">
    <el-alert title="Agent 轨迹记录实际规划与调用。核验仍是模型判断，不等于独立语义核实，也不证明优于固定流程。" type="info" :closable="false" />
    <p class="muted">协议 {{ trace.protocol_version || trace.schema_version }} · 本题已用补检索 {{ trace.supplemental_calls_used ?? '未知' }} 次 · 修订 {{ trace.revised===true ? '已执行' : '未执行' }} · 停止原因 {{ stopName(trace.stop_reason) }}</p>
    <el-alert v-if="trace.final_verification_status==='not_reverified_after_revision'" title="此答案已修订，但未再次发起模型核验；修订不能继承草稿核验结论。" type="warning" :closable="false" />
    <el-collapse>
      <el-collapse-item v-for="(step,index) in trace.steps || []" :key="step.key || index" :name="step.key || index" :title="(index+1)+' · '+stepName(step.kind)+' · '+statusName(step.status)">
        <div class="run-banner"><span>步骤 {{ step.key || index+1 }}</span><span>模型调用 {{ step.model_call?.call_number ?? '未调用' }}</span><span>报告用量 {{ step.model_call?.usage?.status==='reported' ? step.model_call.usage.total_tokens+' tokens' : '未报告／未知' }}</span></div>
        <p v-if="step.decision" class="text-panel">{{ typeof step.decision==='string' ? step.decision : pretty(step.decision) }}</p>
        <p v-if="step.error_code" class="muted">错误码 {{ step.error_code }}</p>
        <el-table v-if="step.context?.source_records?.length" :data="step.context.source_records" empty-text="此步没有新增完整证据">
          <el-table-column prop="evidence_id" label="本步编号" width="100" /><el-table-column label="来源" width="105"><template #default="{row}">{{ sourceRole(row.source_role) }}</template></el-table-column><el-table-column prop="title" label="来源标题" min-width="180" /><el-table-column label="位置" min-width="130"><template #default="{row}">{{ row.physical_page ? '物理页 '+row.physical_page : '文本帧' }} [{{ row.char_start }},{{ row.char_end }})</template></el-table-column><el-table-column width="90"><template #default="{row}"><el-button link @click="$emit('evidence',row)">查看</el-button></template></el-table-column>
        </el-table>
        <p v-if="step.context" class="muted">参考计数 {{ step.context.reference_token_count ?? '未知' }} · {{ step.context.budget_unit || step.context.tokenizer_identity?.budget_unit || '单位见冻结身份' }} · 上下文SHA256 {{ step.context_sha256 || step.context.context_sha256 }}</p>
        <el-collapse><el-collapse-item title="规划、检索、证据与预算快照" :name="'detail-'+index"><pre class="json-panel">{{ pretty({decision:step.decision,retrieval:step.retrieval,context:step.context,budget:step.budget,model_call:step.model_call}) }}</pre></el-collapse-item><el-collapse-item v-if="step.prompt" title="实际提示词与消息哈希" :name="'prompt-'+index"><pre class="json-panel">{{ pretty(step.prompt) }}</pre></el-collapse-item></el-collapse>
      </el-collapse-item>
    </el-collapse>
    <div v-if="trace.verification" class="section"><strong>草稿的模型核验记录</strong><p class="muted">此处未自动转为人工确认。</p><pre class="json-panel">{{ pretty(trace.verification) }}</pre></div>
    <el-collapse v-if="trace.final_budget" class="section"><el-collapse-item title="全运行累计预算与最终上下文哈希" name="final-budget"><pre class="json-panel">{{ pretty({final_budget:trace.final_budget,final_context_sha256:trace.final_context_sha256,final_verification_status:trace.final_verification_status}) }}</pre></el-collapse-item></el-collapse>
  </div>
</template>
<script setup>
import { pretty, sourceRole, statusName } from '../utils/presentation'
defineProps({trace:Object})
defineEmits(['evidence'])
const stepName=value=>({plan:'规划',planning:'规划',retrieve:'补检索',retrieval:'补检索',supplemental_retrieval:'补检索',draft:'草稿',verify:'模型核验',verification:'模型核验',revise:'修订',revision:'修订'}[value] || value || '步骤')
const stopName=value=>({completed:'计划完成',no_more_retrieval:'规划决定停止补检索',round_limit:'规划轮次用尽',budget_exhausted:'预算耗尽',cancelled:'已取消',verification_accepted:'核验未要求修订'}[value] || value || '尚未停止')
</script>
