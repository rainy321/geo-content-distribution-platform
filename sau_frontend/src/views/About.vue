<template>
  <div class="about">
    <el-card class="about-card">
      <div class="about-header">
        <h1>GEO AI 搜索工作台</h1>
        <p class="version">2026.10.27.1</p>
      </div>

      <el-divider />

      <div class="about-section">
        <h3>系统简介</h3>
        <p>
          从品牌和产品资料开始，观察 AI 如何回答与引用来源，再把采集发现转成选题、平台稿件和发布后的复测记录。
          品牌、报告、稿件与任务保存在本机工作区，关闭软件后可以继续。
        </p>
      </div>

      <div class="about-section">
        <h3>当前工作流程</h3>
        <div class="platform-tags">
          <el-tag effect="plain">品牌资料</el-tag>
          <el-tag effect="plain">豆包搜索采集</el-tag>
          <el-tag effect="plain">来源驱动内容</el-tag>
          <el-tag effect="plain">公司资料维护</el-tag>
          <el-tag effect="plain">官网接入</el-tag>
          <el-tag effect="plain">平台发布</el-tag>
          <el-tag effect="plain">同题反馈</el-tag>
        </div>
      </div>

      <div class="about-section">
        <h3>核心功能</h3>
        <ul class="feature-list">
          <li>记录实际回答、可见引用和采集时间，按品牌隔离</li>
          <li>根据来源发现平台，按目标要求生成待审文章</li>
          <li>已接入平台逐篇确认发布；其他平台可配置辅助填写</li>
          <li>沿用有效登录会话，按平台实际要求处理验证</li>
          <li>未指定浏览器时优先 Chrome，未找到再使用 Edge</li>
          <li>知乎常见 Markdown 结构原生导入，逐项核对后继续</li>
          <li>符合恢复条件的旧超时任务，可继续未发送问题</li>
          <li>按原题做单题复测，保留前后回答与来源对照</li>
          <li>企查查公司官网与简介准备、人工处理及只读跟进</li>
          <li>Bing 验证文件指引、公网核对与站长结果分别记录</li>
          <li>发布前核对正文与 AI 声明，未就绪时提示具体步骤</li>
          <li>分别查看发布状态、公开链接与 AI 引用观察</li>
        </ul>
      </div>

      <div class="about-section">
        <h3>使用范围</h3>
        <p>当前已完成今日头条文章的真实发布走查。各平台的账号权限与页面不同，尚未验证的平台需单独接入和测试；稿件需审阅，发布不代表已被 AI 收录或引用。</p>
        <p>公司资料维护仅适用于公司档案，由用户本人在官方页面最终确认提交；待审核、公开资料更新与搜索采用分别记录。</p>
        <p>Bing 登录、文件部署与后台操作由网站负责人完成；软件核对公网文件，并单独保存用户确认的验证、站点地图和索引状态。处理中不代表已索引，也不保证豆包采用。</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
// 关于页面组件
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.about {
  max-width: 700px;
  margin: 0 auto;

  .about-card {
    .about-header {
      text-align: center;

      h1 {
        color: $text-primary;
        margin: 0 0 8px 0;
        font-size: 24px;
      }

      .version {
        color: $text-secondary;
        font-size: 14px;
        margin: 0;
      }
    }

    .about-section {
      margin-bottom: 24px;

      h3 {
        font-size: 16px;
        color: $text-primary;
        margin: 0 0 12px 0;
      }

      p {
        color: $text-secondary;
        line-height: 1.8;
        margin: 0;
      }

      .platform-tags,
      .tech-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .feature-list {
        margin: 0;
        padding-left: 20px;
        color: $text-secondary;
        line-height: 2;
      }
    }
  }
}
</style>
