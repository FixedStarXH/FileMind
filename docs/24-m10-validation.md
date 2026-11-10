# 24 M10 验证记录

## 交付

- 增加生产静态前端镜像与 Nginx 反向代理；生产服务通过 `production` profile 启动，API、数据库、Redis 和前端端口默认绑定 loopback。
- Nginx 支持 SPA 回退、上传请求、长连接 SSE 禁缓冲和 `/health` 转发。
- 清理 `.env.example` 重复/冲突的模型配置，补充 Session、端口和本地开发配置。
- 增加 Age 加密备份及 `filemind-restore-*` 隔离恢复演练脚本。
- 添加四份全合成演示资料，可用于文本解析和检索，不含真实个人信息。

## 验证结果

| 项目 | 结果 | 证据/限制 |
|---|---|---|
| Compose 配置 | 通过 | 默认、`production`、`ops` profiles 均成功展开 |
| 前端生产镜像 | 通过 | `docker compose --profile production build web`；Node 22 构建完成，npm audit 显示 0 vulnerabilities |
| 前端服务 | 通过 | 临时 loopback 端口提供 FileMind 构建页面，`/health/ready` 代理返回 200；数据库、Redis、存储为 `ok`，DeepSeek 为 `not_configured` |
| Linux 脚本语法 | 通过 | 在临时 Alpine 容器中运行 `bash -n` 检查两个运维脚本 |
| 隔离恢复演练 | 通过（核心恢复链路） | 独立源/目标 Compose 项目；pg_dump + 文件卷 tar 恢复到新卷；恢复 API readiness 为 ready，合成数据库标记行与文件内容一致；之后清理了临时项目和卷 |
| Age 加密包装 | 未实测 | 当前 Windows 主机未安装 Age CLI，也未提供 Age 身份文件；恢复演练验证了备份脚本调用的 PostgreSQL/文件卷核心步骤，Age 密文生成、验签和已加密备份解密需在 Linux 运维机配置 Age 后复验 |
| 真实 HTTPS/域名 | 未实测 | Nginx 仅在 loopback 端口验证；生产需由外部 TLS 反向代理终止 HTTPS，并令 `APP_ORIGIN` 与公开 HTTPS 源一致 |

演练使用合成标记数据，不读取或复制日常项目文件。恢复脚本为目标服务请求 Docker 分配 loopback 临时端口，避免与本机既有服务冲突；演练容器、卷、网络和临时归档均已清理。

## 使用入口

- 本地开发部署：README 的 Docker Compose + Vite 步骤。
- 生产静态前端：`docker compose --profile production up -d --build`，需设置 `APP_ENV=production`、`APP_ORIGIN=https://实际域名` 并配置 HTTPS 反向代理。
- 合成资料：`examples/demo/`。
- 加密备份与恢复脚本：`scripts/backup.sh`、`scripts/restore-drill.sh`；依赖 Docker Compose、Age CLI、Age 公钥/私钥和 Linux Bash。脚本会使用短暂维护窗口，恢复演练只接受独立项目名前缀。

## 暂未证明的目标

本次不声称达到 RPO≤24小时或 RTO≤2小时；还未在 Linux 主机完成 Age 加密/解密、异机备份复制、完整用户文档与 blob hash 对账、真实 TLS 域名、定时轮换和灾难环境恢复计时。运行手册要求生产恢复后再执行这些核对。
