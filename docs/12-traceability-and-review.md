# 12 需求追踪与设计审查

## 1. 需求追踪矩阵

| 需求 | 核心接口 | 数据对象 | 验收依据 |
|---|---|---|---|
| F01 | /auth/* | users/sessions | 隔离与CSRF |
| F02 | POST /documents | blobs/versions/quota_reservations | 重复上传、配额、格式 |
| F03 | /documents /folders /tags | documents/folders/tags | 并发改名与版本条件 |
| F04 | /versions/* | extracted_blocks/chunks | 支持类型与引用位置 |
| F05 | /search | chunks/active_generation | Recall@10与降级 |
| F06 | /agent/runs mode=qa | conversations/messages/citations | 引用、拒答、SSE |
| F07 | /agent/runs mode=organize /approval | plans/approvals/audit_events | 审批与原子性 |
| F08 | /agent/runs mode=application_pack /exports | export_artifacts/export_items | 缺失项、清单、来源 |
| F09 | /versions /restore /permanent | versions/documents/blobs | 历史引用、软删、清理 |
| F10 | /tasks/* | tasks/outbox/stream_events | 重投、取消、恢复 |
| F11 | /settings /download | users/export_artifacts | 无Key、云AI授权、下载 |
| F12 | /audit-events | audit_events/run_steps | 用户操作可追踪、无正文泄露 |

## 2. 已确认与默认决策

用户已确认：个人学习与求职；网页电脑优先兼容手机；DeepSeek + 本地 OCR/向量；Key 留空。

沿用上下文：Python后端为主、Edge验收、按模块提交。设计默认：单机自托管、私人账户、无公开注册、首版无团队共享。FileMind为工作名，可随时改名，不影响接口。

## 3. 文档审查修正点

- 中文全文不能直接依赖英文分词；明确jieba与simple配置的同构分词。
- SSE已定义持久化、序号、回放、心跳、权限与终态，不以“支持流式”替代契约。
- 任务取消与已成功状态互斥；worker恢复用租约与fencing token，避免旧worker覆盖新结果。
- 新版本失败不破坏旧索引；引用固定历史版本，永久删除清理派生内容。
- Agent写操作需方案hash、revision、用户确认；队列重投与图恢复不重复执行。
- Office预览承诺结构化内容，不承诺保留原始排版；PPTX图片文字识别延后。
- 本地embedding与云端DeepSeek边界明确，用户启用AI前知晓片段会出站。
- API Key为空时可开发和使用基础功能；真实AI质量验收仍需以后配置Key。
- 通用任务重试不能复用终态Agent的旧审批；需要新run、新方案、新确认。
- 明确回收站原文件可由本人预览下载，但不可进入搜索/Agent上下文；永久清理后禁止访问。

2026-11-08 完整复查补充：产品交付阶段改用 R0–R2，开发模块保留 M0–M10；收件箱、重复上传、文件名查询、Agent 范围与错误码补齐；手动导出与 Agent 审批导出分离；DeepSeek 首版显式关闭默认思考模式；评测集分组与备份删除边界明确。14份Markdown的相对链接、代码围栏和空Key重新检查；尚未执行应用测试、依赖兼容性测试或性能评测，因为应用代码尚未开始。

## 4. 风险与处理

| 风险 | 对策 | 必须验证的阶段 |
|---|---|---|
| 笔记本CPU OCR慢/内存不足 | 低并发、页数限制、进程资源限制 | M0探针与M4 |
| 中文向量模型英文能力有限 | 中英分组评测，后续再决定是否替换多语言模型 | M5 |
| 扫描件/复杂表格识别错误 | 保留原图、引用定位、低质量提示 | M4/M6 |
| DeepSeek模型参数变化 | adapter合约测试、模型名可配、版本记录 | M0/M6 |
| 范围扩大导致延期 | 首版只保留PRD验收范围；新增需求单独记录 | 每模块 |
| 存储与DB不能原子提交 | 原子发布、outbox、孤儿清理、可重试状态 | M2/M3 |
| 客户端已下载的敏感材料无法撤回 | 清晰区分服务端删除与本地副本 | M8/UI |
| 备份在7天内仍含已删除文件 | 恢复前重放可用删除记录；无法核实时保持服务关闭 | M10演练 |

## 5. 后续真正需要用户信息的节点

- GitHub目标仓库已由用户指定为 `https://github.com/FixedStarXH/FileMind`。
- 准备真实AI联调时：用户在本地配置Key，不要求在聊天发送。
- 兼容性探针发现资源不足、产品需求有冲突、或需改变已确认范围时：提出具体问题与备选方案后再实现受影响部分。
- 前端开发前：按用户已选定的 [07 页面与交互](07-ui-and-interactions.md) A「清晰资料库」方向制作可打开的原型，再审查具体布局和交互。用户当前要求先完成文档审查，不启动应用开发。

文档完成不等于功能完成；不将设计检查、mock测试或目标指标描述成已上线能力。
