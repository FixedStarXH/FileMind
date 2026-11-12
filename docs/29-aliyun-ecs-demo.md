# 阿里云 ECS 轻量演示部署

适用环境：Alibaba Cloud Linux 3、2 vCPU、2 GiB 内存、40 GiB 系统盘。该配置仅适合少量合成文件和单人演示；LibreOffice PPTX 渲染 worker 限制为 1.5 GiB，转换大型 PPT 时可能与同机服务争用内存。部署前应确认余量，并避免在共享低内存服务器上同时处理大文件。演示环境设置 `EMBEDDINGS_ENABLED=false`，不加载 BGE 向量模型；DeepSeek Key 留空，因此 AI 问答、智能整理和语义检索不可演示。

## 1. 先检查服务器

在 ECS 远程终端执行 `free -h`、`df -h /`、`docker --version`、`docker compose version`。确认至少有约 10 GiB 可用磁盘空间，并确认现有容器和 80/443 端口用途。首次构建和 OCR 可能出现内存峰值；若没有 swap，先按服务器已有运维规则配置 2 GiB swap，并确认磁盘空间足够。安装 Docker Engine 和 Compose 插件可参考[阿里云 ECS 官方文档](https://help.aliyun.com/zh/ecs/user-guide/install-and-use-docker)。不要在不清楚现有服务用途时调整防火墙、覆盖反向代理或清理 Docker 卷。

## 2. 获取代码和配置

```bash
git clone https://github.com/FixedStarXH/FileMind.git
cd FileMind
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，至少设置：

```dotenv
POSTGRES_PASSWORD=<独立随机密码>
SESSION_SECRET=<独立随机密钥>
EMBEDDINGS_ENABLED=false
DEEPSEEK_API_KEY=
ENABLE_INGESTION_WORKER=true
REGISTRATION_MAX_USERS=5
WEB_BIND=127.0.0.1:8080
API_BIND=127.0.0.1:8000
POSTGRES_BIND=127.0.0.1:5433
REDIS_BIND=127.0.0.1:6380
```

两个密钥分别运行 `openssl rand -hex 32` 生成，不能沿用示例空值或提交 `.env`。若只通过 SSH 隧道演示，设置 `APP_ENV=development` 和 `APP_ORIGIN=http://127.0.0.1:8080`；Nginx 仍使用打包后的生产前端。若要让其他人通过域名访问，先完成域名、备案和 HTTPS，再设置 `APP_ENV=production`、`APP_ORIGIN=https://你的域名`，将 80/443 的 TLS 代理指向本机 `127.0.0.1:8080`。不要将 8080、8000、5433、6380 直接开放给公网。

## 3. 启动轻量组合

在 2 GiB 主机上逐个构建镜像，以降低构建时内存峰值：

```bash
docker compose -f compose.yaml -f compose.demo.yaml --profile production config --quiet
for service in api migrate dispatcher worker-maintenance worker-ingestion web; do
  docker compose -f compose.yaml -f compose.demo.yaml build "$service" || exit 1
done
docker compose -f compose.yaml -f compose.demo.yaml --profile production up -d --no-build
docker compose -f compose.yaml -f compose.demo.yaml --profile production ps
curl -fsS http://127.0.0.1:8080/health/ready
```

`compose.demo.yaml` 使 PostgreSQL 使用较小缓存、两个 Worker 使用单进程，并使 Agent Worker 不随 `production` profile 启动。首次迁移和解析可能需要等待。若健康检查失败，查看 `docker compose -f compose.yaml -f compose.demo.yaml logs --tail=100 api migrate dispatcher worker-ingestion worker-maintenance web`，以及 `docker stats --no-stream`、`free -h`、`df -h /`。

## 4. 演示入口与资料

SSH 隧道方式：在自己电脑上运行 `ssh -N -L 8080:127.0.0.1:8080 <服务器登录用户>@<ECS公网IP>`，保持该终端打开，然后在 Edge 打开 `http://127.0.0.1:8080`。阿里云安全组仅需允许你的 IP 访问 SSH 端口，不需为 FileMind 开放其他端口。此方式适合本人演示或屏幕共享；其他人不能直接打开该本地地址。

通过网页注册演示账号，上传仓库 `examples/demo/` 中的六份虚构资料，等任务完成后演示预览、正文关键词检索、标签/文件夹和手动导出。需要固定演示账号时，也可执行 `docker compose -f compose.yaml -f compose.demo.yaml exec api /app/.venv/bin/filemind create-user demo@example.com`，按提示交互设置至少 12 位密码。不要把真实简历、证书或演示密码提交到仓库。

访问人数增加或需要展示 AI 问答时，先扩容服务器、配置 DeepSeek Key，再启用向量模型和 Agent Worker；不要直接在 2 GiB 方案上开启完整 AI 功能。
