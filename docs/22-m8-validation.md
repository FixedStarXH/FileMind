# M8 验证记录：求职资料包

## 环境与范围

- 日期：2026-11-10。
- Python 3.12、PostgreSQL 17、Docker Compose；DeepSeek API Key 保持空值。
- 验证内容：手动资料导出、ZIP 产物、幂等、配额和源资料失效；Agent 资料清单和审批方案已接入，但因 Key 为空未调用真实模型验证。

## 验证结果

- Compose 配置校验通过；API、dispatcher、Agent worker 和维护 worker 镜像构建成功。
- Alembic 当前版本为 `0009_application_packs`；`/health/live` 返回200，API成功加载 M8 路由。
- Ruff lint 与格式检查通过；前端 `npm run build` 通过。
- mypy 检查通过（33个 Python 源文件）。
- 通过真实本地 API 使用临时账号与合成 TXT 文件创建手动资料包；重复发送相同 `Idempotency-Key` 返回同一个产物。
- 维护 worker 成功生成 ZIP；下载的 `manifest.json` 中源文件 SHA-256 与 ZIP 内文件和原始合成文件一致。
- 文件移入回收站后，资料包下载返回410；导出占用额度由 dispatcher 清理释放，保留的源文件额度正确。
- 冒烟核验创建的临时账号、数据库记录与源文件已清理。

## 限制

DeepSeek Key 未配置，因此没有验证真实模型生成资料清单与缺项提示的质量，Agent 计划生成与审批端到端仍需在配置 Key 后联调。手动导出 API、ZIP worker 与回收站失效清理已用本地合成文件实测。
