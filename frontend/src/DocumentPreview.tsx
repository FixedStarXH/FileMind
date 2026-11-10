import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, Download, LoaderCircle, X } from "lucide-react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { request } from "./api";
import "./DocumentPreview.css";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

const BLOCK_LIMIT = 200;

type PreviewBlock = {
  id: string;
  ordinal: number;
  kind: string;
  text: string;
  locator: Record<string, number | string | number[]>;
  ocr_confidence: number | null;
};
type PreviewData = {
  kind: "pdf" | "image" | "docx" | "pptx" | "text" | "unknown";
  parse_state: string;
  error_code: string | null;
  page_count: number | null;
  warnings: string[];
  blocks: PreviewBlock[];
  has_more: boolean;
};

const kindLabel: Record<PreviewData["kind"], string> = {
  pdf: "PDF 原文", image: "图片原文", docx: "Word 文本", pptx: "幻灯片文本",
  text: "纯文本", unknown: "暂不支持预览",
};

function locatorLabel(block: PreviewBlock): string {
  const locator = block.locator;
  if (typeof locator.page === "number") return `第 ${locator.page} 页`;
  if (typeof locator.slide === "number") return `第 ${locator.slide} 张幻灯片`;
  if (typeof locator.table === "number") return `表格 ${locator.table} · 第 ${locator.row} 行 · 第 ${locator.column} 列`;
  if (typeof locator.paragraph === "number") return `第 ${locator.paragraph} 段`;
  if (typeof locator.line === "number") return `第 ${locator.line} 行`;
  return "文档内容";
}

function isPaged(kind: PreviewData["kind"]): boolean {
  return kind === "pdf" || kind === "pptx";
}

