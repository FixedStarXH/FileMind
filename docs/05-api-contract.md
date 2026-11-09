# 05 HTTP 接口契约

设计版本 v1；前缀 `/api/v1`。M1 已实现账户、会话和设置端点；M2 已实现文件夹、标签、文件和版本生命周期接口；M3 已实现持久任务与 Outbox。其余接口是后续模块契约。FastAPI OpenAPI 会随实现逐模块校验。

## 1. 通用约定

- JSON 使用 snake_case、UUID 字符串、UTC RFC3339 时间。未知请求字段拒绝；path UUID 格式错误422。
- 普通成功：`{"data":{},"request_id":"uuid"}`；列表 data 为 `{items:[],next_cursor:null}`。204、下载流、SSE 无包装。
- 错误：`{"error":{"code":"STALE_PLAN","message":"文件已发生变化，请重新生成整理方案","details":{}},"request_id":"uuid"}`。
- 列表 limit 默认20、最大100；cursor 为不透明游标，排序字段与 id 共同形成稳定顺序，翻页期间数据更新允许出现顺序变化，客户端需刷新；筛选或排序变化需清空 cursor。不同列表可自定排序，但必须在接口说明中固定。
- 登录返回 HttpOnly Session Cookie，Secure（本地 localhost HTTP 例外）、SameSite=Lax、Path=/，有效期7天。写接口要求 `X-CSRF-Token`，服务端校验 Origin；登录接口校验同源 Origin 并限流。
- `GET /auth/me` 返回当前 CSRF token；不把 Session secret 返回 JSON。登录/改密轮换 Session；改密使旧 Session 全失效。
- 创建文件、创建任务、审批与导出必须带 `Idempotency-Key`（UUID，保存24h）。相同 key+请求散列返回原响应，不同请求返回409；上传散列使用已收到文件 SHA-256 及元数据。
- 文档更新带 `If-Match: "<metadata_revision>"`，文件夹使用返回的 revision；缺失428，不匹配412。Agent 计划冲突使用409 STALE_PLAN。标签更新采用最后一次写入生效，不要求 If-Match。
- 其他用户的资源与不存在资源统一404。403用于当前用户关闭云 AI 等业务禁止，不暴露他人资源。

## 2. 接口清单

