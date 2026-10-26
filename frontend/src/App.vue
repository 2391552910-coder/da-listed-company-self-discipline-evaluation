<template>
  <el-container style="height: 100vh">
    <el-aside width="220px">
      <el-menu :default-active="$route.path" router background-color="#1f2d3d"
               text-color="#bfcbd9" active-text-color="#409eff">
        <div class="logo">主体自律评价系统</div>
        <el-menu-item index="/">总览看板</el-menu-item>
        <el-menu-item index="/companies">公司管理</el-menu-item>
        <el-menu-item index="/documents">双源文档中心</el-menu-item>
        <el-menu-item index="/contexts">上下文与任务</el-menu-item>
        <el-menu-item index="/index" disabled>自律指数</el-menu-item>
        <el-menu-item index="/evaluation" disabled>大模型评价</el-menu-item>
        <el-menu-item index="/evidence" disabled>法规检索与证据</el-menu-item>
        <el-menu-item index="/report" disabled>报告导出</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.logo {
  color: #fff;
  font-size: 16px;
  font-weight: bold;
  text-align: center;
  padding: 18px 0;
}
</style>
