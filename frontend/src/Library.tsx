import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  Archive, ArrowDownUp, BriefcaseBusiness, Check, ChevronDown, ChevronRight, Clock3, Eye, FileArchive, FileImage,
  FileText, Folder, FolderPlus, HardDrive, Heart, LoaderCircle, LogOut, MoreHorizontal,
  MessageSquareText, Plus, Search, Settings2, ShieldCheck, Star, Tag as TagIcon, Trash2, Upload, WandSparkles, X,
} from "lucide-react";
import { request, uploadFile } from "./api";
const DocumentPreview = lazy(() => import("./DocumentPreview"));

type User = { id: string; email: string };
type Settings = {
  ai_enabled: boolean;
  quota_bytes: number;
  used_bytes: number;
  reserved_bytes: number;
  capabilities: { deepseek: boolean; keyword_search: boolean; vector_search: boolean };
};
type FolderItem = { id: string; name: string; parent_id: string | null; revision: number };
type TagItem = { id: string; name: string; color: string; document_count: number };
type VersionItem = {
  id: string; version_no: number; original_name: string; parse_state: string; index_mode: string | null; error_code: string | null;
  size_bytes: number; mime: string; is_current: boolean; is_latest: boolean; created_at: string;
};
type DocumentItem = {
  id: string; title: string; folder_id: string | null; tag_ids: string[]; favorite: boolean;
  state: "active" | "trashed"; metadata_revision: number; current_version_id: string | null;
  latest_version_id: string | null; original_name: string | null; parse_state: string | null; index_mode: string | null;
  error_code: string | null;
  size_bytes: number; mime: string | null; created_at: string; updated_at: string;
  trashed_at: string | null; restore_until: string | null; versions?: VersionItem[];
};
type SearchHit = {
  document_id: string; version_id: string; chunk_id: string; title: string; snippet: string;
  locator: { source?: { page?: number; paragraph?: number; slide?: number; line?: number } };
};
type UploadItem = {
  id: string; file: File; status: "waiting" | "uploading" | "done" | "duplicate" | "failed";
  progress: number; message?: string;
};
type DialogState =
  | { kind: "folder" }
  | { kind: "rename-folder"; folder: FolderItem }
  | { kind: "delete-folder"; folder: FolderItem }
  | { kind: "tag" }
  | { kind: "edit-tag"; tag: TagItem }
  | { kind: "delete-tag"; tag: TagItem }
  | { kind: "permanent"; document: DocumentItem };

const MAX_FILE_BYTES = 50 * 1024 * 1024;
const MAX_BATCH = 20;

const errorMessages: Record<string, string> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  CSRF_INVALID: "请求校验失败，请刷新页面后重试。",
  FILE_TOO_LARGE: "单个文件不能超过 50 MiB。",
  UNSUPPORTED_FILE_TYPE: "文件格式或内容不匹配，请选择 PDF、DOCX、PPTX、TXT、MD、PNG 或 JPEG。",
  ARCHIVE_LIMIT_EXCEEDED: "Office 文档的压缩包结构超过安全限制。",
  ENCRYPTED_DOCUMENT: "暂不支持加密的 Office 文档。",
  QUOTA_EXCEEDED: "资料库空间不足，请删除不需要的文件后重试。",
  FOLDER_EXISTS: "同一目录下已有同名文件夹。",
  FOLDER_NOT_EMPTY: "请先移走文件和子文件夹，再删除此文件夹。",
  FOLDER_CYCLE: "不能把文件夹移动到自身或它的子文件夹中。",
  FOLDER_DEPTH_LIMIT: "文件夹最多嵌套 10 层。",
  TAG_EXISTS: "已有同名标签。",
  REVISION_REQUIRED: "资料已更新，请刷新后再试。",
  REVISION_MISMATCH: "资料已被其他操作更新，请刷新后重试。",
  IDEMPOTENCY_CONFLICT: "这次上传请求与之前的请求不一致，请重新选择文件。",
  INVALID_OLD_PASSWORD: "当前密码不正确。",
  CONFIRMATION_MISMATCH: "输入的文件名不一致。",
  TRASH_EXPIRED: "此文件已超过恢复期限。",
  SOURCE_UNAVAILABLE: "原始文件暂时不可用。",
  ENCRYPTED_PDF: "PDF 设置了密码，当前无法解析；原件仍可下载。",
  CORRUPT_DOCUMENT: "文件内容无法读取，请检查文件是否损坏。",
  PAGE_LIMIT_EXCEEDED: "页数超过首版支持上限，原件仍可下载。",
  PARAGRAPH_LIMIT_EXCEEDED: "Word 文档段落过多，当前无法解析。",
  OCR_UNAVAILABLE: "本地 OCR 模型不可用，请检查部署环境后重试。",
  OCR_FAILED: "文字识别失败，可下载原件或稍后重试。",
  PDF_RENDER_FAILED: "PDF 页面转换失败，请检查文件后重试。",
  BLOCK_LIMIT_EXCEEDED: "文档结构太复杂，提取内容超过单文件限制。",
  IMAGE_PIXEL_LIMIT_EXCEEDED: "图片尺寸超过解析上限，原件仍可下载。",
  NETWORK_ERROR: "网络连接中断，请检查网络后重试。",
  SEARCH_UNAVAILABLE: "语义检索模型暂不可用；可切换到关键词检索，或稍后重试。",
};

function messageFor(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  return errorMessages[code] ?? "操作暂时无法完成，请稍后重试。";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(value));
}

