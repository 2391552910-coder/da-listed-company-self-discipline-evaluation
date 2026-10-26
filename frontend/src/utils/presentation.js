export const statusName = value => ({ pending:'待排队', queued:'排队中', running:'运行中', cancelling:'取消处理中', succeeded:'已完成', partial:'部分完成', failed:'失败', cancelled:'已取消', budget_exhausted:'预算耗尽', not_run:'未运行', uncertain:'调用结果未知', ready:'就绪', staging:'构建中', done:'已解析' }[value] || value || '未知')
export const statusType = value => value === 'succeeded' || value === 'ready' || value === 'done' ? 'success' : ['failed','budget_exhausted'].includes(value) ? 'danger' : ['cancelled','partial'].includes(value) ? 'warning' : 'info'
export const kindName = value => ({parse:'文档解析',index:'索引构建',context:'上下文组装',evaluation:'模型评价'}[value] || value || '—')
export const modeName = value => ({dual_fixed:'固定双源',dual_agent:'Agent 扩展'}[value] || value || '方法见冻结配置')
export const short = value => value ? String(value).slice(0,8) : '—'
export const pretty = value => JSON.stringify(value ?? null,null,2)
export const formatDate = value => {
  if(!value)return '—'
  const parsed=new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(parsed)
}
export const sourceRole = value => ({annual_report:'公司年报',regulation:'法规'}[value] || value || '—')
export const evidenceName = value => ({sufficient:'证据充分（模型判断）',insufficient:'证据不足',partial:'证据部分可用',unknown:'尚未确定'}[value] || value || '尚未确定')
export const supportName = value => ({supported:'支持',partial:'部分支持',unsupported:'不支持',conflicted:'存在冲突',unknown:'未知'}[value] || value || '未知')
export const originalFileUrl = record => '/api/v1/document-versions/'+encodeURIComponent(record.version_id)+'/file'+(record.physical_page ? '#page='+encodeURIComponent(record.physical_page) : '')
