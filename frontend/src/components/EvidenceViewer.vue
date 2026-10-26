<template>
  <el-dialog v-model="open" title="引用证据与来源核对" width="90%" class="evidence-dialog">
    <template v-if="record">
      <h3>{{ record.evidence_id }} · {{ record.title || '证据原文' }}</h3>
      <p>{{ sourceRole(record.source_role) }} · {{ record.physical_page ? '物理第 '+record.physical_page+' 页' : '正文文本帧' }} · 字符 [{{ record.char_start }}, {{ record.char_end }})</p>
      <el-alert title="冻结文本用于回放。字符区间、哈希或引文匹配通过，不代表论断已获语义支持；法规适用性与人工复核分别确认。" type="info" :closable="false" />
      <p v-if="quote" class="quote"><strong>模型引用原句</strong><br>{{ quote }}</p>
      <p class="muted hash">文件版本：{{ record.version_id || '—' }} · 片段：{{ record.chunk_id || '—' }}</p>
      <div class="actions">
        <a v-if="record.version_id" :href="originalFileUrl(record)" target="_blank" rel="noopener noreferrer">打开原始文件{{ record.physical_page ? '对应页' : '' }}</a>
        <el-button data-testid="evidence-verify-source" v-if="record.chunk_id" :loading="loading" @click="verify">重新核对来源位置</el-button>
      </div>
      <el-alert v-if="sourceError" :title="sourceError" type="warning" :closable="false" class="section" />
      <el-alert data-testid="evidence-source-status" v-if="source" :title="source.source_available ? '原件完整性与文本帧区间校验通过。' : '原文件不可用；下面保留冻结文本。'" :type="source.source_available ? 'info' : 'warning'" :closable="false" class="section" />
      <h4>冻结证据全文</h4><pre data-testid="evidence-content" class="text-panel">{{ record.content }}</pre>
      <el-collapse>
        <el-collapse-item v-if="source?.frame_text" title="服务端文本帧及命中区间" name="frame"><pre class="text-panel">{{ source.frame_text.slice(0,source.char_start) }}<mark>{{ source.content }}</mark>{{ source.frame_text.slice(source.char_end) }}</pre></el-collapse-item>
        <el-collapse-item title="冻结定位、文件哈希与元数据" name="metadata"><pre class="json-panel">{{ pretty(record) }}</pre></el-collapse-item>
      </el-collapse>
    </template>
  </el-dialog>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import request from '../api/request'
import { originalFileUrl, pretty, sourceRole } from '../utils/presentation'
const props=defineProps({modelValue:Boolean,record:Object,quote:String})
const emit=defineEmits(['update:modelValue'])
const open=computed({get:()=>props.modelValue,set:value=>emit('update:modelValue',value)})
const source=ref(null),loading=ref(false),sourceError=ref('')
let requestVersion=0
watch(()=>props.record,()=>{requestVersion++;source.value=null;sourceError.value='';loading.value=false})
async function verify(){
  if(!props.record?.chunk_id)return
  const version=++requestVersion
  loading.value=true;sourceError.value=''
  try { const response=await request.get('/chunks/'+encodeURIComponent(props.record.chunk_id)+'/source');if(version===requestVersion)source.value=response.data }
  catch { if(version===requestVersion)sourceError.value='来源核对暂不可用，请保留冻结证据并检查原件。' }
  finally { if(version===requestVersion)loading.value=false }
}
</script>
