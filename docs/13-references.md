# 13 官方参考与核对记录

核对日期：2026-11-09。以下用于理解组件能力；具体版本兼容性仍由模块验证，不以网页描述替代运行测试。

| 来源 | 本方案使用的依据 |
|---|---|
| [DeepSeek API 起步](https://api-docs.deepseek.com/) | Chat API接入、模型名配置化；当前文档示例包含deepseek-flash |
| [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) | 模型提出工具调用，应用侧校验与执行；本方案不依赖beta strict |
| [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) | 当前思考模式默认开启，首版需显式关闭；若以后开启，工具调用必须回传 reasoning_content |
| [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 持久化checkpoint与跨中断恢复 |
| [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | 暂停等待输入并恢复；业务副作用仍须自行幂等 |
| [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL向量检索，可与全文检索组合 |
| [FastEmbed](https://qdrant.github.io/fastembed/) | 本地 ONNX 文本向量生成和模型清单 |
| [Qdrant BGE-small-zh ONNX 模型](https://huggingface.co/Qdrant/bge-small-zh-v1.5/commit/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59) | MIT 许可；FileMind 固定至该 revision，512 维 |
| [FastEmbed supported models](https://qdrant.github.io/fastembed/) | BGE-small-zh 模型名、维度及 MIT 元数据 |
| [jieba](https://github.com/fxsjy/jieba) | 中文分词；查询与文档按同一 cut_for_search 规则生成关键词 |
| [PostgreSQL 全文控制](https://www.postgresql.org/docs/current/textsearch-controls.html) | tsvector/tsquery、排名、字段权重；中文分词由本方案补充 |
| [FastAPI Docker 部署](https://fastapi.tiangolo.com/deployment/docker/) | 构建应用容器与部署 |
| [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) | 异步会话与并发边界；任务独立session |
| [Celery Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html) | 重投、ack、幂等、超时的重要性 |
| [Celery Redis transport](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) | Redis visibility timeout 与 worker 退出时未确认任务的重投行为；FileMind 同时用数据库租约与 Outbox 作为恢复依据 |
| [Celery FAQ](https://docs.celeryq.dev/en/stable/faq.html) | Celery 不正式支持 Windows，worker 用 Linux 容器运行 |
| [BGE 中文小模型](https://huggingface.co/BAAI/bge-small-zh-v1.5) | 模型用途、维数与推理方式，实施时固定revision |
| [RapidOCR 使用文档](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/) | Python API、文本框坐标、置信度与ONNX Runtime后端 |
| [RapidOCR 安装说明](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/) | 安装方式、模型随包分发及 headless OpenCV |
| [RapidOCR 许可证与模型归属](https://github.com/RapidAI/RapidOCR) | Apache-2.0 源代码和模型说明；模型源自 PaddleOCR，遵循其适用许可 |
| [PDF.js](https://mozilla.github.io/pdf.js/) | 浏览器PDF渲染 |
| [pdfjs-dist on npm](https://www.npmjs.com/package/pdfjs-dist) | PDF.js 浏览器包；实际版本由 frontend/package-lock.json 锁定 |
| [Vite](https://vite.dev/guide/) | 前端开发/构建，Node要求需与选定版本核对 |
| [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 文件管理、OCR与归档流程参考；本项目独立设计学生场景 |

外部仓库仅作为能力和交互参考；复用代码前另行核对许可证和署名义务。技术栈表中的选型理由属于本项目工程判断。
