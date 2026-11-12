# 02 技术栈与决策记录

## 1. 推荐方案

| 层 | 选型 | 选择原因与约束 |
|---|---|---|
| 前端 | React + TypeScript + Vite | 单页工具应用，无 SEO/服务端渲染需求；由 Nginx 同源提供 |
| UI | Tailwind CSS + shadcn/ui + Lucide | 构建文件列表、对话框、命令菜单；保留自定义视觉能力 |
| 前端数据 | React Router + TanStack Query；局部 React state | 服务端状态与页面状态分开；首版不另加全局状态库 |
| 预览 | PDF.js、图片、清洗后的 Markdown/结构化文本；仅解析 worker 安装 LibreOffice Impress | PDF 和 PPTX 按整页预览并适配窗口；Office 不做在线编辑 |
| Python | Python 3.12 + uv | 兼顾解析/推理依赖兼容性；提交 uv.lock |
| API | FastAPI + Pydantic v2 | 类型校验、OpenAPI、流式事件接口 |
| 数据访问 | SQLAlchemy 2.x + Alembic + psycopg 3 | API 异步会话，worker 同步事务；每个任务独立会话 |
| 数据库 | PostgreSQL 17 + pgvector | 业务数据、关键词索引、向量、任务/审批权威状态在一个数据库 |
| 任务 | Celery + Redis 7.4 | 解析、模型、导出分队列；数据库保存业务任务状态 |
| Agent | LangGraph + PostgreSQL checkpointer | 可暂停审批、可恢复状态图；单 Agent 工具循环 |
| LLM | DeepSeek Chat API，经独立 httpx 适配层 | Key 为空，模型名配置化；工具参数本地校验 |
| OCR | `rapidocr` 3.10 + ONNX Runtime CPU | 本地识别；锁文件固定依赖，默认模型随 RapidOCR wheel 安装，不在首次用户请求时联网下载 |
| 文本提取 | pypdf、pypdfium2、python-docx、python-pptx | 文本提取与页面渲染分离；LibreOffice 只生成 PPTX 预览副本，不参与检索 |
| 向量 | BAAI/bge-small-zh-v1.5 + FastEmbed/ONNX Runtime | 中文优先、512 维、CPU 可运行；模型仓库与 commit 固定；英文质量另外验证 |
| 关键词 | jieba + PostgreSQL tsvector/GIN | 文档与查询同样分词后使用 simple 配置；支持中文全文召回 |
| 文件存储 | 本地持久卷 + Storage 接口 | 单机部署简单；未来可替换 S3 兼容存储，首版不引入 MinIO |
| 部署 | Docker Compose + Nginx；Windows 使用 WSL2/Linux 容器 | worker 运行在 Linux 环境，统一运行依赖 |
| 质量 | pytest、Ruff、mypy、Vitest、Playwright | 集成验证实际 PostgreSQL/Redis；E2E 包含 Edge |

M4 锁文件解析组件版本：pypdf 7.0.0、pypdfium2 5.15.1、python-docx 1.2.0、python-pptx 1.0.2、rapidocr 3.10.0、onnxruntime 1.31.1；M5 锁 FastEmbed、jieba、pgvector Python adapter；前端 PDF.js 锁定在 `frontend/package-lock.json`。实际版本以 `uv.lock` 与 npm 锁文件为准。RapidOCR 与 FastEmbed 都运行 ONNX Runtime CPU。BGE 权重使用 `Qdrant/bge-small-zh-v1.5` 的固定 revision `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`，下载到独立模型卷，不进入 Git；模型文件许可证为 MIT。

这里的版本是已验证的锁定版本，不宣称长期最新。基础镜像与服务镜像使用具体版本标签；发布前记录实际镜像摘要。前端 Node 采用满足所选 Vite engines 的 Node 22 LTS 补丁版本。禁止生产使用浮动 latest 标签。

## 2. 核心决策

**ADR-001：模块化单体 + 独立 worker。** API、任务与 Agent 使用同一 Python 代码库，减少跨服务事务与部署成本。OCR worker 单独进程隔离资源；不是微服务拆分。

**ADR-002：先用 PostgreSQL 统一存储。** 1,000 文件规模优先精确向量检索；在实测需要时引入 HNSW 并复测过滤召回。pgvector 支持向量检索与混合检索组合，但不能代替中文分词。暂不采用 Elasticsearch/Milvus。

**ADR-003：LangGraph 负责 Agent 控制流，Celery 负责任务投递。** Celery 不保存审批业务真相，LangGraph 不直接授予写权限；所有写工具经过业务服务与审批校验。

**ADR-004：DeepSeek 和 Embedding 分开。** Chat API 用于规划和回答，本地 BGE 用于向量检索；模型能力必须通过小样本测试，不能因接口兼容就假设某参数支持。推荐初始模型 `deepseek-flash`，配置 `DEEPSEEK_MODEL`，正式联调核对账户可用性。首版 Chat API 显式设置 `thinking.type=disabled`，避免默认思考模式在多轮工具调用中要求回传 `reasoning_content`；若未来启用思考模式，必须单独实现并验证上下文回传与预算控制。不依赖 beta strict 模式；服务端始终验证 JSON Schema。

**ADR-005：先不加独立 reranker、GraphRAG、多 Agent、MCP。** 首版的新技术落在混合检索、工具调用、持久化状态、人工确认和可测评上。只有错误分析证明需要时才增量引入重排或多语言模型。

**ADR-006：服务端 Session Cookie。** 同源网页采用数据库 Session 与 CSRF 校验，避免前端长期存储访问令牌；未来第三方 API 再考虑独立令牌体系。

## 3. 分阶段兼容性验证门槛

1. M0：Python 3.12、锁文件、API 容器、PostgreSQL/Redis、初始迁移和健康检查在目标容器组合运行。
2. M4：CPU 上验证 PDF 原生文本、扫描 OCR、DOCX/PPTX、TXT/MD、PNG/JPEG；记录包版本和部署资源限制。OCR 权重随 RapidOCR Python 包；核对包许可证，不另行下载模型文件。
3. M5：BGE 实际输出 512 维；按 jieba token span 分段并保留源 locator，查询指令仅用于查询侧；中英样本评测关键词、语义和混合检索。中文分词样本含“操作系统”“操作 系统”“计网/计算机网络”，分词与 chunk 规则版本化。
4. M6：问答模式使用 LangGraph + PostgreSQL checkpointer，DeepSeek Chat API 由 httpx 适配；答案结构、引用编号与来源状态在服务端校验。当前 Key 未配置，真实模型回复、usage 与质量评测待配置后执行；无 Key 时只跑标明 mock 的合约验证。跨中断恢复和 Celery 重投仍需后续专项验证。
5. 每阶段确认相应依赖可在目标容器组合安装并记录许可证；发现阻断性兼容问题再调整，不无故升级技术栈。

官方依据见 [13 官方参考](13-references.md)。
