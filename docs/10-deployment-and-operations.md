# 10 部署与运维

## 1. 运行拓扑

FileMind 以 Docker Compose 运行 Python 3.12 API、PostgreSQL/pgvector、Redis、迁移容器、资料解析 Worker、Agent Worker、维护 Worker 和 Outbox dispatcher。生产静态前端由 Nginx 提供，使用 `production` profile 启动；Vite 只用于开发。API、PostgreSQL、Redis 与 Nginx 的宿主机端口默认绑定 loopback，不直接暴露到公网。

Nginx 提供 SPA 路由回退、52 MB 上传上限、API 反向代理和 SSE 禁缓冲。将其置于 TLS 反向代理后面时，公网只能到达 TLS 代理；`APP_ORIGIN` 必须与浏览器地址完全一致，生产环境设为 `https://...`，以启用 Secure Session Cookie。生产环境不得直接通过 HTTP 公网访问。

解析 Worker 单并发，业务文件卷只读，模型卷可写；RapidOCR 使用锁定 Python 依赖，首次 BGE 索引需下载固定 revision 的 ONNX 模型到模型卷。API 与 Worker 使用非 root 用户。Agent Worker 并发由 `AGENT_CONCURRENCY` 控制，默认1；OCR并发1，导出并发1。模型卷可重建，不进入备份；PostgreSQL 和业务文件卷必须一起备份。

## 2. 配置和启动

1. 安装 Docker Desktop/WSL2（本地）或 Docker Engine + Compose plugin（Linux 主机）。本地开发还需 Python 3.12、uv 和 Node.js 20.19+。
2. 复制 `.env.example` 为 `.env`，生成独立随机的 `POSTGRES_PASSWORD` 与 `SESSION_SECRET`。不得沿用示例空值，也不要把 `.env` 加入 Git。
3. 本地 Vite 开发时设置 `APP_ORIGIN=http://127.0.0.1:5173`，然后在项目根目录运行：

   ```powershell
   docker compose up -d --build
   ```

4. 等待 `http://127.0.0.1:8000/health/ready` 返回 200，再启动前端：

   ```powershell
   cd frontend
   npm ci
   npm run dev
   ```

5. 通过 `docker compose exec api /app/.venv/bin/filemind create-user <email>` 创建首个账户。密码通过终端交互输入，不作为命令参数传递。

生产部署将 `.env` 设置为 `APP_ENV=production`、`APP_ORIGIN=https://实际域名`，并设置 `WEB_BIND=127.0.0.1:8080`，然后运行：

```bash
docker compose --profile production up -d --build
```

TLS 终止代理应将 HTTPS 请求转发到 `http://127.0.0.1:8080`，并保留原始 Host。数据库和 Redis 端口也仅绑定 loopback；若同机其他服务无需访问，不要改为 `0.0.0.0`。不要在生产开启 Vite dev server。部署前从可信通道安装 Age，并将备份私钥保存在部署主机之外。

容器启动会先等待 PostgreSQL/Redis 健康检查，再运行 Alembic migration；API readiness 会检查数据库、Redis 和文件目录。DeepSeek Key 可留空，基础文件管理与本地检索仍可用。首次 BGE 索引需要网络访问固定模型 revision，随后可离线使用。

## 3. 演示资料

`examples/demo/` 提供虚构简历、课程笔记、论文阅读卡片和证书清单，全部为 Markdown/TXT 合成内容，不含真实个人资料或真实研究结论。创建临时用户后手动上传这些文件，可展示文本解析、搜索和求职资料整理。无 DeepSeek Key 时只演示文件处理与关键词检索；不要为演示而将 Key 或个人资料提交到仓库。

## 4. 备份

`scripts/backup.sh <目录>` 会短暂停止 API、dispatcher 和各 Worker，使用 `pg_dump` 备份 PostgreSQL，并归档 `filemind_files` 卷；完成后自动尝试恢复服务。模型缓存和 Redis 不纳入备份：模型可下载重建，任务由数据库 Outbox 恢复。脚本把数据流打包后用 Age 公钥加密，生成的 `.age` 文件应复制到另一台主机或离线存储，并按7天策略轮换。

```bash
export AGE_RECIPIENT='age1...'
scripts/backup.sh /mnt/offsite/filemind
```

备份期间会有短暂维护窗口。若脚本异常退出，先检查容器状态与日志，再确认 API 和 Worker 已重新运行。只备份数据库无法恢复文件内容；只备份文件卷也不能恢复元数据与权限关系。

## 5. 隔离恢复演练

`scripts/restore-drill.sh <backup.age> <filemind-restore-名称>` 仅接受 `filemind-restore-*` 项目名，为演练创建独立 Compose volumes 和 Docker 分配的 loopback 临时端口；它不会覆盖源项目。脚本解密、校验归档内的 SHA-256 清单、恢复数据库与文件卷、撤销恢复数据库中的旧 Session，并检查 API readiness。

```bash
export AGE_IDENTITY=/secure/path/age-identity.txt
scripts/restore-drill.sh /mnt/offsite/filemind/filemind-日期.tar.gz.age filemind-restore-drill1
```

演练后检查演示账户能否登录、文档列表和预览能否打开、下载文件 hash 是否一致，并检查 Worker 是否恢复 Outbox 任务。确认演练数据不再需要后，可先查看 `docker compose -p <project> ps -a` 与 `docker volume ls`，再按项目名前缀逐一删除测试容器和卷；不要对日常项目运行 `down -v`。生产正式恢复前还应更新 `.env` 中的 Session Secret，执行 blob 文件/数据库引用一致性核对、过期任务处理与备份保留策略检查，然后再开放流量。

备份目标 RPO≤24小时、RTO≤2小时是运维目标；只有在记录数据规模、停机时长和恢复验证结果后才能声称达到。数据库与业务文件备份必须来自同一维护窗口。Redis不是权威状态；worker 从持久化任务/outbox恢复。

## 6. 监控、诊断和故障

- `GET /health/live` 检查 API 进程；`GET /health/ready` 检查数据库、Redis 和文件目录。DeepSeek `not_configured` 不会使基础 readiness 失败。
- `docker compose ps` 查看容器状态；`docker compose logs --tail=200 api worker-ingestion worker-agent worker-maintenance dispatcher` 查看服务日志。不要将日志中的用户内容复制到公开 issue。
- 重点观察 API 错误率和延迟、队列等待、任务失败与租约回收、磁盘剩余、模型缓存增长、OCR内存以及文件引用完整性。
- 低磁盘空间、存储卷不可写、数据库不就绪时应停止新上传；不要通过删除 PostgreSQL/文件卷解决故障。
- DeepSeek 缺Key/超时：基础文件管理继续可用，AI功能显示不可用；embedding 不可用：语义检索返回明确错误或混合检索降级到关键词；Redis异常：依赖队列的操作返回错误，恢复后由持久化 Outbox 重投。

## 7. 发布顺序

发布前先生成加密备份并确认镜像构建成功；运行迁移，再启动新 API/Worker，检查 readiness 与浏览器关键路径。不可兼容迁移需维护窗口和回滚方案。恢复旧镜像前必须确认迁移兼容性；不得盲目回滚数据库 schema。