export default function DocumentPreview({
  versionId, versionLabel, onClose,
}: { versionId: string; versionLabel: string; onClose: () => void }) {
  const [data, setData] = useState<PreviewData | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState("");
  const [visualError, setVisualError] = useState("");
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [scale, setScale] = useState(1.15);
  const [selectedBlock, setSelectedBlock] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const requestId = useRef(0);
  const contentUrl = `/api/v1/versions/${versionId}/content`;
  const downloadUrl = `/api/v1/versions/${versionId}/download`;
  const markdown = versionLabel.toLowerCase().endsWith(".md");

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && dialogRef.current) {
        const controls = [...dialogRef.current.querySelectorAll<HTMLElement>("a[href], button:not(:disabled)")];
        const first = controls[0]; const last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first?.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = originalOverflow;
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    let active = true;
    const token = ++requestId.current;
    setLoading(true);
    setError("");
    setVisualError("");
    setData(null);
    setPage(1);
    setSelectedBlock(null);
    void request<PreviewData>(`/versions/${versionId}/preview?limit=${BLOCK_LIMIT}`)
      .then(async (result) => {
        const initial = result.data;
        const firstPage = isPaged(initial.kind)
          ? (await request<PreviewData>(`/versions/${versionId}/preview?page=1&limit=${BLOCK_LIMIT}`)).data
          : initial;
        if (active && token === requestId.current) setData(firstPage);
      })
      .catch(() => { if (active) setError("暂时无法读取预览，请稍后重试或下载原件。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; requestId.current += 1; };
  }, [versionId]);

  useEffect(() => {
    let active = true;
    if (data?.kind !== "pdf") return;
    const loadingTask = pdfjs.getDocument({ url: contentUrl, withCredentials: true });
    void loadingTask.promise
      .then((document) => {
        if (active) setPdf(document);
      })
      .catch(() => { if (active) setVisualError("PDF 原页暂时无法显示，右侧仍可阅读已提取的文字。"); });
    return () => { active = false; void loadingTask.destroy(); };
  }, [contentUrl, data?.kind]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return;
    let active = true;
    let renderTask: pdfjs.RenderTask | null = null;
    void pdf.getPage(page).then(async (pdfPage) => {
      if (!active || !canvasRef.current) return;
      const viewport = pdfPage.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas.getContext("2d");
      if (!context) return;
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      renderTask = pdfPage.render({ canvas, canvasContext: context, viewport });
      await renderTask.promise;
      if (active) setVisualError("");
    }).catch((cause: unknown) => {
      if (active && !(cause instanceof Error && cause.name === "RenderingCancelledException")) {
        setVisualError("PDF 页面渲染失败，右侧仍可阅读已提取的文字。");
      }
    });
    return () => { active = false; renderTask?.cancel(); };
  }, [pdf, page, scale]);

  async function changePage(next: number) {
    if (!data || pageLoading || next === page || next < 1 || next > Math.max(1, data.page_count ?? 1)) return;
    const token = ++requestId.current;
    const previous = page;
    setPage(next);
    setPageLoading(true);
    setMoreLoading(false);
    setSelectedBlock(null);
    setError("");
    try {
      const result = await request<PreviewData>(`/versions/${versionId}/preview?page=${next}&limit=${BLOCK_LIMIT}`);
      if (token === requestId.current) setData(result.data);
    } catch {
      if (token === requestId.current) { setPage(previous); setError("当前页无法读取，请重试或下载原件。"); }
    } finally {
      if (token === requestId.current) setPageLoading(false);
    }
  }

  async function loadMore() {
    if (!data || moreLoading || !data.has_more) return;
    const token = requestId.current;
    setMoreLoading(true);
    setError("");
    const pageQuery = isPaged(data.kind) ? `&page=${page}` : "";
    try {
      const result = await request<PreviewData>(
        `/versions/${versionId}/preview?offset=${data.blocks.length}&limit=${BLOCK_LIMIT}${pageQuery}`,
      );
      if (token === requestId.current) setData((current) => current ? {
        ...current,
        blocks: [...current.blocks, ...result.data.blocks],
        has_more: result.data.has_more,
        warnings: [...new Set([...current.warnings, ...result.data.warnings])],
      } : current);
    } catch {
      if (token === requestId.current) setError("后续内容加载失败，请重试。");
    } finally {
      if (token === requestId.current) setMoreLoading(false);
    }
  }

  function selectBlock(id: string) {
    setSelectedBlock(id);
    document.getElementById(`preview-block-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  const overlays = useMemo(() => data?.blocks.filter((block) => Array.isArray(block.locator.bbox)) ?? [], [data]);
  const pageLimit = Math.max(1, data?.page_count ?? pdf?.numPages ?? 1);
  const visual = data?.kind === "pdf" || data?.kind === "image";
  const emptyText = data?.parse_state === "ready"
    ? visual ? "原件可以浏览，当前没有提取到文字。" : "这个文件没有可显示的文字。"
    : "解析完成后将在这里显示文字。";

  const pageControls = data && isPaged(data.kind) && <nav className="preview-page-controls" aria-label={data.kind === "pptx" ? "幻灯片导航" : "PDF 页码导航"}>
    <button type="button" className="outline-button" disabled={page <= 1 || pageLoading} onClick={() => void changePage(page - 1)}><ChevronLeft size={16} />上一页</button>
    <span aria-live="polite">第 {page} / {pageLimit} {data.kind === "pptx" ? "张" : "页"}</span>
    <button type="button" className="outline-button" disabled={page >= pageLimit || pageLoading} onClick={() => void changePage(page + 1)}>下一页<ChevronRight size={16} /></button>
  </nav>;

  const moreButton = data?.has_more && <button type="button" className="preview-load-more" onClick={() => void loadMore()} disabled={moreLoading || pageLoading}>
    {moreLoading ? <LoaderCircle className="spin" size={16} /> : null}加载后续内容
  </button>;

  return <div className="preview-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className="preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title">
      <header className="preview-header">
        <div className="preview-heading"><span className="detail-kicker">版本预览 · {versionLabel}</span><h2 id="preview-title">文档预览</h2></div>
        <div className="preview-header-actions"><a className="download-full" href={downloadUrl}><Download size={16} aria-hidden="true" />下载原件</a><button ref={closeRef} className="top-icon-button" type="button" onClick={onClose} aria-label="关闭预览"><X size={19} /></button></div>
      </header>
      {error && <div className="preview-error" role="alert">{error}</div>}
      {loading ? <div className="preview-loading"><LoaderCircle className="spin" size={24} />正在准备预览</div>
        : !data ? <div className="preview-empty">没有可显示的预览内容，请下载原件。</div>
          : <>
            {data.warnings.includes("OCR_LOW_CONFIDENCE") && <div className="preview-warning"><AlertTriangle size={16} />部分文字识别置信度较低，请对照原图核验。</div>}
            {data.parse_state !== "ready" && <div className="preview-warning">{data.error_code ? `解析失败：${data.error_code}。` : `当前状态：${data.parse_state}。`}仍可查看原件或下载，文字将在解析完成后出现。</div>}
            <div className={`preview-body ${visual ? "has-visual" : "is-textual"}`}>
              {data.kind === "pdf" && <div className="pdf-stage">
                <div className="pdf-controls">{pageControls}<div className="preview-zoom"><button type="button" className="zoom-button" disabled={scale <= 0.65} onClick={() => setScale((value) => Math.max(0.65, Number((value - 0.15).toFixed(2))))} aria-label="缩小 PDF">−</button><span>{Math.round(scale * 100)}%</span><button type="button" className="zoom-button" disabled={scale >= 2} onClick={() => setScale((value) => Math.min(2, Number((value + 0.15).toFixed(2))))} aria-label="放大 PDF">+</button></div></div>
                {visualError && <div className="preview-visual-error" role="status">{visualError}</div>}
                <div className="pdf-page-wrap"><div className="pdf-page"><canvas ref={canvasRef} aria-label={`PDF 第 ${page} 页`} role="img" />{!pageLoading && overlays.map((block) => {
                  const box = block.locator.bbox as number[];
                  return <button type="button" key={block.id} className={`ocr-overlay ${selectedBlock === block.id ? "is-selected" : ""}`} style={{ left: Number(box[0]) * scale, top: Number(box[1]) * scale, width: (Number(box[2]) - Number(box[0])) * scale, height: (Number(box[3]) - Number(box[1])) * scale }} title={block.text} onClick={() => selectBlock(block.id)} aria-label={`定位识别文字：${block.text}`} />;
                })}</div></div>
              </div>}
              {data.kind === "image" && <div className="image-stage">
                {visualError && <div className="preview-visual-error" role="status">{visualError}</div>}
                <div className="image-canvas"><img src={contentUrl} alt="文档原图" onError={() => setVisualError("原图暂时无法显示，请下载原件查看。")}/>{overlays.map((block) => {
                  const box = block.locator.bbox as number[];
                  const width = Number(block.locator.width); const height = Number(block.locator.height);
                  if (!width || !height) return null;
                  return <button type="button" key={block.id} className={`ocr-overlay ${selectedBlock === block.id ? "is-selected" : ""}`} style={{ left: `${Number(box[0]) / width * 100}%`, top: `${Number(box[1]) / height * 100}%`, width: `${(Number(box[2]) - Number(box[0])) / width * 100}%`, height: `${(Number(box[3]) - Number(box[1])) / height * 100}%` }} title={block.text} onClick={() => selectBlock(block.id)} aria-label={`定位识别文字：${block.text}`} />;
                })}</div>
              </div>}
              {visual && <aside className="preview-block-list" aria-label="提取的文字">
                <div className="preview-list-heading"><strong>{data.kind === "image" ? "识别文字" : "本页文字"}</strong><span>{data.blocks.length} 条</span></div>
                {pageLoading ? <div className="preview-loading"><LoaderCircle className="spin" size={18} />正在读取本页文字</div> : data.blocks.length ? data.blocks.map((block) => <button type="button" id={`preview-block-${block.id}`} key={block.id} className={`preview-block ${selectedBlock === block.id ? "is-selected" : ""}`} onClick={() => selectBlock(block.id)}><span>{locatorLabel(block)}{block.ocr_confidence !== null && <small> · OCR {Math.round(block.ocr_confidence * 100)}%</small>}</span><p>{block.text}</p></button>) : <div className="preview-empty">{emptyText}</div>}
                {moreButton}
              </aside>}
              {!visual && <div className="reader-stage" role="region" aria-label="文件内容">
                <div className="reader-toolbar"><div><span className="reader-kind">{kindLabel[data.kind]}</span><span className="reader-note">{data.kind === "docx" || data.kind === "pptx" ? "提取文本视图 · 排版可能与原文件不同" : data.kind === "text" ? "按原始行号阅读" : ""}</span></div>{data.kind === "pptx" && pageControls}</div>
                <div className={`reader-scroll ${data.kind === "pptx" ? "is-slides" : ""}`}>
                  {data.kind === "unknown" ? <div className="preview-empty">此类型暂不支持在线预览，请下载原件。</div>
                    : <div className={`reader-paper reader-${data.kind}`}>
                      {data.kind === "pptx" && <div className="slide-caption"><span>幻灯片 {page}</span><small>第 {page} / {pageLimit} 张</small></div>}
                      {pageLoading ? <div className="preview-loading"><LoaderCircle className="spin" size={18} />正在读取内容</div>
                        : data.blocks.length ? data.blocks.map((block) => data.kind === "text" ? <div id={`preview-block-${block.id}`} key={block.id} className="reader-line"><span className="reader-line-number">{block.locator.line}</span><span className={markdown && /^#{1,6}\s/.test(block.text) ? "reader-markdown-heading" : "reader-line-text"}>{markdown ? block.text.replace(/^#{1,6}\s+/, "") : block.text}</span></div>
                          : <article id={`preview-block-${block.id}`} key={block.id} className={`reader-content-block ${block.kind.includes("table") ? "is-table-cell" : ""}`}><span className="reader-location">{locatorLabel(block)}</span><p>{block.text}</p></article>)
                          : <div className="preview-empty">{emptyText}</div>}
                      {moreButton}
                    </div>}
                </div>
              </div>}
            </div>
          </>}
    </section>
  </div>;
}