| 方法与路径 | 输入/说明 | 成功响应 |
|---|---|---|
| POST /auth/login | `{email,password}` | 200 `{user,csrf_token}` + Cookie |
| GET /auth/me | Cookie | 200 `{user,csrf_token}` |
| POST /auth/logout | CSRF | 204，撤销 Cookie |
| POST /auth/password | `{old_password,new_password}` | 204，撤销所有会话，重新登录 |
| GET /settings | — | 200 `{ai_enabled,quota_bytes,used_bytes,reserved_bytes,capabilities}` |
| PATCH /settings | `{ai_enabled:bool}` | 200 settings；不能通过此接口写 API key |
| GET /folders | parent_id? | 200 列表 |
| POST /folders | `{name,parent_id?}` | 201 folder |
| PATCH /folders/{id} | `{name?,parent_id?}` + If-Match | 200 folder |
| DELETE /folders/{id} | If-Match，必须为空 | 204 |
| GET /tags | — | 200 列表 |
| POST /tags | `{name,color?}` | 201 tag |
| PATCH /tags/{id} | `{name?,color?}` | 200 tag |
| DELETE /tags/{id} | 移除关联，不删文件 | 204 |
| POST /documents | multipart `file,folder_id?`，一次一个文件；Idempotency-Key | M3 202 `{document,version,task}`；重复内容200 `{duplicate:true,documents:[...]}` |
| GET /documents | folder_id?,tag_ids?,favorite?,q?,state=active/trashed,sort?,cursor?,limit? | 200 列表 |
| GET /documents/{id} | — | 200 文件详情和各版本处理状态 |
| PATCH /documents/{id} | `{title?,folder_id?,tag_ids?,favorite?}` + If-Match | 200 document |
| DELETE /documents/{id} | If-Match，移入回收站 | 204 |
| POST /documents/{id}/restore | If-Match | 200 document |
| DELETE /documents/{id}/permanent | `{confirm_title}`，仅回收站文件；Idempotency-Key | M3 202 `{task}`；文件立即转为 purging 并禁止访问 |
| GET /documents/{id}/versions | — | 200 列表 |
| POST /documents/{id}/versions | multipart `file` + If-Match + Idempotency-Key | M3 202 `{document,version,task}` |
| GET /versions/{id}/preview | `page?`、`offset?`、`limit?`（1–1000） | 200 `{kind,parse_state,error_code?,blocks,page_count,generation,warnings,offset,has_more}`；按 PDF 页码/PPTX 幻灯片筛选 |
| GET /versions/{id}/content | 仅 PDF/图片 inline；鉴权、单 Range | 200/206 bytes，私有 no-store |
| GET /versions/{id}/download | 原文件附件下载 | 200/206 bytes |
| POST /versions/{id}/reprocess | `{}`；不允许同版本并行解析 | 202 `{task}` |
| POST /search | SearchRequest `{query,mode,scope,limit}`；mode=`keyword|semantic|hybrid` | 200 `{hits,degraded,retrieval_version}` |
| POST /conversations | `{title?,scope}` | 201 conversation |
| GET /conversations | 游标 | 200 列表 |
| GET /conversations/{id} | — | 200 会话、消息与引用 |
| DELETE /conversations/{id} | 取消关联活动任务后清理 | 202 `{task_id}` |
| POST /agent/runs | M6: `{mode:"qa",conversation_id,instruction,scope}` | 202 `{run_id,status,events_url}`；Key 未配置503 `AI_NOT_CONFIGURED`；账户未启用AI返回403 |
| GET /agent/runs/{id} | — | 200 RunDetail |
| GET /agent/runs/{id}/events | SSE；Last-Event-ID? | 200 text/event-stream |
| POST /agent/runs/{id}/cancel | `{}` | 202 `{status:"cancel_requested"}` |
| GET /agent/runs/{id}/plan | — | 200 Plan；尚无方案404 PLAN_NOT_READY |
| POST /agent/runs/{id}/approval | ApprovalRequest | 202 `{run_id,status}` |
| POST /exports | `{version_ids:[...]}`，1–100项；仅手动导出，不接受 agent_run_id | 202 `{export_id,task_id}` |
| GET /exports/{id} | — | 200 `{state,expires_at,download_url?}` |
| GET /exports/{id}/download | 已过期410；源文档不可用410 | 200 ZIP |
| GET /tasks | kind?,status?,cursor? | 200 列表 |
| GET /tasks/{id} | — | 200 task |
| POST /tasks/{id}/cancel | queued/running | 202 task；终态409 |
| POST /tasks/{id}/retry | failed/cancelled，原资源可用 | 202 新 task，含 retry_of |
| GET /audit-events | resource_id?,cursor? | 200 列表 |

`GET /health/live` 只检测进程；`GET /health/ready` 检测 DB、broker、文件卷可用，不返回密钥。DeepSeek 缺 Key 不影响基础 readiness；capabilities 表明哪些功能可用。

账户仅由管理员 CLI 创建，不开放公开注册。初始化命令为 `filemind create-user <email>`；密码通过交互提示输入两次，不作为命令行参数保存。写接口要求会话 Cookie、CSRF header 和精确匹配 `APP_ORIGIN` 的 Origin。M1 登录限流按邮箱与客户端 IP 分别计数，15分钟最多10次/邮箱、30次/IP；Redis 不可用时拒绝登录。

**M2/M3 阶段接口边界**：M2 曾同步接收文件并同步永久删除。M3 起上传和新增版本返回202，原始 blob 与元数据在同一数据库事务中提交，并创建 parse task/outbox；解析 task 状态为 queued、stage=`waiting_parser`，M3 dispatcher 暂不将它投递给 Celery，M4 提供解析 Worker 后再启用。M3 永久删除返回202，文件立即标记 purging 并禁止访问，当前解析任务会收到取消请求；后台 Worker 等待解析租约结束后清除 blob 与元数据。202 只表示请求已持久接收，不表示解析或删除已完成。

## 3. 核心模型

**Document**：`id,title,folder_id?,tag_ids[],favorite,state,metadata_revision,current_version_id?,latest_version_id?,created_at,updated_at`。列表还返回 size_bytes、mime、parse_state、index_mode?；latest/current 可以不同。

`GET /documents` 默认 state=active；省略 folder_id 表示“全部文件”，传 `folder_id=inbox` 表示 folder_id=null 的收件箱；真实文件夹使用 UUID。q 对标题与原始文件名做大小写不敏感匹配，不搜索正文；正文使用 `/search`。sort 首版为 `updated_desc`（默认）、`name_asc`、`size_desc`，游标绑定排序和筛选。`GET /documents/{id}` 的最新版本与当前可检索版本可能不同；首次解析失败时 current_version_id=null。

