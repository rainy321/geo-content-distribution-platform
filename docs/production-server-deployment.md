# GEO 正式服务器部署方案

## 目标拓扑

正式服务器继续由现有 Nginx 统一接入。GEO 使用独立 Compose 项目
`geo-platform`，Web 只监听宿主机 `127.0.0.1:5409`，不会直接暴露容器端口。
Nginx 新增独立子域名并反向代理到该端口。发布 Worker 与 Web 使用同一镜像，
共享 SQLite、账号 Cookie 和素材目录，但由独立进程运行调度器。

```text
Internet -> Nginx :443 -> 127.0.0.1:5409 -> geo-platform-web
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
/opt/geo-platform/
  app/                 # 代码和 compose.production.yaml
  runtime/
    data/               # SQLite
    cookies/            # 平台账号会话，敏感
    media/              # 图片和视频
  backups/              # 部署前快照
```

`deploy/production/app.env` 只存在于服务器，权限必须为 `0600`。AI Key、运营口令、
会话签名密钥和平台 Cookie 不进入 Git、Docker 构建上下文或日志。

## 上线闸门

1. 只读盘点 CPU、内存、磁盘、Docker、现有容器、监听端口、Nginx 站点、VNC 显示
   和主机防火墙。
2. 确认 `5409` 未占用，并从现有 `server_name` 选择同一根域名下未使用的 GEO
   子域名；先完成 DNS 解析。
3. 在 `/opt/geo-platform` 创建隔离目录，复制代码和运行时数据。迁移 SQLite 前先
   备份并校验文件摘要；Cookie 只通过 SSH 传输。
4. 从 `app.env.example` 创建服务器 Secret 文件。首次启动保持
   `ALLOW_REAL_PUBLISHING=false`。
5. 运行 `docker compose -f compose.production.yaml config`，构建镜像，再只启动
   Web；通过本机健康接口验收后才启动 Worker。
6. 新增单独的 Nginx 配置，执行 `nginx -t` 成功后只 reload Nginx，不 restart，
   避免中断其他项目。
7. 配置 TLS 后验收登录、AI 生成、数据库持久化、素材上传和 Worker readiness。
8. 平台 Cookie 逐个做只读登录检测；失效账号通过 VNC 人工登录。只有验收完成且
   项目所有者明确开启时，才把 `ALLOW_REAL_PUBLISHING` 改为 `true`。

## 回滚

应用回滚只停止 `geo-platform` Compose 项目并恢复其独立目录中的数据库快照；
Nginx 回滚只移除本次新增的 GEO 站点链接并在 `nginx -t` 通过后 reload。不得执行
全局 `docker system prune`、重启 Docker daemon、覆盖默认 Nginx 站点或删除其他
项目目录。

Vercel 部署在服务器验收完成前保持在线。切换域名后仍保留一段观察期，确认数据、
AI、上传和发布 Worker 均稳定后再决定是否下线旧部署。
