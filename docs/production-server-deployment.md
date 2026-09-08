# GEO 正式服务器部署方案

## 目标拓扑

正式服务器继续由现有 Nginx 统一接入。GEO 使用独立 Compose 项目
`geo-platform`，Web 只监听宿主机 `127.0.0.1:5409`，不会直接暴露容器端口。
Nginx 新增独立子域名并反向代理到该端口。发布 Worker 与 Web 使用同一镜像，
共享 SQLite、账号 Cookie 和素材目录，但由独立进程运行调度器。

```text
Internet -> Nginx :443 -> 127.0.0.1:5409 -> geo-platform-web
                                             |
                                             +-- optional private content-engine:8080
                                             |
                                             +-- shared SQLite / cookies / media
                                             |
                                      geo-platform-worker
```

本方案不修改或复用其他项目的容器、网络、目录和端口。VNC 只用于平台要求的人工
登录或验证，不应通过新的公网端口暴露。

## 服务器目录

推荐使用以下独立目录：

```text
/srv/geo-platform/
  app/                 # 代码和 compose.production.yaml
  runtime/
    data/               # SQLite
    cookies/            # 平台账号会话，敏感
    media/              # 图片和视频
  backups/              # 部署前快照
```

`deploy/production/app.env` 只存在于服务器，权限必须为 `0600`。AI Key、运营口令、
会话签名密钥和平台 Cookie 不进入 Git、Docker 构建上下文或日志。
`compose.production.yaml` 默认从代码目录的同级 `../runtime` 挂载这三个运行目录；
在候选 release 目录验收时必须显式设置 `GEO_RUNTIME_ROOT=/srv/geo-platform/runtime`。
本机使用 rootless Docker，因此生产 Compose 默认用容器内 `0:0`，它映射为无特权的
`dongai-deploy` 宿主用户；镜像本身仍以 `geo` 非 root 用户为默认。若迁移到 rootful
Docker，必须先改为映射后的非 root UID，并逐项实测数据库文件和三个挂载目录可写。
所有生产 Compose 命令必须以 `dongai-deploy` 身份在同一个 rootless Docker context
中执行，并先用 `docker info`、当前 GEO 容器 ID 核对 daemon；不要在 root 会话中误连
另一套 `/var/run/docker.sock` daemon。

## 上线闸门

1. 只读盘点 CPU、内存、磁盘、Docker、现有容器、监听端口、Nginx 站点、VNC 显示
   和主机防火墙。
2. 确认 `5409` 未占用，并从现有 `server_name` 选择同一根域名下未使用的 GEO
   子域名；先完成 DNS 解析。
3. 在 `/srv/geo-platform` 创建隔离目录，复制代码和运行时数据。迁移 SQLite 前先
   备份并校验文件摘要；Cookie 只通过 SSH 传输。
4. 从 `app.env.example` 创建服务器 Secret 文件。首次启动保持
   `ALLOW_REAL_PUBLISHING=false`，并独立保持 `ENABLE_BILIBILI_RUNTIME=false`。
5. 运行 `docker compose -f compose.production.yaml config --quiet`，构建镜像，再
   只启动 Web；通过本机健康接口验收后才启动 Worker。不要把不带 `--quiet` 的
   展开结果写入日志，因为 Compose 可能解析并显示 `app.env` 中的密钥。
   Dockerfile 对固定的 Chrome-for-Testing 归档在解压前执行 SHA-256
   校验。更换 `PATCHRIGHT_CHROMIUM_URL` 时必须同时从受信任来源重新核对并
   覆盖 `PATCHRIGHT_CHROMIUM_SHA256`；禁止只换 URL 或用未固定的摘要构建。
   候选镜像启动后还必须执行 `id -u`、`test -w /app/data/database.db` 及 Cookie/媒体
   目录写权限检查，并实际运行一次 `initialize_database`；任一失败都禁止切换。
6. 新增单独的 Nginx 配置，执行 `nginx -t` 成功后只 reload Nginx，不 restart，
   避免中断其他项目。
7. 配置 TLS 后验收登录、AI 生成、数据库持久化、素材上传和 Worker readiness。
8. 平台 Cookie 逐个做只读登录检测；失效账号通过 VNC 人工登录。只有验收完成且
   项目所有者明确开启时，才把 `ALLOW_REAL_PUBLISHING` 改为 `true`。开放其他平台时
   仍须保持 `ENABLE_BILIBILI_RUNTIME=false`；它只在固定版本/摘要、安全解包与商业授权
   全部验收后单独开启。

## 回滚

应用回滚先把当前镜像固定为不可变的 `rollback-<timestamp>` 标签，候选镜像使用
Git SHA 标签。切换失败时只把 Web/Worker 切回旧镜像；兼容性正常时保留当前数据库，
避免丢失切换后新增的数据。只有确认迁移破坏了 schema 或业务数据时，才在停写并
再次备份现状后恢复部署前 SQLite 快照。Nginx 回滚只移除本次新增的 GEO 站点链接并
在 `nginx -t` 通过后 reload。不得执行
全局 `docker system prune`、重启 Docker daemon、覆盖默认 Nginx 站点或删除其他
项目目录。

Vercel 部署在服务器验收完成前保持在线。切换域名后仍保留一段观察期，确认数据、
AI、上传和发布 Worker 均稳定后再决定是否下线旧部署。

可选同事内容引擎的隔离配置、灰度切换、回滚和 Bilibili 商用硬门槛见
[内容引擎生产切换与回滚](content-engine-production-runbook.md)。
