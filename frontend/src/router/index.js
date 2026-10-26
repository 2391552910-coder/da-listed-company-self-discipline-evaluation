import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/documents', component: () => import('../views/DocumentCenter.vue') },
  { path: '/', component: () => import('../views/Dashboard.vue') },
  { path: '/companies', component: () => import('../views/CompanyManage.vue') },
  { path: '/import', component: () => import('../views/DataImport.vue') },
  { path: '/index', component: () => import('../views/IndexResult.vue') },
  { path: '/evaluation', component: () => import('../views/Evaluation.vue') },
  { path: '/evidence', component: () => import('../views/EvidenceSearch.vue') },
  { path: '/report', component: () => import('../views/ReportExport.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
