# 内容引擎生产切换与回滚

本文只描述 `geo-platform` 项目内的可逆切换，不触碰同机其他 Compose 项目、
Nginx 站点或 Docker 全局资源。正式环境默认继续使用 `qwen`；同事引擎是可选、
profile 隔离的内部服务，而不是 Web 的启动依赖。

## 固定边界

- Web 只通过 `http://content-engine:8080` 访问同事引擎。
- `content-engine` 没有宿主机端口，不挂载 GEO 的 SQLite、Cookie、媒体目录或
  Docker socket，也不读取 `deploy/production/app.env`。
- 同事镜像必须提供 `POST /api/v1/generate`、`GET /health/live` 和
  `GET /health/ready`，并在镜像内声明针对 readiness 的 Docker
  `HEALTHCHECK`。Compose 不假设镜像内存在 `curl`、Python 或特定启动命令。
- 引擎只能把临时文件写到 `/tmp`；需要的模型和静态资源应在镜像构建阶段固化。
- `COLLEAGUE_CONTENT_ENGINE_TOKEN` 与引擎侧校验值必须是独立随机密钥，不能复用
  AI Key、运营口令、会话密钥或平台 Cookie。
- `COLLEAGUE_CONTENT_ENGINE_ALLOWED_HOSTS` 默认且建议保持为 Compose 服务名
  `content-engine`；只有经过审批的内部 DNS 名称才能加入，禁止公网和云 metadata
  地址，避免 Bearer token 被误发到外部。

## 首次准备

1. 将同事镜像固定到不可变 digest，并通过部署主机的受控镜像仓库提供。不要在
   生产机运行来源不明的构建脚本。
2. 从 `deploy/production/content-engine.env.example` 创建
   `deploy/production/content-engine.env`，权限设为 `0600`。只放同事镜像真正
   需要的变量，不得复制 `app.env`。
3. 在部署 shell 或项目根目录的 Git-ignored `.env` 中设置：

   ```dotenv
   COLLEAGUE_CONTENT_ENGINE_IMAGE=registry.example.com/geo/content-engine@sha256:<digest>
   COLLEAGUE_CONTENT_ENGINE_ENV_FILE=./deploy/production/content-engine.env
   ```

4. 保持 `app.env` 中 `CONTENT_ENGINE=qwen`，并先执行配置预检：

   ```bash
   docker compose -f compose.production.yaml config --quiet
   docker compose -f compose.production.yaml --profile colleague config --quiet
   ```

   不要把不带 `--quiet` 的 Compose 展开结果复制到工单或日志；`env_file` 的解析值
   可能包含 Web 和内容引擎密钥。

## 可控切换（单 Web 会短暂重建）

当前拓扑只有一个 Web 容器，修改进程环境必须重建该容器，因此不能宣称真正的
零停机。先停止新生成请求并等待在途请求结束；SQLite、Worker 和内容引擎不会随
Web 一起重启。

1. 只启动候选引擎，不重建 Web/Worker：

   ```bash
   docker compose -f compose.production.yaml --profile colleague up -d content-engine
   docker compose -f compose.production.yaml --profile colleague ps content-engine
   ```

2. 从 Compose 网络内部检查健康；不增加临时公网端口：

   ```bash
   docker compose -f compose.production.yaml exec -T web python -c "import os, urllib.request; token=os.getenv('COLLEAGUE_CONTENT_ENGINE_TOKEN',''); headers={'Authorization': 'Bearer '+token} if token else {}; urllib.request.urlopen(urllib.request.Request('http://content-engine:8080/health/live', headers=headers), timeout=3).read(); urllib.request.urlopen(urllib.request.Request('http://content-engine:8080/health/ready', headers=headers), timeout=10).read()"
   ```

3. 为 `app.env` 创建权限为 `0600` 的时间戳备份。把
   `CONTENT_ENGINE=qwen` 改为 `CONTENT_ENGINE=colleague`，同时把默认空值
   `CONTENT_ENGINE_FALLBACK=` 改为 `CONTENT_ENGINE_FALLBACK=qwen`；保留
   Qwen 凭据和全部超时/响应大小限制。默认 Qwen 模式不配置同名 fallback，
   避免供应商故障时重复发送同一请求。
4. 只重建 Web，不重启 Worker 或同机其他项目：

   ```bash
   docker compose -f compose.production.yaml up -d --no-deps web
   ```

5. 验收 `/api/health`，再做一条不保存、不发布的 600 字生成请求。只记录 HTTP
   状态、引擎名、`trace_id`、耗时和响应字节数；不要记录请求正文、品牌事实、
   Bearer token 或供应商原始响应。

## 回滚

切换后出现协议错误、持续超时、错误率异常或内容质量闸门失败时：

1. 立即把 `app.env` 恢复到备份，确认 `CONTENT_ENGINE=qwen`。
2. 再次只重建 Web：

   ```bash
   docker compose -f compose.production.yaml up -d --no-deps web
   ```

3. 复核 `/api/health` 和一条不保存生成请求。确认 Qwen 恢复后，可停止候选服务：

   ```bash
   docker compose -f compose.production.yaml --profile colleague stop content-engine
   ```

禁止使用 `docker compose down -v`、`docker system prune`，也不要删除
`runtime/`。内容引擎没有业务卷，因此停止它不会修改 GEO 数据。

## 两天商用版的 Bilibili 硬门槛

当前 Bilibili 适配器会从 GitHub `releases/latest` 动态下载 `biliup` 可执行文件，
尚未固定版本与 SHA-256，也未完成安全解包、非 root 执行和商业授权口径核验。
因此两天商用版必须保持以下运营硬门槛：

- `ALLOW_REAL_PUBLISHING=false` 作为客户环境默认值；
- 独立保持 `ENABLE_BILIBILI_RUNTIME=false`。该代码闸门会在调用下载器前阻断
  Bilibili 的账号检测、登录和真实发布工厂；即使开放其他平台也不得顺带开启；
- 不迁移或连接生产 Bilibili 账号，不创建 Bilibili 真实任务；
- 仅提供稿件/素材导出与人工发布说明；
- 只有在固定版本和哈希、安全解包、最小权限运行、SBOM/NOTICE 及书面商业授权
  全部完成后，才允许单独开放该渠道。

只有上述供应链和授权条件全部关闭并完成隔离验收后，才可显式设置
`ENABLE_BILIBILI_RUNTIME=true`；不得仅凭人工口头确认绕过该闸门。
