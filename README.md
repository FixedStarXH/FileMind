# FileMind · 知档

面向大学生的 AI 个人学习与求职资料管理系统。将课件、论文、证书和简历变成可检索、可引用、可整理的个人资料库。

**当前状态：M10 部署、演示资料与恢复演练工具已交付。** Python 固定为 3.12；项目名为工作名。DeepSeek Key 尚未配置，因此真实模型效果仍待联调；20k 检索、OCR 等性能指标尚未实测，不作为已达成成绩。

## 已确认的产品边界

- 场景：个人学习与求职资料。
- 形态：网页应用，电脑优先，兼容手机浏览器；Edge 为主要验收浏览器。
- AI：DeepSeek API，本地 OCR、本地向量模型；API Key 留空。
- 首版：单个自托管实例可有多个账户，每个账户拥有独立私人资料库，无团队协作与公开分享。
- 开发顺序：文档 → 兼容性验证 → 分模块实现与验收 → 模块提交。GitHub 目标仓库：[FixedStarXH/FileMind](https://github.com/FixedStarXH/FileMind)。

## 本地开发

先安装 Python 3.12、uv、Node.js 20.19+ 与 Docker Desktop，在项目根目录复制 `.env.example` 为 `.env`，生成强 `POSTGRES_PASSWORD` 和 `SESSION_SECRET`。本地 Vite 开发时将 `APP_ORIGIN` 设为 `http://127.0.0.1:5173`，启动数据库、Redis 与 API：

```powershell
docker compose up -d --build
```

打开 `http://127.0.0.1:8000/health/live` 和 `http://127.0.0.1:8000/health/ready`。Docker Compose 会在启动时应用迁移，并运行解析、Agent 与维护 Worker。也可在本地 Python 环境执行迁移；将 `.env` 的 `DATABASE_URL` 设为 `postgresql+psycopg://filemind:<POSTGRES_PASSWORD>@127.0.0.1:5433/filemind`，`REDIS_URL` 设为 `redis://127.0.0.1:6380/0`，`STORAGE_ROOT` 设为 `./data/files`，然后运行：

```powershell
uv sync --dev
uv run alembic upgrade head
uv run uvicorn filemind.main:app --app-dir src --reload
```


基础 API 默认监听 `http://127.0.0.1:8000`。本地 Python 进程与 Compose API 容器不能同时占用该端口，切换时先停止另一进程。`/health/live` 检查进程，`/health/ready` 在数据库、Redis、文件目录全部可用时返回就绪。DeepSeek Key 可留空，健康检查会显示 `not_configured`。

用户可在网页选择“注册新账户”，使用邮箱和至少12位密码自行注册；默认最多20个账户，可通过 `.env` 的 `REGISTRATION_MAX_USERS` 调整，也可通过 `REGISTRATION_ENABLED=false` 关闭。管理员仍可在终端执行 `docker compose exec api /app/.venv/bin/filemind create-user student@example.com` 创建账户。启动前端开发服务器：

```powershell
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173` 登录。资料库支持上传、下载、重复文件提示、文件夹与标签、版本、收藏及回收站；永久删除由后台任务处理。任务中心可查看、取消或重试任务。文档解析任务提取 PDF、DOCX、PPTX、TXT、Markdown、PNG 和 JPEG，生成页码/段落/幻灯片预览定位和检索索引。正文搜索支持 jieba 中文关键词、BGE 语义与混合排序；首次索引会将固定 revision 的本地 ONNX 模型下载到 Docker 模型卷（约95 MB），无网络或模型不可用时关键词检索仍可用。AI 问答可选择全库、收件箱、文件夹或标签范围，答案附可定位引用；连续追问最多带入最近12条未删除消息。资料整理 Agent 可基于有限资料范围生成改名、移动和标签建议，逐项审批后原子应用；审计记录可查询且不保存正文。求职资料包可推荐有证据的材料并提示缺项，审批后异步生成 ZIP；也可手动选择已识别文件导出。产物计入配额，24小时到期或源文件移入回收站后失效并清理。无 DeepSeek Key 时 API 明确返回未配置状态，不创建 AI 任务。

生产静态前端以 `docker compose --profile production up -d --build` 启动，默认仅监听 `127.0.0.1:8080`。将 `.env` 的 `APP_ENV` 设为 `production`、`APP_ORIGIN` 设为对外 HTTPS 源，并在本机 TLS 反向代理后使用；不要直接暴露容器端口。演示材料位于 `examples/demo/`，均为合成数据。加密备份和隔离恢复演练见 [部署与运维](docs/10-deployment-and-operations.md)。

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
| [16 M2 验证记录](docs/16-m2-validation.md) | 文件生命周期、入库与接口验收结果 |
| [17 M3 验证记录](docs/17-m3-validation.md) | Outbox、Worker 租约、任务中心与恢复验收结果 |
| [18 M4 验证记录](docs/18-m4-validation.md) | 文档解析、OCR、预览接口与定位验收结果 |
| [19 M5 验证记录](docs/19-m5-validation.md) | 中文关键词、BGE 向量、混合检索和评测结果 |
| [20 M6 验证记录](docs/20-m6-validation.md) | 带引用问答、持久化图状态、SSE 回放与缺 Key 行为 |
| [21 M7 验证记录](docs/21-m7-validation.md) | 整理计划、人工审批、事务应用、幂等恢复与审计 |
| [22 M8 验证记录](docs/22-m8-validation.md) | 求职资料包、人工审批、手动导出、ZIP、配额和过期清理 |
| [23 M9 验证记录](docs/23-m9-validation.md) | Edge 端到端、手机视口、安全边界、性能与模型评测限制 |
| [24 M10 验证记录](docs/24-m10-validation.md) | 生产前端容器、合成演示资料、备份恢复和部署验收 |
| [25 使用流程手册](docs/25-user-guide.md) | 登录、上传、检索、整理、导出与常见问题 |
| [26 M11 验证记录](docs/26-m11-validation.md) | 网页注册、双账户会话和 Edge 页面验收 |
| [配置模板](.env.example) | 无真实密钥的目标配置；应用实现后生效 |

先阅读 01、02、07 确认产品与使用体验，再按 03 → 04 → 05 → 06 进入实现。规格冲突以 01 的产品范围与 05 的外部契约为准，并同步修订所有受影响文档。
