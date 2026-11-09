# 14 M0 基础工程验证记录

日期：2026-11-09。环境：Windows 开发机、CPython 3.12.14、Docker Engine 29.4.1。

## 已完成

- `uv lock --python 3.12` 生成 uv.lock；`uv sync --python 3.12 --frozen` 安装项目依赖。
- Docker Compose 启动 PostgreSQL 17 + pgvector、Redis 7.4、FastAPI API 容器。
- `uv run alembic upgrade head` 在空数据库完成初始迁移，安装 vector 扩展与 agent_state schema。
- 静态检查：Ruff 修正一处导入格式；Mypy strict 对4个源文件通过。
- HTTP `/health/live` 返回 `{"status":"alive"}`。
- HTTP `/health/ready` 返回 200，database、broker、storage 为 ok，deepseek 为 not_configured；符合 Key 留空的设计。
- Compose 配置验证通过；PostgreSQL 与 Redis 容器健康状态正常，API 容器运行。

## 尚未覆盖

M0 仅是基础工程。用户账户、文件上传、文档解析、检索、Agent、前端页面尚未开发。OCR、BGE、DeepSeek 的真实兼容性分别在 M4、M5、M6 验证；目前不能将 M0 健康检查当成整个产品可用。真实 AI 测试需用户在本地配置 Key。

本机 `.env` 是忽略文件，包含随机生成的数据库密码和 Session secret，DeepSeek Key 为空。不得提交或复制到公开仓库。