function formatParseState(state: string | null): string {
  if (!state || state === "not_started") return "待解析";
  if (state === "queued") return "排队中";
  if (state === "extracting") return "提取文本";
  if (state === "ocr") return "文字识别";
  if (state === "indexing") return "建立检索索引";
  if (state === "ready") return "已解析并可检索";
  if (state === "failed") return "解析失败";
  if (state === "cancelled") return "已取消";
  return state;
}

function parseErrorMessage(code: string | null): string {
  return code ? errorMessages[code] ?? `解析失败（${code}），原始文件仍可下载。` : "解析失败，原始文件仍可下载。";
}

function fileIcon(mime: string | null) {
  if (mime?.startsWith("image/")) return <FileImage size={18} />;
  if (mime?.includes("pdf") || mime?.includes("officedocument")) return <FileArchive size={18} />;
  return <FileText size={18} />;
}

export default function Library({
  user, settings, csrf, busy, setBusy, onSettings, onTasks, onAssistant, onOrganizer, onApplicationPacks, onLogout,
}: {
  user: User; settings: Settings; csrf: string; busy: boolean; setBusy: (value: boolean) => void;
  onSettings: () => void; onTasks: () => void; onAssistant: () => void; onOrganizer: () => void; onApplicationPacks: () => void; onLogout: () => void;
}) {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [tags, setTags] = useState<TagItem[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [nextDocumentCursor, setNextDocumentCursor] = useState<string | null>(null);
  const [loadingMoreDocuments, setLoadingMoreDocuments] = useState(false);
  const [folderFilter, setFolderFilter] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [favoriteFilter, setFavoriteFilter] = useState(false);
  const [stateFilter, setStateFilter] = useState<"active" | "trashed">("active");
  const [sort, setSort] = useState<"updated_desc" | "name_asc" | "size_desc">("updated_desc");
  const [search, setSearch] = useState("");
  const [searchMode, setSearchMode] = useState<"hybrid" | "keyword" | "semantic">("hybrid");
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);
  const [searchDegraded, setSearchDegraded] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewVersion, setPreviewVersion] = useState<VersionItem | null>(null);
  const [activeDocument, setActiveDocument] = useState<DocumentItem | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [folderDraft, setFolderDraft] = useState("");
  const [tagDraft, setTagDraft] = useState<string[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [dialogName, setDialogName] = useState("");
  const [dialogColor, setDialogColor] = useState("#6c7a70");
  const [dialogParent, setDialogParent] = useState("");
  const [confirmTitle, setConfirmTitle] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const versionInput = useRef<HTMLInputElement>(null);
  const targetFolderId = folderFilter && folderFilter !== "inbox" ? folderFilter : null;

  async function runFullTextSearch(mode = searchMode) {
    const query = search.trim();
    if (!query) return;
    const scope = folderFilter === "inbox"
      ? { document_ids: [], folder_ids: [], tag_ids: [], inbox: true, all_documents: false }
      : folderFilter
        ? { document_ids: [], folder_ids: [folderFilter], tag_ids: [], inbox: false, all_documents: false }
        : tagFilter
          ? { document_ids: [], folder_ids: [], tag_ids: [tagFilter], inbox: false, all_documents: false }
          : { document_ids: [], folder_ids: [], tag_ids: [], inbox: false, all_documents: true };
    setSearchBusy(true);
    setError("");
    try {
      const result = await request<{ hits: SearchHit[]; degraded: boolean }>("/search", {
        method: "POST",
        body: JSON.stringify({ query, mode, scope, limit: 50 }),
      });
      setSearchHits(result.data.hits);
      setSearchDegraded(result.data.degraded);
    } catch (cause) {
      setSearchHits([]);
      setError(messageFor(cause));
    } finally {
      setSearchBusy(false);
    }
  }

  const loadCatalog = useCallback(async () => {
    try {
      const [folderResult, tagResult] = await Promise.all([
        request<{ items: FolderItem[] }>("/folders"),
        request<{ items: TagItem[] }>("/tags"),
      ]);
      setFolders(folderResult.data.items);
      setTags(tagResult.data.items);
    } catch (cause) {
      setError(messageFor(cause));
    }
  }, []);

  const loadDocuments = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    const query = new URLSearchParams({ state: stateFilter, sort, limit: "100" });
    if (folderFilter) query.set("folder_id", folderFilter);
    if (tagFilter) query.append("tag_ids", tagFilter);
    if (favoriteFilter) query.set("favorite", "true");
    if (search.trim()) query.set("q", search.trim());
    try {
      const result = await request<{ items: DocumentItem[]; next_cursor: string | null }>(`/documents?${query}`);
      setDocuments((current) => silent
        ? [...result.data.items, ...current.filter((document) => !result.data.items.some((fresh) => fresh.id === document.id))]
        : result.data.items);
      setNextDocumentCursor(result.data.next_cursor);
    } catch (cause) {
      setError(messageFor(cause));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [folderFilter, favoriteFilter, search, sort, stateFilter, tagFilter]);

  async function loadMoreDocuments() {
    if (!nextDocumentCursor || loadingMoreDocuments) return;
    setLoadingMoreDocuments(true);
    setError("");
    const query = new URLSearchParams({ state: stateFilter, sort, limit: "100", cursor: nextDocumentCursor });
    if (folderFilter) query.set("folder_id", folderFilter);
    if (tagFilter) query.append("tag_ids", tagFilter);
    if (favoriteFilter) query.set("favorite", "true");
    if (search.trim()) query.set("q", search.trim());
    try {
      const result = await request<{ items: DocumentItem[]; next_cursor: string | null }>(`/documents?${query}`);
      setDocuments((current) => [...current, ...result.data.items]);
      setNextDocumentCursor(result.data.next_cursor);
    } catch (cause) {
      setError(messageFor(cause));
    } finally {
      setLoadingMoreDocuments(false);
    }
  }

  const loadSelectedDocument = useCallback(async (documentId: string) => {
    const result = await request<DocumentItem>(`/documents/${documentId}`);
    setActiveDocument(result.data);
    setTitleDraft(result.data.title);
    setFolderDraft(result.data.folder_id ?? "");
    setTagDraft(result.data.tag_ids);
  }, []);

  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  useEffect(() => { void loadDocuments(); }, [loadDocuments]);
  useEffect(() => { setSearchHits(null); }, [folderFilter, tagFilter, favoriteFilter, stateFilter]);

  useEffect(() => {
    if (!documents.some((document) => ["queued", "extracting", "ocr", "indexing"].includes(document.parse_state ?? ""))) return;
    const timer = window.setInterval(() => { void loadDocuments(true); }, 3000);
    return () => window.clearInterval(timer);
  }, [documents, loadDocuments]);

  useEffect(() => {
    if (!selectedId) {
      setActiveDocument(null);
      return;
    }
    let alive = true;
    void request<DocumentItem>(`/documents/${selectedId}`)
      .then((result) => {
        if (!alive) return;
        setActiveDocument(result.data);
        setTitleDraft(result.data.title);
        setFolderDraft(result.data.folder_id ?? "");
        setTagDraft(result.data.tag_ids);
      })
      .catch((cause) => { if (alive) setError(messageFor(cause)); });
    return () => { alive = false; };
  }, [loadSelectedDocument, selectedId]);

  useEffect(() => {
    if (!selectedId || !activeDocument || !["queued", "extracting", "ocr", "indexing"].includes(activeDocument.parse_state ?? "")) return;
    const timer = window.setInterval(() => {
      void loadSelectedDocument(selectedId);
      void loadDocuments(true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeDocument, loadDocuments, loadSelectedDocument, selectedId]);

  function updateUpload(id: string, update: Partial<UploadItem>) {
    setUploads((current) => current.map((item) => item.id === id ? { ...item, ...update } : item));
  }

  async function sendFiles(files: File[], options: { versionFor?: DocumentItem } = {}) {
    if (!files.length) return;
    if (files.length > MAX_BATCH) {
      setError(`一次最多上传 ${MAX_BATCH} 个文件。`);
      return;
    }
    const selected = files.map((file) => ({
      id: crypto.randomUUID(), file, status: "waiting" as const, progress: 0,
    }));
    setUploads((current) => [...selected, ...current].slice(0, 30));
    setError("");
    setNotice("");
    let next = 0;
    const worker = async () => {
      while (next < selected.length) {
        const item = selected[next++];
        updateUpload(item.id, { status: "uploading", message: undefined });
        if (item.file.size > MAX_FILE_BYTES) {
          updateUpload(item.id, { status: "failed", message: "文件超过 50 MiB。" });
          continue;
        }
        try {
          const path = options.versionFor
            ? `/documents/${options.versionFor.id}/versions`
            : "/documents";
          const response = await uploadFile<{ duplicate?: boolean }>(
            path,
            item.file,
            csrf,
            (progress) => updateUpload(item.id, { progress }),
            options.versionFor
              ? { ifMatch: options.versionFor.metadata_revision }
              : { folderId: targetFolderId },
          );
          const duplicate = response.data.duplicate === true;
          updateUpload(item.id, {
            status: duplicate ? "duplicate" : "done", progress: 100,
            message: duplicate ? "相同内容已存在" : options.versionFor ? "新版本已保存" : "已加入资料库",
          });
        } catch (cause) {
          updateUpload(item.id, { status: "failed", message: messageFor(cause) });
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, selected.length) }, () => worker()));
    await Promise.all([loadDocuments(), loadCatalog()]);
    if (options.versionFor) {
      setSelectedId(options.versionFor.id);
      try { await loadSelectedDocument(options.versionFor.id); }
      catch (cause) { setError(messageFor(cause)); }
    }
  }

  async function reprocessVersion(version: VersionItem) {
    try {
      setBusy(true);
      await request(`/versions/${version.id}/reprocess`, { method: "POST", headers: { "X-CSRF-Token": csrf }, body: "{}" });
      setNotice("已重新加入解析队列。");
      if (selectedId) await loadSelectedDocument(selectedId);
      await loadDocuments();
    } catch (cause) {
      setError(messageFor(cause));
    } finally {
      setBusy(false);
    }
  }

  function onFilesSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    void sendFiles(files);
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    event.stopPropagation();
    const files = Array.from(event.dataTransfer.files);
    if (files.length) void sendFiles(files);
  }

  async function changeFavorite(document: DocumentItem) {
    try {
      await request(`/documents/${document.id}`, {
        method: "PATCH",
        headers: { "X-CSRF-Token": csrf, "If-Match": `"${document.metadata_revision}"` },
        body: JSON.stringify({ favorite: !document.favorite }),
      });
      await loadDocuments();
      if (selectedId === document.id) await loadSelectedDocument(document.id);
    } catch (cause) { setError(messageFor(cause)); }
  }

  async function saveDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeDocument) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<DocumentItem>(`/documents/${activeDocument.id}`, {
        method: "PATCH",
        headers: { "X-CSRF-Token": csrf, "If-Match": `"${activeDocument.metadata_revision}"` },
        body: JSON.stringify({
          title: titleDraft,
          folder_id: folderDraft || null,
          tag_ids: tagDraft,
        }),
      });
      await loadSelectedDocument(result.data.id);
      setNotice("资料信息已保存。");
      await Promise.all([loadDocuments(), loadCatalog()]);
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }

  async function moveToTrash(document: DocumentItem) {
    setBusy(true);
    try {
      await request(`/documents/${document.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf, "If-Match": `"${document.metadata_revision}"` },
      });
      setSelectedId(null);
      setActiveDocument(null);
      setNotice("文件已移入回收站，可在30天内恢复。");
      await loadDocuments();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }

  async function restoreDocument(document: DocumentItem) {
    setBusy(true);
    try {
      await request(`/documents/${document.id}/restore`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf, "If-Match": `"${document.metadata_revision}"` },
      });
      setSelectedId(null);
      setStateFilter("active");
      setNotice("文件已恢复到资料库。");
      await loadDocuments();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }

  async function permanentlyDelete(document: DocumentItem) {
    setBusy(true);
    try {
      await request(`/documents/${document.id}/permanent`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ confirm_title: confirmTitle }),
      });
      setDialog(null);
      setConfirmTitle("");
      setSelectedId(null);
      setNotice("永久删除任务已排队，可在任务中心查看进度。");
      await loadDocuments();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }

  async function submitDialog(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dialog) return;
    setBusy(true);
    setError("");
    try {
      if (dialog.kind === "folder") {
        await request("/folders", {
          method: "POST", headers: { "X-CSRF-Token": csrf },
          body: JSON.stringify({ name: dialogName, parent_id: dialogParent || null }),
        });
        setNotice("文件夹已创建。");
      } else if (dialog.kind === "rename-folder") {
        await request(`/folders/${dialog.folder.id}`, {
          method: "PATCH",
          headers: { "X-CSRF-Token": csrf, "If-Match": `"${dialog.folder.revision}"` },
          body: JSON.stringify({ name: dialogName }),
        });
        setNotice("文件夹名称已更新。");
      } else if (dialog.kind === "delete-folder") {
        await request(`/folders/${dialog.folder.id}`, {
          method: "DELETE",
          headers: { "X-CSRF-Token": csrf, "If-Match": `"${dialog.folder.revision}"` },
        });
        setNotice("文件夹已删除。");
      } else if (dialog.kind === "tag") {
        await request("/tags", {
          method: "POST", headers: { "X-CSRF-Token": csrf },
          body: JSON.stringify({ name: dialogName, color: dialogColor }),
        });
        setNotice("标签已创建。");
      } else if (dialog.kind === "edit-tag") {
        await request(`/tags/${dialog.tag.id}`, {
          method: "PATCH", headers: { "X-CSRF-Token": csrf },
          body: JSON.stringify({ name: dialogName, color: dialogColor }),
        });
        setNotice("标签已更新。");
      } else if (dialog.kind === "delete-tag") {
        await request(`/tags/${dialog.tag.id}`, {
          method: "DELETE", headers: { "X-CSRF-Token": csrf },
        });
        if (tagFilter === dialog.tag.id) setTagFilter(null);
        setNotice("标签已删除，文件本身保留。");
      }
      setDialog(null);
      setDialogName("");
      setDialogParent("");
      await Promise.all([loadCatalog(), loadDocuments()]);
    } catch (cause) { setError(messageFor(cause)); }
    finally { setBusy(false); }
  }

  function openDialog(next: DialogState) {
    setDialog(next);
    setError("");
    setDialogName(next.kind === "rename-folder" ? next.folder.name : next.kind === "edit-tag" ? next.tag.name : "");
    setDialogColor(next.kind === "edit-tag" ? next.tag.color : "#6c7a70");
    setDialogParent("");
    setConfirmTitle("");
  }

  async function deleteFolder(folder: FolderItem) {
    openDialog({ kind: "delete-folder", folder });
  }

  function goToAll() {
    setStateFilter("active"); setFolderFilter(null); setFavoriteFilter(false); setTagFilter(null);
  }

  function goToTrash() {
    setStateFilter("trashed"); setFolderFilter(null); setFavoriteFilter(false); setTagFilter(null);
  }

  const folderNames = useMemo(() => new Map(folders.map((folder) => [folder.id, folder.name])), [folders]);
  const used = settings.used_bytes + settings.reserved_bytes;
  const quotaPercent = settings.quota_bytes ? Math.min(100, used / settings.quota_bytes * 100) : 0;
  const selectedTag = tags.find((tag) => tag.id === tagFilter);
  const activeFolder = folders.find((folder) => folder.id === folderFilter);
  const heading = stateFilter === "trashed" ? "回收站"
    : favoriteFilter ? "收藏"
      : selectedTag ? selectedTag.name
        : activeFolder?.name ?? (folderFilter === "inbox" ? "收件箱" : "全部文件");

  function renderFolderTree(parentId: string | null, depth = 0): React.ReactNode {
    return folders.filter((folder) => folder.parent_id === parentId).map((folder) => {
      const children = folders.some((child) => child.parent_id === folder.id);
      const expanded = expandedFolders.has(folder.id);
      return (
        <div key={folder.id}>
          <div className={`side-tree-row ${folderFilter === folder.id ? "is-active" : ""}`} style={{ paddingLeft: `${10 + depth * 14}px` }}>
            <button className="tree-expand" onClick={() => setExpandedFolders((current) => {
              const next = new Set(current); expanded ? next.delete(folder.id) : next.add(folder.id); return next;
            })} aria-label={expanded ? "折叠文件夹" : "展开文件夹"}>
              {children ? (expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span className="tree-spacer" />}
            </button>
            <button className="tree-label" onClick={() => {
              setStateFilter("active"); setFolderFilter(folder.id); setFavoriteFilter(false); setTagFilter(null);
            }}><Folder size={15} /><span>{folder.name}</span></button>
            <details className="row-menu"><summary aria-label={`${folder.name} 文件夹选项`}><MoreHorizontal size={16} /></summary><div className="row-menu-popover">
              <button onClick={() => openDialog({ kind: "rename-folder", folder })}>重命名</button>
              <button className="danger-action" onClick={() => void deleteFolder(folder)}>删除文件夹</button>
            </div></details>
          </div>
          {children && expanded && renderFolderTree(folder.id, depth + 1)}
        </div>
      );
    });
  }

  function toggleDraftTag(tagId: string) {
    setTagDraft((current) => current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId]);
  }

  const onDropPrevent = (event: DragEvent<HTMLElement>) => event.preventDefault();

  return (
    <main className="library-shell" onDragOver={onDropPrevent} onDrop={onDrop}>
      <aside className="library-sidebar">
        <div className="sidebar-brand"><a className="brand" href="#library"><span className="brand-mark"><FileText size={18} /></span>知档</a><span className="workspace-label">个人资料库</span></div>
        <nav className="library-nav" aria-label="资料库导航">
          <button className={`nav-item ${stateFilter === "active" && !folderFilter && !favoriteFilter && !tagFilter ? "is-active" : ""}`} onClick={goToAll}><Archive size={16} />全部文件</button>
          <button className={`nav-item ${folderFilter === "inbox" && stateFilter === "active" ? "is-active" : ""}`} onClick={() => { setStateFilter("active"); setFolderFilter("inbox"); setFavoriteFilter(false); setTagFilter(null); }}><Folder size={16} />收件箱</button>
          <button className={`nav-item ${favoriteFilter ? "is-active" : ""}`} onClick={() => { setStateFilter("active"); setFolderFilter(null); setFavoriteFilter(true); setTagFilter(null); }}><Heart size={16} />收藏</button>
          <button className={`nav-item ${stateFilter === "trashed" ? "is-active" : ""}`} onClick={goToTrash}><Trash2 size={16} />回收站</button>
          <button className="nav-item" onClick={onTasks}><Clock3 size={16} />任务中心</button>
          <button className="nav-item assistant-nav-item" onClick={onAssistant}><MessageSquareText size={16} />AI 问答</button>
          <button className="nav-item assistant-nav-item" onClick={onOrganizer}><WandSparkles size={16} />资料整理</button>
          <button className="nav-item assistant-nav-item" onClick={onApplicationPacks}><BriefcaseBusiness size={16} />求职资料包</button>
        </nav>
        <div className="side-section-heading"><span>文件夹</span><button onClick={() => openDialog({ kind: "folder" })} aria-label="新建文件夹"><Plus size={16} /></button></div>
        <div className="folder-tree">{renderFolderTree(null)}</div>
        <div className="side-section-heading tag-heading"><span>标签</span><button onClick={() => openDialog({ kind: "tag" })} aria-label="新建标签"><Plus size={16} /></button></div>
        <div className="tag-list">
          {tags.map((tag) => (
            <div className={`tag-side-row ${tagFilter === tag.id ? "is-active" : ""}`} key={tag.id}>
              <button onClick={() => { setStateFilter("active"); setTagFilter(tag.id); setFolderFilter(null); setFavoriteFilter(false); }}><i style={{ background: tag.color }} /><span>{tag.name}</span><small>{tag.document_count}</small></button>
              <details className="row-menu"><summary aria-label={`${tag.name} 标签选项`}><MoreHorizontal size={16} /></summary><div className="row-menu-popover">
                <button onClick={() => openDialog({ kind: "edit-tag", tag })}>编辑标签</button>
                <button className="danger-action" onClick={() => openDialog({ kind: "delete-tag", tag })}>删除标签</button>
              </div></details>
            </div>
          ))}
          {tags.length === 0 && <p className="sidebar-empty">还没有标签</p>}
        </div>
        <div className="sidebar-bottom">
          <div className="quota-label"><HardDrive size={14} /><span>存储空间</span><span className="quota-values">{formatBytes(used)} / {formatBytes(settings.quota_bytes)}</span></div>
          <div className="meter quota-meter"><span style={{ width: `${quotaPercent}%` }} /></div>
          <div className="profile-row"><span className="profile-avatar">{user.email.slice(0, 1).toUpperCase()}</span><span className="profile-email" title={user.email}>{user.email}</span><button onClick={onSettings} aria-label="账户设置"><Settings2 size={16} /></button></div>
        </div>
      </aside>

      <section className={`library-main ${selectedId ? "has-detail" : ""}`}>
        <header className="library-topbar">
          <div className="breadcrumb"><span>我的资料</span><ChevronRight size={14} /><strong>{heading}</strong></div>
          <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => { setSearch(event.target.value); setSearchHits(null); }} onKeyDown={(event) => { if (event.key === "Enter") void runFullTextSearch(); }} placeholder="筛文件名或搜正文…" aria-label="筛选文件名或搜索正文" />{search && <button onClick={() => { setSearch(""); setSearchHits(null); }} aria-label="清除搜索"><X size={14} /></button>}</label>
          <button className="search-body-button" onClick={() => void runFullTextSearch()} disabled={!search.trim() || searchBusy} aria-label="按所选方式搜索正文" title="搜索正文（也可按 Enter）"><Search size={15} /><span>{searchBusy ? "搜索中" : "搜正文"}</span></button>
          <select className="search-mode-select" value={searchMode} onChange={(event) => { const mode = event.target.value as typeof searchMode; setSearchMode(mode); if (searchHits !== null) void runFullTextSearch(mode); }} aria-label="检索方式"><option value="hybrid">混合</option><option value="keyword">关键词</option><option value="semantic">语义</option></select>
          <button className="top-icon-button" onClick={onSettings} aria-label="账户设置"><Settings2 size={18} /></button>
          <button className="top-icon-button" onClick={onLogout} disabled={busy} aria-label="退出登录"><LogOut size={17} /></button>
        </header>

        <div className="library-content">
          <div className="content-heading">
            <div><div className="eyebrow">{stateFilter === "trashed" ? "已删除项目" : "学习 · 论文 · 求职材料"}</div><h1>{heading}</h1><p>{stateFilter === "trashed" ? "文件在回收站保留30天，可以恢复或永久删除。" : "把课件、论文、证书和简历收在一个有序的私人空间。"}</p></div>
            <div className="heading-actions">
              {stateFilter === "active" && <button className="outline-button" onClick={() => openDialog({ kind: "folder" })}><FolderPlus size={16} /><span>新建文件夹</span></button>}
              {stateFilter === "active" && <button className="primary-button" onClick={() => fileInput.current?.click()}><Upload size={16} /><span>上传文件</span></button>}
              <input ref={fileInput} className="visually-hidden" type="file" multiple accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg" onChange={onFilesSelected} />
            </div>
          </div>

          {error && <div className="library-alert" role="alert"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭提示"><X size={15} /></button></div>}
          {notice && <div className="library-notice" role="status"><Check size={15} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="关闭提示"><X size={15} /></button></div>}

          {uploads.length > 0 && <section className="upload-queue" aria-label="上传队列">
            <div className="upload-queue-heading"><div><strong>上传队列</strong><span>{uploads.filter((item) => item.status === "done" || item.status === "duplicate").length} / {uploads.length} 已完成</span></div><button onClick={() => setUploads((items) => items.filter((item) => item.status === "failed" || item.status === "uploading" || item.status === "waiting"))}>清除已完成</button></div>
            {uploads.slice(0, 4).map((item) => <div className="upload-item" key={item.id}><div className="upload-item-icon">{fileIcon(null)}</div><div className="upload-item-main"><span>{item.file.name}</span><div className="upload-progress"><span style={{ width: `${item.progress}%` }} /></div></div><small>{item.message ?? (item.status === "uploading" ? `${item.progress}%` : item.status === "waiting" ? "等待中" : "")}</small>{item.status === "failed" && <button className="retry-button" onClick={() => void sendFiles([item.file])}>重试</button>}</div>)}
            {uploads.length > 4 && <p className="upload-more">另有 {uploads.length - 4} 个上传项目</p>}
          </section>}

          <div className="file-toolbar"><div className="file-count">{searchHits !== null ? `${searchBusy ? "正在检索…" : `${searchHits.length} 条正文结果`}` : loading ? "正在读取…" : `${documents.length} 个项目`}{searchDegraded && <span className="search-degraded">向量暂不可用，已退回关键词</span>}{tagFilter && selectedTag && <button className="filter-chip" onClick={() => setTagFilter(null)}><i style={{ background: selectedTag.color }} />{selectedTag.name}<X size={12} /></button>}</div>{searchHits === null && <label className="sort-select"><ArrowDownUp size={14} /><select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)} aria-label="排序"><option value="updated_desc">最近更新</option><option value="name_asc">名称 A–Z</option><option value="size_desc">文件大小</option></select></label>}</div>

          <div className="document-table" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
            <div className="table-head"><span>名称</span><span>状态</span><span>大小</span><span>更新日期</span><span></span></div>
            {searchHits !== null ? searchBusy ? <div className="table-placeholder"><LoaderCircle className="spin" size={20} />正在检索正文</div> : searchHits.length === 0 ? <div className="empty-library"><div className="empty-icon"><Search size={22} /></div><strong>没有找到正文匹配项</strong><p>试试关键词、语义或混合检索，或缩小当前资料范围。</p></div> : searchHits.map((hit) => {
                const source = hit.locator.source ?? {};
                const location = source.page ? `第 ${source.page} 页` : source.paragraph ? `第 ${source.paragraph} 段` : source.slide ? `第 ${source.slide} 张幻灯片` : source.line ? `第 ${source.line} 行` : "文档片段";
                return <button className="search-hit-row" key={hit.chunk_id} onClick={() => setSelectedId(hit.document_id)}><span className="search-hit-title"><strong>{hit.title}</strong><small>{location}</small></span><span className="search-hit-snippet">{hit.snippet}</span><ChevronRight size={16} /></button>;
              }) : loading ? <div className="table-placeholder"><LoaderCircle className="spin" size={20} />正在加载资料</div>
              : documents.length === 0 ? <div className="empty-library"><div className="empty-icon"><Folder size={24} /></div><strong>{search ? "没有找到匹配文件" : stateFilter === "trashed" ? "回收站是空的" : "这里还没有资料"}</strong><p>{search ? "试试其他关键词，或清除当前筛选。" : stateFilter === "trashed" ? "删除的文件会显示在这里。" : "拖入文件，或点击下方按钮开始建立你的资料库。"}</p>{stateFilter === "active" && !search && <><button className="primary-button" onClick={() => fileInput.current?.click()}><Upload size={16} />选择文件上传</button><small className="empty-hint">支持 PDF、Word、PowerPoint、TXT、Markdown 和图片，单个文件最大 50 MiB。</small></>}</div>
              : documents.map((document) => {
                const rowTags = tags.filter((tag) => document.tag_ids.includes(tag.id));
                return <div className={`document-row ${selectedId === document.id ? "is-selected" : ""}`} key={document.id}>
                  <div className="doc-name-cell"><span className="doc-type-icon">{fileIcon(document.mime)}</span><div className="doc-name-stack"><button className="doc-title-button" onClick={() => setSelectedId(document.id)}>{document.title}</button><div className="doc-row-tags">{rowTags.slice(0, 3).map((tag) => <span key={tag.id}><i style={{ background: tag.color }} />{tag.name}</span>)}</div></div></div>
                  <span className={`doc-status ${document.parse_state === "ready" ? "is-ready" : document.parse_state === "failed" ? "is-failed" : ""}`}><i />{formatParseState(document.parse_state)}</span>
                  <span className="doc-size">{formatBytes(document.size_bytes)}</span>
                  <span className="doc-date">{formatDate(document.updated_at)}</span>
                  <span className="doc-actions">{stateFilter === "active" ? <button className={`star-button ${document.favorite ? "is-favorite" : ""}`} onClick={(event) => { event.stopPropagation(); void changeFavorite(document); }} aria-label={document.favorite ? "取消收藏" : "收藏文件"}><Star size={16} fill={document.favorite ? "currentColor" : "none"} /></button> : <button className="star-button" aria-label="查看回收站文件"><ChevronRight size={16} /></button>}</span>
                </div>;
              })}
          </div>
          {searchHits === null && nextDocumentCursor && <div className="load-more-documents"><button className="secondary-button" onClick={() => void loadMoreDocuments()} disabled={loadingMoreDocuments}>{loadingMoreDocuments && <LoaderCircle className="spin" size={15} />}{loadingMoreDocuments ? "正在加载…" : "加载更多文件"}</button></div>}
          <div className="drop-hint"><Upload size={14} /><span>也可以把文件拖到页面中上传，单次最多20个文件，单个不超过50 MiB。</span></div>
        </div>

        {activeDocument && <aside className="document-detail" aria-label="文件详情">
          <div className="detail-heading"><div><span className="detail-kicker">资料详情</span><h2>{activeDocument.title}</h2></div><button className="top-icon-button" onClick={() => setSelectedId(null)} aria-label="关闭详情"><X size={18} /></button></div>
          <div className="detail-file-card"><span className="detail-file-icon">{fileIcon(activeDocument.mime)}</span><div><strong>{activeDocument.original_name}</strong><small>{formatBytes(activeDocument.size_bytes)} · {activeDocument.mime ?? "未知格式"}</small></div></div>
          <div className="detail-section"><div className="detail-section-title"><span>文件信息</span><span className="revision-badge">修订 {activeDocument.metadata_revision}</span></div>
            <form className="detail-form" onSubmit={(event) => void saveDocument(event)}>
              <label htmlFor="doc-title">显示名称</label><input id="doc-title" value={titleDraft} maxLength={255} disabled={stateFilter === "trashed"} onChange={(event) => setTitleDraft(event.target.value)} />
              <label htmlFor="doc-folder">所在文件夹</label><select id="doc-folder" value={folderDraft} disabled={stateFilter === "trashed"} onChange={(event) => setFolderDraft(event.target.value)}><option value="">收件箱</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select>
              <label>标签</label><div className={`detail-tag-picker ${stateFilter === "trashed" ? "is-disabled" : ""}`}>{tags.length ? tags.map((tag) => <label className="tag-check" key={tag.id}><input type="checkbox" checked={tagDraft.includes(tag.id)} disabled={stateFilter === "trashed"} onChange={() => toggleDraftTag(tag.id)} /><i style={{ background: tag.color }} />{tag.name}</label>) : <small>还没有标签</small>}</div>
              {stateFilter === "active" && <button className="secondary-button detail-save" type="submit" disabled={busy}>保存资料信息</button>}
            </form>
          </div>
          <div className="detail-section"><div className="detail-section-title"><span>版本记录</span>{stateFilter === "active" && <button className="link-button" onClick={() => versionInput.current?.click()}><Plus size={14} />新版本</button>}</div>
            <input ref={versionInput} type="file" className="visually-hidden" accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg" onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length && activeDocument) void sendFiles(files.slice(0, 1), { versionFor: activeDocument }); }} />
            <div className="version-list">{(activeDocument.versions ?? []).map((version) => <div className="version-row" key={version.id}><span className="version-number">v{version.version_no}</span><div className="version-main"><strong>{version.original_name}</strong><small>{formatDate(version.created_at)} · {formatParseState(version.parse_state)}</small></div>{version.is_current && <span className="current-label">当前</span>}<button className="download-button" onClick={() => setPreviewVersion(version)} aria-label={`预览版本${version.version_no}`} title="预览"><Eye size={15} /></button><a className="download-button" href={`/api/v1/versions/${version.id}/download`} aria-label={`下载版本${version.version_no}`} title="下载原件"><HardDrive size={15} /></a></div>)}</div>
          </div>
          <div className="detail-section detail-state"><div className="detail-section-title">处理状态</div><p><span className={`doc-status ${activeDocument.parse_state === "ready" ? "is-ready" : activeDocument.parse_state === "failed" ? "is-failed" : ""}`}><i />{formatParseState(activeDocument.parse_state)}</span></p><small>{activeDocument.parse_state === "failed" ? parseErrorMessage(activeDocument.error_code) : activeDocument.parse_state === "ready" ? `内容已保存，可预览并全文检索${activeDocument.index_mode === "keyword_only" ? "（当前仅关键词可用）" : ""}。` : "文件正在由本地解析与索引服务处理，完成后预览和搜索会自动更新。"}</small>{activeDocument.parse_state === "failed" && activeDocument.versions?.find((version) => version.id === activeDocument.latest_version_id) && <button className="link-button reprocess-button" onClick={() => void reprocessVersion(activeDocument.versions!.find((version) => version.id === activeDocument.latest_version_id)!)} disabled={busy}><LoaderCircle size={14} />重新解析</button>}</div>
          <div className="detail-footer">{stateFilter === "active" ? <><button className="danger-outline" onClick={() => void moveToTrash(activeDocument)} disabled={busy}><Trash2 size={15} />移入回收站</button><a className="download-full" href={activeDocument.latest_version_id ? `/api/v1/versions/${activeDocument.latest_version_id}/download` : "#"} aria-disabled={!activeDocument.latest_version_id}><HardDrive size={15} />下载原件</a></> : <><button className="secondary-button" onClick={() => void restoreDocument(activeDocument)} disabled={busy}>恢复文件</button><button className="danger-outline" onClick={() => openDialog({ kind: "permanent", document: activeDocument })} disabled={busy}><Trash2 size={15} />永久删除</button></>}</div>
        </aside>}
      </section>

      {dialog && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDialog(null); }}><section className="library-dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <div className="dialog-header"><div><span className="detail-kicker">资料库设置</span><h2 id="dialog-title">{dialog.kind === "folder" ? "新建文件夹" : dialog.kind === "rename-folder" ? "重命名文件夹" : dialog.kind === "delete-folder" ? "删除文件夹" : dialog.kind === "tag" ? "新建标签" : dialog.kind === "edit-tag" ? "编辑标签" : dialog.kind === "delete-tag" ? "删除标签" : "永久删除文件"}</h2></div><button className="top-icon-button" onClick={() => setDialog(null)} aria-label="关闭对话框"><X size={18} /></button></div>
        {dialog.kind === "permanent" ? <form onSubmit={(event) => { event.preventDefault(); void permanentlyDelete(dialog.document); }}><p className="dialog-copy">永久删除后，原始文件和相关版本无法恢复。请输入文件名确认。</p><label className="dialog-label">文件名：<strong>{dialog.document.title}</strong></label><input className="dialog-input" value={confirmTitle} onChange={(event) => setConfirmTitle(event.target.value)} autoFocus /><div className="dialog-actions"><button type="button" className="outline-button" onClick={() => setDialog(null)}>取消</button><button className="danger-button" disabled={busy || confirmTitle !== dialog.document.title}>永久删除</button></div></form>
          : dialog.kind === "delete-folder" || dialog.kind === "delete-tag" ? <form onSubmit={(event) => void submitDialog(event)}><p className="dialog-copy">{dialog.kind === "delete-folder" ? `确定删除“${dialog.folder.name}”文件夹吗？只有空文件夹可以删除。` : `删除“${dialog.tag.name}”标签不会删除关联文件。`}</p>{error && <p className="feedback error">{error}</p>}<div className="dialog-actions"><button type="button" className="outline-button" onClick={() => setDialog(null)}>取消</button><button className="danger-button" disabled={busy}>确认删除</button></div></form>
            : <form onSubmit={(event) => void submitDialog(event)}><label className="dialog-label" htmlFor="dialog-name">{dialog.kind.includes("folder") ? "文件夹名称" : "标签名称"}</label><input id="dialog-name" className="dialog-input" autoFocus value={dialogName} onChange={(event) => setDialogName(event.target.value)} maxLength={dialog.kind.includes("folder") ? 100 : 40} required />
              {dialog.kind === "folder" && <><label className="dialog-label" htmlFor="dialog-parent">上级文件夹</label><select id="dialog-parent" className="dialog-input" value={dialogParent} onChange={(event) => setDialogParent(event.target.value)}><option value="">我的资料库（根目录）</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></>}
              {(dialog.kind === "tag" || dialog.kind === "edit-tag") && <><label className="dialog-label" htmlFor="dialog-color">标签颜色</label><div className="color-input-row"><input id="dialog-color" type="color" value={dialogColor} onChange={(event) => setDialogColor(event.target.value)} /><span>{dialogColor}</span></div></>}
              {error && <p className="feedback error">{error}</p>}<div className="dialog-actions"><button type="button" className="outline-button" onClick={() => setDialog(null)}>取消</button><button className="primary-button" disabled={busy}>{busy ? "正在保存…" : "保存"}</button></div></form>}
      </section></div>}
      {previewVersion && <Suspense fallback={<div className="preview-backdrop"><div className="preview-loading"><LoaderCircle className="spin" size={23} />正在加载预览组件</div></div>}><DocumentPreview versionId={previewVersion.id} versionLabel={`v${previewVersion.version_no} · ${previewVersion.original_name}`} onClose={() => setPreviewVersion(null)} /></Suspense>}
    </main>
  );
}
