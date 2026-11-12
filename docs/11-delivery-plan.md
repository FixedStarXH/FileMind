# 11 开发与模块提交计划

每个模块完成“代码、必要检查、文档更新、可演示验收”后创建一个独立Git提交。用户已同意开始开发；GitHub目标为 `https://github.com/FixedStarXH/FileMind`，与旧WebPilot仓库分开。发现需求歧义先提问；已在本文档确定的技术实现不反复请求许可。

| 模块 | 内容 | 依赖 | 验收与建议提交名 |
|---|---|---|---|
| D0 | 完整设计文档 | 无 | 本次文档；`docs: define FileMind v0.1 specification` |
| M0 | Python 3.12 基础兼容性、工程、锁文件、Compose、CI | D0 | 空库迁移、API容器、基础健康检查；`chore: bootstrap application and runtime` |
| M1 | 账户、Session/CSRF、配额基础、登录与设置页 | M0 | 登录/CSRF/改密/退出/限流/配额；`feat: add private accounts and quota` |
| M2 | 同步上传与 blob、文件夹/标签、版本、回收站 | M1 | 无AI可管理文件、删除恢复、重复上传、下载与配额更新；上传标记 not_started；`feat: implement document lifecycle` |
| M3 | Outbox、Celery租约、任务中心、取消恢复；上传/删除接入202任务 | M2 | kill worker后恢复、幂等；parse task 暂停等待 M4；`feat: add durable background processing` |
| M4 | 解析、OCR、结构化预览与定位（已完成） | M3 | 每种支持类型成功/失败样本；`feat: parse documents with local OCR` |
| M5 | 中文全文、BGE向量、混合检索 | M4 | 三组检索对照评测；`feat: add hybrid document retrieval` |
| M6 | DeepSeek adapter、LangGraph QA、引用、SSE | M5 | 引用正确定位、断线回放、缺Key处理；`feat: add cited document assistant` |
| M7 | 整理方案、审批、事务执行、审计 | M6 | 版本冲突、双击审批、重放不重复写；`feat: add approved file organization` |
| M8（已完成） | 求职资料包、缺项提示、ZIP和过期清理 | M7 | manifest一致、源文件删除使下载失效；`feat: generate application document packs` |
| M9（已完成） | 响应式联调、关键E2E、性能与安全复核 | M8 | Edge 桌面/375px 模拟视口关键路径及安全边界；性能与真实模型评测限制记录在 `docs/23-m9-validation.md`；`test: verify release workflows and evaluation` |
| M10（已完成） | 部署、恢复演练、演示数据、说明书 | M9 | 生产前端容器与健康代理通过；隔离 Compose 项目完成数据库/文件卷恢复冒烟，Age 加密脚本限制见 `docs/24-m10-validation.md`；`docs: deliver deployment and demo guide` |
| M11（已完成） | 网页自助注册与多人独立使用 | M10 | Edge 页面注册后自动登录、双账户独立会话、重复邮箱与来源校验；验证记录见 `docs/26-m11-validation.md`；`feat: enable self-service account registration` |
| M12（已完成） | 多格式文档预览阅读体验 | M11 | Edge 实际预览 PDF、DOCX、PPTX、TXT、PNG、JPEG 及手机视口；长文本加载后续内容；验证记录见 `docs/27-preview-validation.md`；`feat: improve multi-format document preview` |
| M13（已完成） | PPTX 幻灯片内嵌图片预览 | M12 | 图片/文字位置、双页图片、私有图片访问和 Edge 页面验收；验证记录见 `docs/28-pptx-media-validation.md`；`feat: preview embedded slide images` |
| M14（已完成） | PDF 与 PPTX 整页预览、适配视口和缩放 | M13 | PDF/PPTX Edge 桌面及手机视口；《太原红色记忆》第 1、2、13 页完整渲染；验证记录见 `docs/30-full-page-preview-validation.md`；`feat: render full page document previews` |
| M15（已完成） | AI 问答寒暄处理、资料不足引导与模型错误说明 | M14 | 寒暄不调用模型；无证据时提示下一步；DeepSeek 402 返回账户额度说明；`feat: improve assistant question answering UX` |

前端随M1–M8逐模块交付，不把所有页面拖到最后。M1做登录，M2做资料库，M3任务中心，M4预览，M5搜索，M6对话，M7审批，M8资料包。

## 工作量估计

单人学习并开发预计6–9周，仅作排期参考：基础与文件管理1–2周，解析与检索1–2周，Agent与审批1–2周，前端联调与评测部署1–2周。评测、Docker准备和可用Key可能延长周期；不能在未验证依赖与模型质量时承诺固定交付日期。

## 每模块完成定义

1. 实际可运行，关键异常路径有证据；OpenAPI和文档一致。
2. 相应测试通过，Ruff/typecheck/frontend build按影响范围通过。
3. 无真实Key、私密资料、大模型权重、临时ZIP进入Git。
4. 更新变更记录，提交仅包含本模块变更，Git工作区检查无误。
5. 用户可按验收步骤查看结果；推送到已指定的新仓库，报告commit SHA与验证结果。

## 简历可证明的成果

可表述技术实现：异步文档处理、中文混合检索、带引用问答、持久化Agent状态、人工审批与幂等恢复。效果数字必须来自评测报告，例如检索Recall@10、引用支持率、吞吐与P95。项目名称、技术数量或模型调用次数不能替代实际效果。
