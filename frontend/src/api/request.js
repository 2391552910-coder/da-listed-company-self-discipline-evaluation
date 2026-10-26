import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({ baseURL: '/api/v1', timeout: 120000 })

request.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        ElMessage.error(body.message || '请求失败')
        return Promise.reject(new Error(body.message))
      }
      return body
    }
    return body
  },
  (err) => {
    const detail=err.response?.data?.detail
    const validationMessage=Array.isArray(detail) ? detail.map(item=>item.loc?.slice(1).join('.')+': '+item.msg).join('；') : typeof detail==='string' ? detail : ''
    const msg = err.response?.data?.message || validationMessage || err.message || '网络错误'
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

export default request
