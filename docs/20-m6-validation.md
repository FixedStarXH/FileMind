# M6 验证记录：带引用的资料问答

## 环境与范围

- 日期：2026-11-09
- Python 3.12；PostgreSQL 17；Docker Compose；DeepSeek API Key 保持空值。
- 本模块包含独立 Agent 队列、LangGraph QA、PostgreSQL checkpoint、范围化检索、结构化引用、对话历史、SSE 事件持久化与回放、问答页面。
- 本阶段只实现 QA 模式；资料整理与资料包留给 M7/M8。

## 验证结果

- M6 Alembic 迁移 `0007_agent_qa` 应用成功；`agent_state` 中存在 LangGraph `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 与 `checkpoint_migrations` 表。
- API 冒烟：创建会话成功；DeepSeek Key 空值时创建 run 返回 `503 AI_NOT_CONFIGURED` 且不留下 run；SSE Last-Event-ID 回放成功；超出事件保留期返回 `stream.reset`；跨用户读取返回 404。
- LangGraph + PostgreSQLSaver 空证据流程完成 checkpoint 和终态事件持久化，并返回明确的“无法根据资料回答”文本，没有调用模型。
- 合成资料与 mock 回答流程验证 `[E1]` 被绑定到实际检索片段、来源定位可保存；永久删除来源时清理关联答案、消息、引用文本、SSE 事件和 graph checkpoint。
- AI 问答页 TypeScript 检查及 Vite 生产构建通过；后端 Ruff 与 mypy 检查通过。

## 限制

合成资料流程中的回答由显式 mock 生成，不是真实模型结果；没有真实 DeepSeek 调用、引用语义正确率、幻觉率或答案质量评测。当前只验证引用编号确实来自检索结果、持久化关联和清理行为，不能据此声称引用语义已被证明正确。配置 Key 后需在 M9 使用标注语料评估引用支持率、拒答质量、时延与 token 用量。

连续追问仅向模型提供同一会话中最近12条未删除消息（最多20,000字符），历史内容只用于解析指代；当前答案事实仍要求引用本次检索所得证据。上下文隔离与删除清理已由代码路径实现，但尚未做大规模多轮对话评测。
