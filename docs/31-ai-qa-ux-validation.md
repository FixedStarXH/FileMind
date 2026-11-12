# M15 AI 问答体验验收

- 对“你好”等短寒暄使用本地回复，不检索资料、不调用 DeepSeek。演示账号实际任务完成，`llm_calls=0` 且 token 用量为 0。
- 当前资料范围没有召回证据时，回复会说明可上传并等待解析，或切换到包含相关文件的范围。
- 模型返回 HTTP 402 时映射为 `MODEL_BILLING_REQUIRED`；前端提示检查 DeepSeek 账户余额、API 额度或服务状态。
- DeepSeek 401/403 仍显示 Key 无效提示；429 保留限流处理。
- Ruff、mypy 和前端生产构建通过。

错误日志仅记录 HTTP 状态，不输出 API Key 或模型响应正文。