**Task**：`id,kind,status,stage,progress?,attempt,error_code?,cancel_requested,retry_of?,created_at,updated_at`。progress 是真实已完成项/总项，不可计算则 null。M3 kinds 为 parse、document_purge；parse task 的 stage=`waiting_parser` 表示等待 M4 Worker。

**Folder**：`id,name,parent_id?,revision,created_at,updated_at`。**Tag**：`id,name,color`。

任务重试补充规则：`/tasks/{id}/retry` 只用于失败/取消且原资源仍可用的解析任务，或仍处于 purging 的永久删除任务；返回同一失败任务的重复 retry 会重放已创建的子任务。不可取消的永久删除任务返回409。Agent任务不经通用 retry 接口重跑；创建新问答须提交新 run。后续有审批模式时，Agent任务返回409 `RESTART_AGENT_REQUIRED`，前端通过创建新run重新生成方案并重新审批。自动恢复尚未终结的Agent run使用原checkpoint及已记录审批，只能继续同一plan，已应用动作不得重复执行。审批执行失败后不能将原批准用于新方案。

回收站文件允许所有者通过版本预览/下载接口访问，文件详情响应携带trashed状态；搜索和Agent工具仍排除。包含回收站文件的历史回答只显示“来源已移入回收站”并隐藏原文片段，恢复后可再次定位；相关导出包立即失效。purging/purged状态禁止内容访问。Office 加密文件若在接收检查时识别，返回422 `ENCRYPTED_DOCUMENT`；加密 PDF 在异步解析时失败为 `ENCRYPTED_PDF`，文件仍可下载。

**Scope**：`{"document_ids":["uuid"],"folder_ids":[],"tag_ids":[],"inbox":false,"all_documents":false}`。各选择器按并集确定范围并去重；inbox=true 选择 folder_id=null 的未归档文件；all_documents=true 时其他选择器和 inbox 必须为空/false。默认禁止隐式全库；搜索和问答可显式选择全库，按授权索引检索，结果证据固定版本；整理和资料包必须指定有限范围并在 run 开始时展开至最多100文件，超出返回422 SCOPE_TOO_LARGE。文件夹包含后代文件夹。

**SearchRequest**：`{query:string(1..2000),mode:"keyword"|"semantic"|"hybrid",scope:Scope,limit:int(1..50)}`。返回 hit：`{document_id,version_id,chunk_id,title,snippet,locator,score}`；score 是关键词 rank 或 reciprocal-rank-fusion 排序值，不是置信概率。语义与混合模式使用固定 revision 的本地 BGE；若模型不可用，hybrid 返回关键词结果并置 `degraded=true`，semantic 返回503 `SEARCH_UNAVAILABLE`。只检索 owner 的 active 文档 current_version 与 active_generation；首版最多50条，不提供游标。

搜索 scope 必须显式指定至少一个选择器，或 `all_documents=true`；仅文件名查询请用 `/documents?q=`。问答的 scope 在 run 创建时确定，后续追问可重新选择范围，但每个 run 都保存独立的范围快照。

**Locator** 是判别联合：M4 提取块使用 `{page:1,source:"native"|"ocr",bbox?:[left,top,right,bottom]}`、`{paragraph:12}`、`{table:1,row:2,column:3}`、`{slide:3,shape:2,paragraph:1}`、`{line:8}` 或 `{source:"ocr",bbox:[...],width:1400,height:900}`。PDF OCR bbox 单位为 PDF 页面点，图片 bbox 为应用 EXIF 方向校正后的像素；段落、表格、幻灯片、行和页码从1开始。M5 chunk locator 归一到统一引用 schema。

**RunRequest** 示例：

```json
{
  "mode": "organize",
  "instruction": "把这些材料整理为课程、证书和求职资料",
  "conversation_id": null,
  "scope": {"document_ids": ["11111111-1111-4111-8111-111111111111"], "folder_ids": [], "tag_ids": [], "all_documents": false}
}
```

mode=`qa|organize|application_pack`。instruction 长度1..4000；qa 必须提供 conversation_id，其他模式可空；同一 conversation 同时只允许一个活动 run，冲突409。客户端不能设置 system prompt、工具名、owner_id、预算上限。

**RunDetail**：`id,mode,status,scope_snapshot,steps[],answer?,citations[],plan_id?,usage,error?`。只展示工具摘要，不返回模型内部思维链。

