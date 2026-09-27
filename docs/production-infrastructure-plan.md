# GEO MVP 生产基础设施落地清单

> 2026-09-08 更新：项目所有者已经提供一台带 Docker、Nginx 和 VNC 的持久化
> Linux 服务器。当前首选方案已改为同机部署 Web 与独立 Worker，使用服务器本地
> SQLite、Cookie 和素材持久目录。下文的 Turso、对象存储和 Upstash 组合保留为
> 多实例扩容路径，不是本次单机迁移的前置条件。具体步骤见
> [`production-server-deployment.md`](./production-server-deployment.md)。

## 当前边界

- `https://geo.tongyuye.com` 是正式入口；Web 与独立 Worker 已部署到同一台受控 Linux 服务器并共享 `/srv/geo-platform/runtime`。
- 正式环境使用持久 SQLite、私有 Cookie/素材目录和独立每日热备；Web 仅绑定 `127.0.0.1:5409`，由现有 Nginx 提供 HTTPS。
- 旧 Vercel 地址只保留为可重建 Demo，继续固定 `DEMO_MODE=true`、`ALLOW_REAL_PUBLISHING=false`、`RUN_PUBLISH_SCHEDULER=false`。
- 首次上线的服务器 Worker 也保持真实发布总开关关闭；逐个平台验证迁移 Cookie 后才显式开启。
- 当前代码已完成运行时 `DATABASE_PATH`、`COOKIES_DIRECTORY`、`MEDIA_ROOT` 隔离；旧版兼容接口不再绕过这些配置。

## 多实例扩容时的推荐组合

### 1. 业务数据库：Turso / libSQL

选择理由：现有数据访问层使用 SQLite SQL、占位符、`lastrowid` 和事务；Turso 的 Python 远程客户端沿用接近 `sqlite3` 的接口，比迁移到 PostgreSQL 改动更小。

接入前置条件：

- 项目所有者创建 Turso 数据库并接受对应套餐条款。
- 只向 Vercel 和 Worker 注入 `TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`，不写入仓库。
- 增加统一连接工厂；本地测试继续使用 SQLite 文件，生产 Web 与 Worker 使用远程 libSQL。
- 迁移 `projects`、`articles`、`content_templates`、`file_records`、`user_info`、`publish_jobs`，核对行数、外键和任务状态。
- Web 与 Worker 各执行一次写后读验收，并验证任务原子领取不会重复执行。

官方资料：<https://docs.turso.tech/sdk/python/quickstart>

### 2. 素材：私有对象存储

素材、封面和视频不能长期留在 Vercel `/tmp`。建议先使用 Vercel Blob Private，以便与现有 Vercel 项目统一管理；如果后续更换 Worker 云厂商，可改为 S3 兼容存储。

实现要求：

- 数据库只保存对象键、原始文件名、大小、媒体类型与校验和，不保存临时本地绝对路径。
- 浏览器上传优先采用客户端直传，避免大视频经过 Vercel Function。
- Worker 执行任务前把私有对象下载到一次性工作目录，校验大小/摘要，发布结束后清理临时副本。
- 账号 Cookie 不与普通素材混放；必须使用独立私有前缀、最小权限 Token，并禁止生成公开 URL。
- 删除接口先删除对象，成功后再删除元数据；失败时保留可重试状态，避免只删数据库造成孤儿对象。

官方资料：<https://vercel.com/docs/vercel-blob>

### 3. 共享限流：Upstash Redis Free

代码中的 REST 原子计数适配器已完成。项目所有者创建或连接 Upstash Redis 后，只需把 `UPSTASH_REDIS_REST_URL` 与 `UPSTASH_REDIS_REST_TOKEN` 作为 Vercel Secret 配置并重部署。

验收：

- `/backend/api/health` 的 `rate_limit_scope` 从 `instance` 变为 `shared`。
- 从两个独立会话累计触发同一窗口上限，确认计数不是实例内各自计算。
- 暂时移除一个测试环境 Token，确认服务安全降级为进程内限流且业务不崩溃；生产环境随后恢复 Secret。

官方资料：<https://upstash.com/docs/redis/howto/vercelintegration>

### 4. 发布 Worker：当前正式 Linux 主机

正式服务器已构建 Playwright 与 Patchright 浏览器运行时，迁移 12 个账号记录及其 Cookie、素材和 SQLite；`sau-worker --check` 在模拟真实模式下返回 `ready`，Web 与 Worker 容器均有独立健康探针。

启用真实常驻执行前必须：

- 从正式服务器出口 IP 逐个执行账号只读登录检查，失效账号由项目所有者重新登录。
- 确认新建任务的文章、账号、素材、渠道和执行时间与授权指纹完全一致。
- 把服务器 Worker 的 `ALLOW_REAL_PUBLISHING` 从 `false` 显式改为 `true`；Vercel 端仍保持关闭。
- 先执行 `sau-worker --check`，再用一条新建的 Demo 定时任务跑通领取、执行和状态回写，最后才验收真实定时发布。

## 不应采用的方案

- 不把 SQLite、Cookie 或视频长期放在 Vercel `/tmp`。
- 不在 Vercel Function 中启动 Playwright 常驻发布线程。
- 不把媒体账号 Cookie 放在 Public Blob 或前端可读取变量中。
- 不同时迁移到 PostgreSQL、重写全部数据访问层并更换发布运行时；这会背离 MVP 的最小侵入原则。

## 未来横向扩容需要项目所有者确认的外部动作

1. 创建 Turso 资源并确认套餐。
2. 创建私有对象存储并确认套餐。
3. 创建或连接 Upstash Redis；Free 适合当前 MVP，超限后再评估升级。
4. 确认扩容后的浏览器 Worker 主机、出口 IP 和 Cookie 迁移策略。

完成以上四项后，实施顺序固定为：数据库连接工厂与数据迁移 → 私有对象存储 → Web/Worker 联通 → Upstash → Demo 定时任务 → 单次授权真实定时验收。
