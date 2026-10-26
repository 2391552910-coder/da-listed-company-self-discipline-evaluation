<template>
  <el-select
    v-bind="$attrs"
    v-model="selection"
    filterable
    remote
    remote-show-suffix
    :remote-method="search"
    :clearable="clearable"
    :placeholder="placeholder"
    :loading="loading"
    loading-text="正在查找公司"
    :no-data-text="searchError ? '公司读取失败，请重新输入或关闭后重试' : '未找到公司，请修改关键词或先登记公司'"
    @visible-change="opened"
  >
    <el-option
      v-for="company in options"
      :key="company.id"
      :value="company[valueField]"
      :label="company.stock_code+' '+company.stock_name"
    />
    <el-option v-if="selectedMissing" :value="selection" :label="missingLabel" disabled />
    <el-option v-if="searchError && options.length" value="__company_error__" label="搜索失败，请重新输入关键词后重试" disabled />
    <el-option v-if="total>50" value="__company_more__" :label="'共 '+total+' 家匹配公司，显示前50家，请输入更完整的代码或名称'" disabled />
    <el-option v-if="selectionError" value="__company_selection_error__" label="当前公司名称未能读取，请关闭后重试" disabled />
  </el-select>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import request from '../api/request'

defineOptions({ inheritAttrs:false })
const props=defineProps({
  modelValue:{type:[String,Number],default:''},
  valueField:{type:String,default:'stock_code',validator:value=>['stock_code','id'].includes(value)},
  clearable:{type:Boolean,default:true},
  placeholder:{type:String,default:'输入股票代码或公司名称'},
})
const emit=defineEmits(['update:modelValue'])
const normalizedValue=()=>{
  if(props.valueField!=='stock_code' || typeof props.modelValue!=='string')return props.modelValue
  const value=props.modelValue.trim().toUpperCase()
  return /^[0-9]{6}(?:\.(?:SH|SZ|BJ))?$/.test(value) ? value.slice(0,6) : value
}
const selection=computed({
  get:normalizedValue,
  set:value=>emit('update:modelValue',value===undefined || value===null || value==='' ? (props.valueField==='id' ? null : '') : value),
})
const rows=ref([]),selected=ref(null),loading=ref(false),total=ref(0),searchError=ref(false),selectionError=ref(false),selectedMissing=ref(false)
let alive=true,query='',searchSequence=0,selectionSequence=0
const hasSelection=()=>props.modelValue!=='' && props.modelValue!==null && props.modelValue!==undefined
const options=computed(()=>selected.value && !rows.value.some(row=>row[props.valueField]===selected.value[props.valueField]) ? [selected.value,...rows.value] : rows.value)
const missingLabel=computed(()=>(props.valueField==='id' ? '公司 ID '+props.modelValue : props.modelValue)+'（未找到登记记录）')

async function search(value=''){
  query=String(value).trim()
  const sequence=++searchSequence,params={page:1,page_size:50,...(query ? {q:query} : {})}
  loading.value=true;searchError.value=false
  try{
    const response=await request.get('/companies',{params})
    if(!alive || sequence!==searchSequence)return
    rows.value=response.data.items;total.value=response.data.total
  }catch{
    if(alive && sequence===searchSequence){rows.value=[];total.value=0;searchError.value=true}
  }finally{
    if(alive && sequence===searchSequence)loading.value=false
  }
}

async function hydrate(){
  const sequence=++selectionSequence,value=normalizedValue(),field=props.valueField
  selected.value=null;selectedMissing.value=false;selectionError.value=false
  if(!hasSelection())return
  try{
    const response=await request.get('/companies',{params:{page:1,page_size:1,[field==='id' ? 'company_id' : 'stock_code']:value}})
    if(!alive || sequence!==selectionSequence || value!==normalizedValue() || field!==props.valueField)return
    selected.value=response.data.items.find(row=>row[field]===value) || null
    selectedMissing.value=!selected.value
  }catch{
    if(alive && sequence===selectionSequence)selectionError.value=true
  }
}
function opened(visible){if(visible){search(query);if(hasSelection() && !selected.value)hydrate()}}
watch(()=>[props.modelValue,props.valueField],hydrate,{immediate:true})
onMounted(()=>search())
onUnmounted(()=>{alive=false;searchSequence++;selectionSequence++})
</script>