**Plan**：`id,revision,plan_hash,expires_at,actions[],missing_items[]`。action 包含 `id,type,document_id,version_id?,expected_revision,before,after,reason,citation_ids[]`；允许 type=`rename|move|set_tags|include_in_export`。`include_in_export` 必须包含版本ID和所依据文档的 revision；确认前再次校验这两个值。移动目标必须是已有文件夹；未建目录时让用户先创建，首版避免嵌套创建与回滚。

`application_pack` 的需求由 instruction 描述；服务端另保存结构化的材料清单、缺失项与来源，确认后仅为选中的 `include_in_export` 项创建导出任务。直接 `POST /exports` 是用户手动选文件的操作，不会读取或执行 Agent 方案；Agent 路径只能通过审批节点创建导出任务。

**ApprovalRequest**：

```json
{"plan_id":"uuid","plan_hash":"sha256","decision":"approve","selected_action_ids":["a1","a2"]}
```

decision=`approve|reject`，approve 至少选择一项；reject 必须为空数组。服务端验证每个 action 均属于该 plan，原子消费唯一审批。重复请求仅在相同幂等键和请求时重放成功，否则409 ALREADY_DECIDED。返回202只代表恢复已排队。

## 4. SSE 协议

```text
id: 17
event: run.step
data: {"run_id":"uuid","seq":17,"stage":"search","message":"已检索到8个相关片段"}

id: 18
event: answer.delta
data: {"run_id":"uuid","seq":18,"text":"根据课件……"}
```

事件类型：`run.started`、`run.step`、`answer.delta`、`citation.added`、`approval.required`、`run.completed`、`run.failed`、`run.cancelled`、`run.expired`、`stream.reset`。每个事件有 run_id/seq；seq 在单 run 内严格递增。最终答案先完成引用和权限校验，再分段发送，首版不把未经校验的模型 token 直接展示。

服务端每15秒心跳，浏览器以 Cookie 认证；GET SSE 无副作用、不要求 CSRF header。断线带 Last-Event-ID 回放；游标早于7天保留期返回 stream.reset 并要求 GET run 获取快照。事件持久化后才发送，客户端按 seq 去重。终态事件后关闭连接；拉取终态 run 不重复执行任务。文件被删除导致历史事件清理时返回 reset。

## 5. 错误码

| HTTP | code | 处理 |
|---|---|---|
| 401 | AUTH_REQUIRED | 登录 |
| 403 | CSRF_INVALID / AI_DISABLED | 刷新会话/用户启用云 AI |
| 404 | RESOURCE_NOT_FOUND | 返回列表 |
| 409 | STALE_PLAN / ALREADY_DECIDED / TASK_CONFLICT / QUOTA_EXCEEDED / IDEMPOTENCY_CONFLICT / RESTART_AGENT_REQUIRED | 更新状态或重新预览 |
| 410 | ARTIFACT_EXPIRED / SOURCE_UNAVAILABLE | 重新导出或选择其他材料 |
| 412/428 | REVISION_MISMATCH / REVISION_REQUIRED | 刷新后修改/补充 If-Match |
| 413/415 | FILE_TOO_LARGE / UNSUPPORTED_FILE_TYPE | 调整文件 |
| 422 | VALIDATION_ERROR / ENCRYPTED_DOCUMENT / SCOPE_TOO_LARGE | 修正请求/解密后上传/缩小范围 |
| 429 | RATE_LIMITED / RUN_BUDGET_EXCEEDED | 等待 Retry-After 或缩小范围 |
| 503 | AI_NOT_CONFIGURED / MODEL_UNAVAILABLE / SEARCH_UNAVAILABLE | 显示不可用，不伪造结果 |

M4 解析失败时 `tasks.error_code` 和 `document_versions.error_code` 写入稳定错误码，例如 `CORRUPT_DOCUMENT`、`ENCRYPTED_PDF`、`PAGE_LIMIT_EXCEEDED`、`PDF_PAGE_PIXEL_LIMIT_EXCEEDED`、`IMAGE_PIXEL_LIMIT_EXCEEDED`、`OCR_UNAVAILABLE`、`OCR_FAILED`。M4 不声明 OCR 单页硬超时；任务限制文件大小、PDF 页数及渲染/图片像素，运维可回收异常 worker。M5 关键词索引不依赖向量模型；语义模型不可用时返回 `SEARCH_UNAVAILABLE` 或 hybrid `degraded=true`。日志保存 request_id，响应不返回堆栈、绝对路径或 Key。
