# FileMind · 知档

面向大学生的 AI 个人学习与求职资料管理系统。将课件、论文、证书和简历变成可检索、可引用、可整理的个人资料库。

**当前状态：M1 账户与登录模块已完成。** Python 固定为 3.12；项目名为工作名。技术选型是实施方案，性能数字是验收目标，不是已测成绩。

## 已确认的产品边界

- 场景：个人学习与求职资料。
- 形态：网页应用，电脑优先，兼容手机浏览器；Edge 为主要验收浏览器。
- AI：DeepSeek API，本地 OCR、本地向量模型；API Key 留空。
- 首版：单个自托管实例可有多个账户，每个账户拥有独立私人资料库，无团队协作与公开分享。
- 开发顺序：文档 → 兼容性验证 → 分模块实现与验收 → 模块提交。GitHub 目标仓库：[FixedStarXH/FileMind](https://github.com/FixedStarXH/FileMind)。

## 本地开发

先安装 Python 3.12、uv、Node.js 20.19+ 与 Docker Desktop，在项目根目录复制 `.env.example` 为 `.env`，生成强 `POSTGRES_PASSWORD` 和 `SESSION_SECRET`。启动数据库、Redis 与 API：

```powershell
docker compose up -d --build
```

打开 `http://127.0.0.1:8000/health/live` 和 `http://127.0.0.1:8000/health/ready`。数据库迁移在本地 Python 环境执行；将 `.env` 的 `DATABASE_URL` 设为 `postgresql+psycopg://filemind:<POSTGRES_PASSWORD>@127.0.0.1:5433/filemind`，`REDIS_URL` 设为 `redis://127.0.0.1:6380/0`，`STORAGE_ROOT` 设为 `./data/files`，然后运行：

```powershell
uv sync --dev
uv run alembic upgrade head
uv run uvicorn filemind.main:app --app-dir src --reload
```

基础 API 默认监听 `http://127.0.0.1:8000`。本地 Python 进程与 Compose API 容器不能同时占用该端口，切换时先停止另一进程。`/health/live` 检查进程，`/health/ready` 在数据库、Redis、文件目录全部可用时返回就绪。DeepSeek Key 可留空，健康检查会显示 `not_configured`。

M1 账户初始化：API 容器运行且迁移完成后，在终端执行 `docker compose exec api /app/.venv/bin/filemind create-user student@example.com`，按提示输入至少12位密码。启动前端开发服务器：

```powershell
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173` 登录。当前页面提供登录、存储配额、AI 启用状态和退出；文件管理会在后续模块接入。

## 文档导航

| 文档 | 内容 |
|---|---|
| [01 产品需求](docs/01-product-requirements.md) | 用户故事、版本范围、文件类型、验收条件 |
| [02 技术选型与决策](docs/02-technology-decisions.md) | 技术栈、替代方案、版本策略和验证项 |
| [03 系统架构](docs/03-architecture.md) | 模块、数据流、状态机、一致性、任务恢复 |
| [04 数据库设计](docs/04-database.md) | 表、字段、索引、租户隔离、生命周期 |
| [05 接口契约](docs/05-api-contract.md) | HTTP、鉴权、请求响应、错误码、SSE |
| [06 Agent 与检索](docs/06-agent-and-retrieval.md) | 工具、图流程、审批、引用、模型预算 |
| [07 页面与交互](docs/07-ui-and-interactions.md) | 页面结构、关键交互、状态、响应式规则 |
| [08 安全与数据治理](docs/08-security-and-data.md) | 文件安全、云端数据、密钥、隔离、删除 |
| [09 测试与评测](docs/09-testing-and-evaluation.md) | 功能、故障、Agent 指标和发布门槛 |
| [10 部署与运维](docs/10-deployment-and-operations.md) | 容器、配置、备份、监控、故障处理 |
| [11 开发计划](docs/11-delivery-plan.md) | 模块依赖、每模块提交、完成标准 |
| [12 追踪与设计审查](docs/12-traceability-and-review.md) | 需求映射、风险、实施前验证与已决事项 |
| [13 官方参考](docs/13-references.md) | 核对过的官方来源 |
| [14 M0 验证记录](docs/14-m0-validation.md) | 基础工程的实际运行结果与限制 |
| [15 M1 验证记录](docs/15-m1-validation.md) | 账户模块实现、接口检查与当前限制 |
| [配置模板](.env.example) | 无真实密钥的目标配置；应用实现后生效 |

先阅读 01、02、07 确认产品与使用体验，再按 03 → 04 → 05 → 06 进入实现。规格冲突以 01 的产品范围与 05 的外部契约为准，并同步修订所有受影响文档。
