# M3 验收记录：持久任务、Outbox 与任务中心

日期：2026-11-09。运行版本：Celery 5.7.0、Redis 7.4、PostgreSQL 17。

## 已交付

- Alembic `0004_durable_tasks`：tasks 状态、attempt、租约、heartbeat、fencing token、取消标记、重试链；outbox 只保存 task ID。
- API 事务同时写入文件元数据、parse task 与 outbox；上传和新增版本返回202。M3 的 parse task 持久排队并显示 `waiting_parser`，不发送给解析队列；M4 启用 ingestion Worker 后接续处理。
- 永久删除现在返回202：文件转为 `purging` 并立即不可访问；后台 maintenance Worker 等待现有解析租约释放后，删除元数据和无共享引用的 blob，并回收用户额度。
- Celery worker late-ack、worker-lost requeue、prefetch=1；dispatcher 负责 Outbox 发布、租约过期恢复和排队任务重发。worker 只接收 task ID，业务状态以 PostgreSQL 为准。
- 任务 API：按当前用户列出/读取、取消可取消任务、重试失败任务；任务中心每5秒刷新，可查看排队、进行中、失败和完成状态。
- Compose 增加迁移、dispatcher、maintenance Worker；Worker 以具名非 root 用户运行。

## 检查结果

- Compose 配置校验通过；`docker compose up -d --build` 成功，迁移 head 为 `0004_durable_tasks`。
- API readiness 返回就绪，数据库、Redis 和文件存储均正常；DeepSeek 未配置状态符合预期。
- `ruff check src migrations/versions/0004_durable_tasks.py` 通过；`mypy src` 对20个源文件无问题。
- `npm run build` 通过，TypeScript 与 Vite 构建成功。
- HTTP 冒烟验证通过：上传202和 waiting_parser 状态、任务取消与重试幂等、回收站文件转 purging、永久删除202幂等重放、Worker 删除成功、配额归零。
- 租约恢复验证：暂停 maintenance Worker，将测试任务置于过期 running 租约，再启动 Worker；dispatcher 恢复任务，任务成功且 attempt 增至2。
- 所有临时测试账户、任务、资料文件和本地临时文件均已清理。

未完成 Edge 实机截图检查。租约恢复使用过期租约模拟，没有在长时间运行的真实解析进程中执行强制 kill。

## 当前边界

- M3 只运行 maintenance 队列。上传解析任务暂留数据库，直到 M4 提供解析处理器并设置 `ENABLE_INGESTION_WORKER=true`。
- 当前 worker 实际处理永久删除；OCR、解析、索引和任务进度仍由 M4/M5 实现。
- dispatcher 每秒轮询数据库；租约120秒、heartbeat每30秒；Redis visibility timeout 180秒。Redis 队列只用于通知，任务状态与恢复记录保存在 PostgreSQL。
