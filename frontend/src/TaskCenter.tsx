import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, CircleAlert, Clock3, LoaderCircle, RotateCcw, XCircle } from "lucide-react";
import { ApiError, request } from "./api";
import "./TaskCenter.css";

type TaskItem = {
  id: string;
  kind: string;
  resource_id: string | null;
  version_id: string | null;
  status: "queued" | "running" | "cancel_requested" | "succeeded" | "failed" | "cancelled";
  stage: string;
  progress: { completed: number; total: number } | null;
  attempt: number;
  error_code: string | null;
  cancel_requested: boolean;
  retry_of: string | null;
  created_at: string;
  updated_at: string;
};

type Props = { csrf: string; onLibrary: () => void; onSettings: () => void; onLogout: () => void };

const statusLabels: Record<TaskItem["status"], string> = {
  queued: "排队中", running: "处理中", cancel_requested: "正在取消",
  succeeded: "已完成", failed: "失败", cancelled: "已取消",
};
const kindLabels: Record<string, string> = { parse: "解析文档", document_purge: "永久删除" };
const stageLabels: Record<string, string> = {
  starting: "准备任务",
  extracting: "提取文档文本",
  ocr: "本地 OCR 识别",
  finalizing: "保存解析结果",
  completed: "处理完成",
  failed: "处理失败",
  cancelled: "已取消",
};
const parseErrors: Record<string, string> = {
  ENCRYPTED_PDF: "PDF 设置了密码",
  CORRUPT_DOCUMENT: "文件损坏或内容无法读取",
  PAGE_LIMIT_EXCEEDED: "页数超过支持上限",
  PDF_PAGE_PIXEL_LIMIT_EXCEEDED: "PDF 页面尺寸超过渲染上限",
  IMAGE_PIXEL_LIMIT_EXCEEDED: "图片尺寸超过解析上限",
  PARAGRAPH_LIMIT_EXCEEDED: "Word 文档段落超过上限",
  OCR_UNAVAILABLE: "本地 OCR 模型不可用",
  OCR_FAILED: "本地文字识别失败",
  PDF_RENDER_FAILED: "PDF 页面转换失败",
  BLOCK_LIMIT_EXCEEDED: "提取内容数量超过上限",
  BLOCK_SIZE_LIMIT_EXCEEDED: "单段内容超过上限",
};

function taskMessage(task: TaskItem): string {
  if (task.kind === "parse" && task.stage === "waiting_parser") return "等待解析模块上线后继续处理";
  if (task.stage === "retry_wait") return "将自动重试";
  if (task.error_code === "WORKER_LOST") return "工作进程已恢复，任务将重试";
  if (task.error_code) return `${parseErrors[task.error_code] ?? task.error_code}；可重试任务`;
  if (task.stage === "purging") return "正在清理文件和版本";
  return stageLabels[task.stage] ?? task.stage;
}

export default function TaskCenter({ csrf, onLibrary, onSettings, onLogout }: Props) {
  const [items, setItems] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await request<{ items: TaskItem[]; next_cursor: string | null }>("/tasks?limit=100");
      setItems(result.data.items);
      setError("");
    } catch (cause) {
      setError(cause instanceof ApiError && cause.message === "AUTH_REQUIRED"
        ? "登录状态已失效，请重新登录。"
        : "暂时无法读取任务，请检查连接后重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function act(task: TaskItem, action: "cancel" | "retry") {
    setBusyId(task.id);
    try {
      await request(`/tasks/${task.id}/${action}`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
      await load();
    } catch (cause) {
      setError(cause instanceof ApiError && cause.message === "TASK_CONFLICT"
        ? "任务状态已变化，列表已刷新。"
        : "操作未完成，请稍后重试。");
      await load();
    } finally {
      setBusyId(null);
    }
  }

  const activeCount = items.filter((item) => ["queued", "running", "cancel_requested"].includes(item.status)).length;

  return (
    <main className="task-shell">
      <header className="task-topbar">
        <a className="brand" href="#tasks"><span className="brand-mark"><Clock3 size={18} /></span>知档</a>
        <nav className="task-nav" aria-label="主导航">
          <button className="quiet-button" onClick={onLibrary}><ArrowLeft size={16} /><span>返回资料库</span></button>
          <button className="quiet-button" onClick={onSettings}>账户设置</button>
          <button className="quiet-button" onClick={onLogout}>退出</button>
        </nav>
      </header>
      <section className="task-content">
        <div className="eyebrow">后台处理</div>
        <div className="task-title-row"><div><h1>任务中心</h1><p>查看资料解析与删除任务的进度和结果。</p></div><span className="task-count"><LoaderCircle size={15} className={activeCount ? "spin" : ""} />{activeCount} 项进行中</span></div>
        {error && <div className="task-alert" role="alert"><CircleAlert size={16} />{error}<button onClick={() => void load()}>重试</button></div>}
        <div className="task-list" aria-live="polite">
          {loading ? <div className="task-empty"><LoaderCircle className="spin" />正在读取任务</div>
            : items.length === 0 ? <div className="task-empty"><Clock3 size={24} /><strong>还没有后台任务</strong><span>上传文件或清理回收站内容后，任务会显示在这里。</span></div>
              : items.map((task) => <article className="task-card" key={task.id}>
                <div className={`task-status-icon is-${task.status}`}>{task.status === "failed" ? <CircleAlert size={18} /> : task.status === "cancelled" ? <XCircle size={18} /> : <Clock3 size={18} />}</div>
                <div className="task-info"><div className="task-name-row"><strong>{kindLabels[task.kind] ?? task.kind}</strong><span className={`task-status-pill is-${task.status}`}>{statusLabels[task.status]}</span></div>
                  <p>{taskMessage(task)}</p>
                  <small>{new Date(task.created_at).toLocaleString("zh-CN")} · 尝试 {task.attempt}/3</small>
                  {task.progress && <div className="task-progress"><span style={{ width: `${task.progress.total ? Math.min(100, task.progress.completed / task.progress.total * 100) : 0}%` }} /></div>}
                </div>
                <div className="task-actions">
                  {(task.status === "queued" || task.status === "running") && task.kind === "parse" && <button disabled={busyId === task.id} onClick={() => void act(task, "cancel")}>取消</button>}
                  {(task.status === "failed" || task.status === "cancelled") && <button disabled={busyId === task.id} onClick={() => void act(task, "retry")}><RotateCcw size={14} />重试</button>}
                </div>
              </article>)}
        </div>
      </section>
    </main>
  );
}
