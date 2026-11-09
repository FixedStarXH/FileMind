# 15 M1 账户与登录验证记录

日期：2026-11-09。环境：Windows 开发机、CPython 3.12、Docker Compose、PostgreSQL 17、Redis 7.4、Node.js 24。

## 已实现

- 管理员通过 `filemind create-user <email>` 创建账户；密码交互输入、至少12位，使用 Argon2id 保存散列。
- 登录、HttpOnly Session Cookie、服务端仅保存令牌散列、CSRF token、Origin 校验、登出和改密后撤销全部会话。
- 登录按邮箱和客户端 IP 限流；Redis 不可用时拒绝认证。AI 开关只有在 DeepSeek Key 已配置时才能启用。
- 账户设置接口返回存储额度与能力状态；页面提供登录、额度展示、AI 开关、改密和退出。
- Alembic `0002_accounts` 创建 users 与 sessions 表及额度约束。

## 验证结果

- `uv run --frozen ruff check src`：通过。
- `uv run --frozen mypy src/filemind`：通过。
- `npm install` 与 `npm run build`：TypeScript 检查和 Vite 生产构建通过；前端锁文件已生成。
- `uv run --frozen alembic upgrade head`：在 M0 数据库完成 `0001_bootstrap → 0002_accounts`。
- Docker API 镜像重建后，`/health/live` 与 `/health/ready` 正常；数据库、Redis、存储检查为 ok，DeepSeek 显示未配置。
- 使用临时账户完成创建、登录、会话读取、设置读取、无 CSRF 写请求被拒绝、有效 CSRF 更新、改密、旧会话失效、重新登录和退出的请求链路。临时账户随后已删除。

## 当前边界

M1 尚无文件、任务或对话资源，因此实际文件级跨账户隔离要等 M2 数据模型和接口实现后验证。Edge 与手机视口的人工视觉验收也尚未执行；当前构建结果不等于已完成浏览器视觉审查。DeepSeek API Key 保持空白，AI 请求链路不属于本模块。
