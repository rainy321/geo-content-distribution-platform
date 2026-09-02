# 搜狐号图文发布设计（type=8）

日期：2026-07-31

## 范围

- 平台：搜狐号创作者后台 `mp.sohu.com`
- 内容类型：仅图文文章（不做视频、不做 CLI skill）
- 平台编号：`type = 8`

## 架构

对齐今日头条图文接入：Playwright cookie 登录 + `SoHuArticle` 填表发布 + `/postVideo` 后台线程调度。

```
PublishCenter (type=8, article)
  -> POST /postVideo
  -> post_article_sohu
  -> SoHuArticle (Playwright)

AccountManagement
  -> SSE /login?type=8
  -> sohu_cookie_gen
  -> cookiesFile/*.json (user_info.type=8)
```

## 关键文件

| 模块 | 路径 |
|------|------|
| Uploader | `uploader/sohu_uploader/main.py` |
| Logger | `utils/log.py` (`sohu_logger`) |
| Cookie 校验 | `myUtils/auth.py` case 8 |
| 登录 SSE | `myUtils/login.py` `sohu_cookie_gen` |
| 发布调度 | `myUtils/postVideo.py` `post_article_sohu` |
| 后端路由 | `sau_backend.py` type=8 |
| 前端账号/发布 | `AccountManagement.vue` / `PublishCenter.vue` / `account.js` |
| 示例 | `examples/get_sohu_cookie.py` / `examples/upload_article_to_sohu.py` |

## 行为约定

- 登录：有头浏览器打开 `mp.sohu.com/mpfe/v4/login`，人工登录后保存 storage_state
- 发文 URL 候选：`mpfe/v4/contentManagement/news/addarticle` 等，失败则从内容管理点「写文章」
- 支持：标题（必填，5-72 字）、正文、标签（独立输入或写入正文）、可选封面（单图；尺寸大于 450×300；jpg/jpeg/png，单张最大 10MB）、创作声明（无需声明/含有虚构演绎内容/含有AI生成内容/含有营销信息/内容为转载/内容为个人观点）、dry_run、可选定时；旧版“信息来源”参数通过别名兼容
- 搜狐强制 `contentType=article`；前端隐藏视频选项
- 创作声明默认「无需声明」；旧版 `workStatement/info_source` 的“无特别声明”“包含AI创作内容”“包含虚构创作”会映射到当前平台标签

## 验收

1. 账号管理可添加搜狐并登录，DB `type=8`
2. 发布中心选搜狐仅能发图文，任务可提交
3. dry_run 填表不点发布
4. Cookie 失效时日志明确提示重新登录
