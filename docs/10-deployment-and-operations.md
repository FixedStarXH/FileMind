# 10 部署与运维设计

## 1. 运行拓扑

Windows 开发机使用 Docker Desktop/WSL2 的 Linux 容器；浏览器用 Edge。正式部署为单台 Linux 主机 + Docker Compose，前端与 API 同源。Compose 启动独立 ingestion、Agent 与维护 worker；Nginx 和 export worker 在后续模块加入。

服务：nginx、api、worker-ingestion、worker-agent、worker-export、worker-maintenance、dispatcher、postgres、redis、migrate。M5 ingestion worker 单并发、3 GiB 内存限制、业务文件卷只读；专用模型卷只对 ingestion worker 可写，API 只读。RapidOCR 模型随锁定 Python 包安装；首次 BGE 索引会从固定 revision 下载约95 MB ONNX 模型至模型卷，后续离线复用。API 与 worker 以非 root 用户运行。

默认 OCR 并发1、Agent并发2、导出并发1；API数据库池5+overflow5，worker每进程独立小池。参考资源4核8GiB，推荐开发机为容器预留至少8GiB，首次安装和大PDF需实测内存，不能把参考配置当保证。

M0 已确认 Docker 可用，但尚未验证 OCR 与向量模型的峰值内存；在 M4/M5 按实际机器资源决定并发数。若机器无法分配上述资源，再讨论降低模型/并发或使用服务器。

只公开 Nginx HTTP/HTTPS 端口；PostgreSQL/Redis 不映射公网；nginx 配置52MiB请求上限、上传超时、SSE禁缓冲与长连接。生产必须HTTPS，本地绑定127.0.0.1。

## 2. 配置与启动流程

配置见根目录 `.env.example`；空 Key 可启动基础功能，`AI_ENABLED` 不替代每用户的 ai_enabled 授权。DB/Redis凭据和SESSION_SECRET必须由部署者生成，示例不提供可投入生产的默认密码。

实施后流程：检查Docker/WSL → 复制配置并生成本地秘密 → 构建锁定依赖的镜像 → 启动数据库和 Alembic 迁移 → 管理员命令创建账户 → 启动服务 → 检查 ingestion worker 与健康状态。首次索引需允许 ingestion worker 访问 Hugging Face 下载固定 revision；模型资产只写入 Docker 模型卷，不进入 Git。

`.env.example` 是设计期配置名规范，实际代码必须逐项接入并校验；禁止不生效的配置留在正式发布模板。密钥不进入前端构建变量。

## 3. 监控与诊断

- 结构化JSON日志关联 request_id/task_id/run_id/owner匿名标识，不记录正文与密钥。
- 指标：请求延迟、错误率、队列等待时长、任务失败/重试/过期租约、OCR吞吐、模型token、LLM错误、磁盘剩余、孤儿文件数量。
- 诊断页只给管理员/本机运维访问；记录所用模型与pipeline版本。首版日志与Prometheus文本指标端点即可，Grafana/LangSmith作为后续可选服务。
- 磁盘剩余低于10%告警并停止新上传/导出；已有文件保持可读。

## 4. 备份与恢复

每天一次数据库与文件卷一致性备份，保留7天，备份加密并保存在不同磁盘/主机。首版采用短维护窗口：停止写请求，暂停调度，等待活动写事务/文件发布结束，备份PostgreSQL和文件卷，再恢复服务。仅备份DB无法恢复文件。

目标RPO≤24小时、RTO≤2小时，需要演练证明。恢复后先根据仍可用的删除记录重放清理、校验所有blob引用与校验和，重建可再生索引，撤销原Session、过期审批和导出链接，再开放服务。删除记录缺失时不能直接开放恢复实例，需人工核查或等待相应备份过期。Redis不作为权威数据备份，任务从DB恢复投递。

## 5. 故障处理

| 故障 | 行为 |
|---|---|
| DeepSeek不可用/缺Key | 文件管理与本地搜索可用，AI显示不可用 |
| embedding不可用 | semantic返回503；hybrid明确降级keyword |
| Redis不可用 | 已提交outbox等待恢复；禁止无限积压，达到队列上限时返回503 |
| worker退出 | 租约到期恢复；旧fencing token不可提交 |
| PostgreSQL不可用 | readiness失败，拒绝写入；不返回假成功 |
| 文件卷异常/磁盘满 | 停止接收新上传，清理临时文件并释放额度 |
| 删除任务失败 | 保持purging不可访问，重试并告警 |

版本发布顺序：备份 → 兼容性迁移 → 新镜像 → 冒烟 → 恢复流量。旧worker先排空再替换，避免不同pipeline混写；需要不可兼容迁移时使用维护窗口。
