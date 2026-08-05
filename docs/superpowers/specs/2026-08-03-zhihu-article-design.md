# 知乎文章发布设计（type=9）

日期：2026-08-03

## 范围

- 平台：知乎创作中心 / 专栏写文章（Playwright）
- 内容类型：仅文章（不做视频、想法、回答、CLI、skill）
- 平台编号：`type = 9`
- 字段：标题（必填）、正文（必填）、可选封面、`dry_run`
- 接入入口：Web 后台 + `examples` 脚本（对齐搜狐）

## 架构

对齐搜狐号图文接入：Playwright cookie 登录 + `ZhiHuArticle` 填表发布 + `/postVideo` 后台线程调度。

```
PublishCenter (type=9, article)
  -> POST /postVideo
  -> post_article_zhihu
  -> ZhiHuArticle (Playwright)

AccountManagement
  -> SSE /login?type=9
  -> zhihu_cookie_gen
  -> cookiesFile/*.json (user_info.type=9)
```

## 关键文件

| 模块 | 路径 |
|------|------|
| Uploader | `uploader/zhihu_uploader/main.py`（+ `__init__.py`） |
| Logger | `utils/log.py`（`zhihu_logger`） |
| Cookie 校验 | `myUtils/auth.py` case 9 |
| 登录 SSE | `myUtils/login.py` `zhihu_cookie_gen` |
| 发布调度 | `myUtils/postVideo.py` `post_article_zhihu` |
| 后端路由 | `sau_backend.py` type=9；`ARTICLE_CAPABLE_PLATFORMS` 含 9；强制 `contentType=article` |
| 前端账号/发布 | `AccountManagement.vue` / `PublishCenter.vue` / `account.js` |
| 示例 | `examples/get_zhihu_cookie.py` / `examples/upload_article_to_zhihu.py` |

不改动：DB schema（沿用 `user_info.type`）、`sau_cli.py`、`skills/`。

## 行为约定

- 登录：有头浏览器打开知乎登录页（如 `https://www.zhihu.com/signin`），人工登录后保存 Playwright `storage_state`
- Cookie 有效性：访问写文章或创作中心相关页，未跳回登录页则视为有效
- 发文入口优先 `https://zhuanlan.zhihu.com/write`；失败则从创作中心点「写文章」兜底
- 标题必填；正文必填（纯文本按段落填入编辑器）
- 封面可选（jpg/jpeg/png，单张默认 ≤10MB）；若知乎页面校验更严，实现时按页面提示收紧
- `dry_run=true`：填表不点发布
- 前端：`articleCapablePlatforms` 与 `articleOnlyPlatforms` 均含 `9`，隐藏视频选项
- 不做：话题标签、专栏选择、定时发布、视频、想法、CLI/skill

## 验收

1. 账号管理可添加知乎并登录，DB `user_info.type=9`
2. 发布中心选知乎仅能发图文，任务可提交
3. `dry_run` 填表不点发布
4. Cookie 失效时日志明确提示重新登录
