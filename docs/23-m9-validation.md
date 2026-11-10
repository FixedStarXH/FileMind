# 23 M9 验证记录

## 范围

本模块完成 Edge 桌面/手机视口联调、资料上传到导出的端到端流程，以及关键授权检查。端到端流程中发现资料包页面在创建导出后不会自动更新状态，因此增加了对排队中/运行中导出的自动轮询；任务进入终态后轮询停止。

## 验证结果

| 项目 | 结果 | 说明 |
|---|---|---|
| 前端生产构建 | 通过 | `npm run build` |
| Playwright Edge E2E | 3 passed，1 skipped | 桌面完整流程、桌面响应式和 375px 手机视口响应式通过；手机项目跳过文件传输完整流程，避免重复写入测试资料 |
| 桌面关键路径 | 通过 | 登录、上传 TXT、等待解析、正文搜索、手动资料包导出、ZIP 下载、移入回收站后导出失效 |
| 响应式 | 通过 | Edge 桌面 1365px 与手机模拟 375px 下，登录、资料库和资料包页面无文档级横向溢出；这是浏览器设备模拟，不是实体手机测试 |
| CSRF | 通过 | 已登录状态下无 CSRF 头的设置修改请求返回 403 |
| 用户隔离 | 通过 | 第二个测试账户下载第一个账户版本返回 404；未登录读取导出列表返回 401 |
| 性能目标 | 未完成实测 | 本记录不将计划阈值写成实测结果；20,000 片段检索并发、元数据 P95、OCR 吞吐与资源峰值仍待独立压测 |
| Agent 质量 | 未完成实测 | DeepSeek Key 保持空值；真实模型回答、引用支持率和资料包缺项识别需要配置 Key 与标注集后评测 |

E2E 使用现有可丢弃测试账户及唯一随机文件名；成功完成的流程会将测试文档移入回收站、使导出失效，并关闭第二账户的请求上下文。不要把测试账户密码写入仓库。

## 复现

在 `frontend` 目录安装锁文件依赖，并配置两个可丢弃的测试账户：

```powershell
npm ci
$env:E2E_EMAIL = "first-test@example.com"
$env:E2E_PASSWORD = "<disposable-password>"
$env:E2E_EMAIL_2 = "second-test@example.com"
$env:E2E_PASSWORD_2 = "<disposable-password>"
npm run test:e2e
```

Playwright 使用已安装的 Microsoft Edge（`channel: msedge`），并会自动启动 Vite 前端。API、数据库、Redis 和 Worker 应先通过 Docker Compose 启动并就绪。

## 已知限制

- 375px 为 Edge 移动设备模拟视口；未在实体 Android/iOS 浏览器上完成触控、软键盘与真实网络测试。
- E2E 覆盖主要用户路径与少数安全边界，不替代 `docs/09-testing-and-evaluation.md` 的全部故障、并发、越权及 20k 检索压测清单。
- 实际模型评测和性能结论尚未形成，因此不能将目标值写作项目成果。
