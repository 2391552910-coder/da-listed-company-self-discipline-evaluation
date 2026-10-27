<template>
  <el-container class="app-layout" :data-motion="reduced ? 'reduced' : 'full'">
    <a class="skip-link" href="#main-content">跳至主要内容</a>
    <el-aside class="app-sidebar" width="244px">
      <router-link class="brand" to="/" aria-label="主体自律评价工作总览"><span class="brand-mark"><SceneIcon name="layers" /></span><span>主体自律评价<small>上市公司研究工作台</small></span></router-link>
      <div class="nav-caption">研究空间</div>
      <el-menu aria-label="主要导航" :default-active="activeMenu" router>
        <el-menu-item v-for="item in navigation" :key="item.path" :index="item.path"><SceneIcon :name="item.icon" /><span>{{ item.label }}</span><span class="nav-indicator" /></el-menu-item>
      </el-menu>
      <div class="sidebar-note"><span class="note-line" />从原文到结论<p>保存来源，核对证据，回看每次运行。</p><span class="chapter-note">第三章指数结果待接入</span></div>
    </el-aside>
    <el-container class="app-body">
      <el-header class="app-header"><div class="header-location"><span class="header-system">主体自律研究</span><span class="header-divider">/</span><span>{{ currentTitle }}</span></div><button class="motion-control" type="button" :aria-pressed="reduced" @click="toggleMotion"><SceneIcon name="motion" />{{ reduced ? '开启动效' : '减少动效' }}</button></el-header>
      <el-main id="main-content" tabindex="-1"><router-view v-slot="{ Component }"><transition name="scene" mode="out-in"><component :is="Component" :key="route.path" /></transition></router-view><footer class="workspace-footer">主体自律评价研究工作台<span>结论、来源校验与人工复核分别记录</span></footer></el-main>
    </el-container>
  </el-container>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import SceneIcon from './components/SceneIcon.vue'
const route=useRoute()
const navigation=[{path:'/',label:'工作总览',icon:'overview'},{path:'/companies',label:'公司管理',icon:'company'},{path:'/documents',label:'双源文档中心',icon:'document'},{path:'/contexts',label:'上下文与检索',icon:'search'},{path:'/evaluations',label:'评价工作台',icon:'layers'},{path:'/tasks',label:'任务中心',icon:'task'},{path:'/experiments',label:'实验与对照',icon:'experiment'},{path:'/reports-export',label:'报告导出',icon:'report'}]
const activeMenu=computed(()=>route.path.startsWith('/evaluations') ? '/evaluations' : route.path)
const currentTitle=computed(()=>navigation.find(item=>item.path===activeMenu.value)?.label || '研究工作台')
const reduced=ref(false)
try{reduced.value=localStorage.getItem('dalun-reduced-motion')==='true'}catch{}
document.documentElement.dataset.motion=reduced.value ? 'reduced' : 'full'
function toggleMotion(){reduced.value=!reduced.value;document.documentElement.dataset.motion=reduced.value ? 'reduced' : 'full';try{localStorage.setItem('dalun-reduced-motion',String(reduced.value))}catch{}}
</script>
