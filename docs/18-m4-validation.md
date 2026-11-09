# M4 验收记录：文档解析、本地 OCR 与预览

日期：2026-11-09。Python 3.12.14 容器；关键依赖版本以 `uv.lock` 与 `frontend/package-lock.json` 为准。

## 已交付

- Alembic `0005_document_parsing` 新增按 owner/version/generation 隔离的 `extracted_blocks`；每个块保存类型、文本、原文 locator 与 OCR 置信度。
- ingestion Celery worker 独占 `ingestion` exchange/routing key；maintenance 使用独立 `maintenance` exchange/routing key。上传任务从 `waiting_parser` 进入解析；worker 单并发、最多 3 GiB、原文件卷只读。
- PDF 原生文本与低文本页扫描 OCR、DOCX 段落和表格、PPTX 文本和表格、UTF-8 TXT/MD、PNG/JPEG EXIF 校正与 RapidOCR 均有实现。PDF 最多200页；OCR 页面栅格最多2,000万像素；图片最多4,000万像素；文本块数量/长度及 DOCX 段落数有界。
- OCR 使用 `rapidocr` + ONNX Runtime CPU。依赖锁定为 rapidocr 3.10.0、onnxruntime 1.31.1；默认 OCR 模型随 wheel 安装，不需提交权重或在首个用户任务时联网下载。RapidOCR 项目与模型许可证说明为 Apache-2.0；依赖元数据和许可证来源列于 [官方参考](13-references.md)。
- 新增按 owner 授权的版本预览、PDF/图片 inline content 与单 Range 读取接口，以及失败后重新解析 API。重试生成新的持久任务，不会并行解析同一版本。
- 资料库显示解析阶段并自动刷新；任务中心显示提取/OCR 阶段和常见失败原因。预览页使用 PDF.js 显示 PDF、图片 OCR 框、Word/PowerPoint/文本结构化内容和原文位置；低置信 OCR 有提示。
- M4 首版上传设置 `latest_version_id`，但在 M5 建立检索索引前不激活 `current_version_id`。解析完成的 `ready` 表示已提取，可预览，不代表可检索。

## 检查结果

- `docker compose config --quiet`、Docker 镜像构建、PostgreSQL migration head `0005_document_parsing` 成功；API readiness 为 ready，DeepSeek 显示 not_configured。
- ingestion Worker 日志确认 `ingestion` exchange/key；maintenance Worker 日志确认 `maintenance` exchange/key，两队列不再竞争同一路由。
- Ruff 和格式检查通过；mypy 对22个源文件无问题；`npm run build` 通过，PDF.js 被拆为按需预览 chunk。
- 临时真实 API/Worker 上传验收：原生 PDF、扫描 PDF、DOCX（段落与表格）、PPTX（文本与表格）、TXT、MD、PNG、JPEG 均成功并返回结构化块；扫描 PDF/图片返回带 bbox 的 OCR 块。
- 当前版本尚未建立 M5 检索索引时，重复上传仍能基于最新版本识别为重复，并返回 `200 duplicate=true`。
- 加密 PDF 任务以 `ENCRYPTED_PDF` 失败，原件下载仍为200；PDF `Range: bytes=0-15` 返回206与16字节；重新解析接口返回202并成功完成。
- 验收后清除临时账户、任务、文档、blob 和文件；数据库确认临时账户为0。DeepSeek Key 和模型权重均未写入仓库。

## 当前限制

- M4 保存结构化提取结果；全文/关键词/向量索引和“可检索”状态属于 M5。
- 首版不识别 PPTX 内嵌图片文字，不承诺复杂公式、手写、Word 原样排版或跨页表格重排。PDF OCR 页面框以页面点计，图片框以 EXIF 校正后的像素计。
- OCR 在单页调用内不承诺硬超时；任务在页边界响应取消，worker 异常退出由 Celery 重投恢复。
- 已完成浏览器构建和接口验收；本模块未留 Edge 实机截图，正式端到端 Edge/手机视口检查安排在 M9。
