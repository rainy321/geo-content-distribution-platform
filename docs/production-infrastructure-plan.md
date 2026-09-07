# GEO MVP 生产基础设施落地清单

## 当前边界

- Vercel 只承载 Web 控制面和轻量 API，`DEMO_MODE=true`、`ALLOW_REAL_PUBLISHING=false`、`RUN_PUBLISH_SCHEDULER=false`。
- Vercel Function 的本地文件系统只有临时 `/tmp`，不能作为文章、任务、Cookie 或素材的持久存储。
- 真实发布必须在安装浏览器、能够长期运行且可安全保存媒体账号会话的 Worker 主机执行。
- 当前代码已完成运行时 `DATABASE_PATH`、`COOKIES_DIRECTORY`、`MEDIA_ROOT` 隔离；旧版兼容接口不再绕过这些配置。

## 推荐的最小组合

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

### 4. 发布 Worker：先使用当前 Windows 主机

这是最少迁移的第一阶段：平台账号已经在此机器完成登录，浏览器运行时、Cookie 和素材目录均可用，`sau-worker --check` 已返回 `ready`。

启用真实常驻执行前必须：

- Web 与 Worker 已切换到同一个持久数据库；否则 Vercel 创建的任务无法被本机 Worker 看到。
- Worker 能以最小权限读取私有素材与账号 Cookie。
- 本机设置受控的开机启动/进程守护、日志轮转和磁盘空间告警。
- Worker 环境设置 `DEMO_MODE=false`、`ALLOW_REAL_PUBLISHING=true`；Vercel 端仍保持真实发布关闭。
- 先执行 `sau-worker --check`，再用一条新建的 Demo 定时任务跑通领取、执行和状态回写，最后才验收真实定时发布。

## 不应采用的方案

- 不把 SQLite、Cookie 或视频长期放在 Vercel `/tmp`。
- 不在 Vercel Function 中启动 Playwright 常驻发布线程。
- 不把媒体账号 Cookie 放在 Public Blob 或前端可读取变量中。
- 不同时迁移到 PostgreSQL、重写全部数据访问层并更换发布运行时；这会背离 MVP 的最小侵入原则。

## 需要项目所有者确认的外部动作

1. 创建 Turso 资源并确认套餐。
2. 创建私有对象存储并确认套餐。
3. 创建或连接 Upstash Redis；Free 适合当前 MVP，超限后再评估升级。
4. 确认当前 Windows 主机是否允许作为第一阶段常驻 Worker；如果不允许，再选择 VPS/托管容器主机。

完成以上四项后，实施顺序固定为：数据库连接工厂与数据迁移 → 私有对象存储 → Web/Worker 联通 → Upstash → Demo 定时任务 → 单次授权真实定时验收。
