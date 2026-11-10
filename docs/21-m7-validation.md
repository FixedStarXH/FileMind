# M7 验证记录：人工审批的资料整理

## 环境与范围

- 日期：2026-11-10。
- Python 3.12、PostgreSQL 17、Docker Compose；DeepSeek API Key 保持空值。
- 本模块实现有限范围资料整理、改名/移动/标签建议、持久化审批暂停与恢复、原子元数据执行、动作账本、审计查询和审批工作区。

## 验证结果

- Alembic `0008_approved_organization` 应用到数据库；M7 运行状态、计划、审批、动作执行账本和审计事件表已创建。
- LangGraph + PostgreSQL checkpoint mock 流程验证：生成计划后等待审批、恢复并应用所选操作、重复执行由账本阻止重复写入。
- 文档 metadata revision 改变后，审批执行以 `STALE_PLAN` 拒绝，且不产生部分元数据修改。
- 审批拒绝 HTTP 流程返回202；同一 Idempotency-Key 和相同请求重放返回原响应，不同 Key 再次决定返回409 `ALREADY_DECIDED`。
- 审计 API 用临时账户实测 owner 过滤、resource_id 筛选、双页游标翻页和无效游标400；未认证请求返回401。临时账户及关联记录已清理。
- `/health/live` 与 `/health/ready` 均返回200；M7 路由已加载。
- 前端 Vite 生产构建通过；Ruff 检查与格式检查通过；mypy 检查通过（31 个源文件）。
- 审计读取接口限制为当前 owner，支持 resource_id 过滤和 created_at/id 稳定游标翻页；响应只返回安全审计 details，不提供正文。

## 限制

DeepSeek Key 未配置，计划生成使用显式 mock；没有验证真实模型整理质量、事实正确率或 token/时延指标。M7 只改变文件元数据，不会自动移动磁盘文件或删除资料。真实模型与浏览器审批体验应在 M9 使用配置 Key 的完整 E2E 继续验收。
