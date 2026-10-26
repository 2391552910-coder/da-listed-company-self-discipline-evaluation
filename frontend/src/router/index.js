import { createRouter, createWebHistory } from 'vue-router'
const routes = [
  { path:'/', component:()=>import('../views/Dashboard.vue') },
  { path:'/companies', component:()=>import('../views/CompanyManage.vue') },
  { path:'/documents', component:()=>import('../views/DocumentCenter.vue') },
  { path:'/contexts', component:()=>import('../views/ContextWorkbench.vue') },
  { path:'/evaluations', component:()=>import('../views/EvaluationWorkspace.vue') },
  { path:'/evaluations/new', component:()=>import('../views/EvaluationWorkspace.vue') },
  { path:'/evaluations/:id', component:()=>import('../views/EvaluationDetail.vue') },
  { path:'/tasks', component:()=>import('../views/TaskCenter.vue') },
  { path:'/experiments', component:()=>import('../views/ExperimentCenter.vue') },
  { path:'/reports-export', component:()=>import('../views/ReportExport.vue') },
  { path:'/evaluation', redirect:'/evaluations' },
  { path:'/report', redirect:'/reports-export' },
  { path:'/import', redirect:'/documents' },
  { path:'/evidence', redirect:'/evaluations' },
  { path:'/index', redirect:'/' },
  { path:'/:pathMatch(.*)*', component:()=>import('../views/NotFound.vue') },
]
export default createRouter({history:createWebHistory(),routes,scrollBehavior:()=>({top:0})})
