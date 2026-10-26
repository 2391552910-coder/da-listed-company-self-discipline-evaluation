<template>
  <el-container class="app-layout">
    <el-aside class="app-sidebar" width="220px">
      <div class="brand"><span class="brand-mark">DA</span><div>主体自律评价<small>上市公司研究工作台</small></div></div>
      <el-menu :default-active="activeMenu" router background-color="#13243a" text-color="#bfcbd9" active-text-color="#93c5fd">
        <el-menu-item index="/">工作总览</el-menu-item>
        <el-menu-item index="/companies">公司管理</el-menu-item>
        <el-menu-item index="/documents">双源文档中心</el-menu-item>
        <el-menu-item index="/contexts">上下文与检索</el-menu-item>
        <el-menu-item index="/evaluations">评价工作台</el-menu-item>
        <el-menu-item index="/tasks">任务中心</el-menu-item>
        <el-menu-item index="/experiments">实验与对照</el-menu-item>
        <el-menu-item index="/reports-export">报告导出</el-menu-item>
        <el-menu-item index="/index" disabled>第三章指数 · 待接入</el-menu-item>
      </el-menu>
      <p class="sidebar-note">研究辅助评价<br>结论、证据与人工复核分别记录</p>
    </el-aside>
    <el-container>
      <el-header class="app-header"><span>上市公司主体自律评价系统</span><span class="muted">双源证据 · 可追溯运行</span></el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route=useRoute()
const activeMenu=computed(()=>route.path.startsWith('/evaluations') ? '/evaluations' : route.path)
</script>
