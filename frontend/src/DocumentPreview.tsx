import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, Download, LoaderCircle, X } from "lucide-react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { request } from "./api";
import "./DocumentPreview.css";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

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

export default function DocumentPreview({
  versionId, versionLabel, onClose,
}: { versionId: string; versionLabel: string; onClose: () => void }) {
  const [data, setData] = useState<PreviewData | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [scale, setScale] = useState(1.15);
  const [selectedBlock, setSelectedBlock] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const contentUrl = `/api/v1/versions/${versionId}/content`;
  const downloadUrl = `/api/v1/versions/${versionId}/download`;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setData(null);
    setPdf(null);
    setPage(1);
    void request<PreviewData>(`/versions/${versionId}/preview?limit=500`)
      .then(async (result) => {
        if (!alive) return;
        setData(result.data);
        if (result.data.kind === "pdf") {
          const loaded = await pdfjs.getDocument({ url: contentUrl, withCredentials: true }).promise;
          if (alive) setPdf(loaded);
        }
      })
      .catch(() => { if (alive) setError("暂时无法读取预览，请稍后重试或下载原件。"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [contentUrl, versionId]);

  useEffect(() => {
    if (!data || !["pdf", "pptx"].includes(data.kind) || page === 1 && data.kind === "text") return;
    let alive = true;
    void request<PreviewData>(`/versions/${versionId}/preview?page=${page}&limit=1000`)
      .then((result) => {
        if (alive) setData((current) => current ? { ...current, blocks: result.data.blocks, warnings: result.data.warnings } : current);
      })
      .catch(() => { if (alive) setError("无法读取当前页的文本定位。"); });
    return () => { alive = false; };
  }, [data?.kind, page, versionId]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return;
    let alive = true;
    void pdf.getPage(page).then(async (pdfPage) => {
      if (!alive || !canvasRef.current) return;
      const viewport = pdfPage.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas.getContext("2d");
      if (!context) return;
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      await pdfPage.render({ canvas, canvasContext: context, viewport }).promise;
    }).catch(() => { if (alive) setError("PDF 页面渲染失败。"); });
    return () => { alive = false; };
  }, [pdf, page, scale]);

  const overlays = useMemo(() => data?.blocks.filter((block) => Array.isArray(block.locator.bbox)) ?? [], [data]);
  const pageLimit = Math.max(1, data?.page_count ?? 1);

  function locatorLabel(block: PreviewBlock): string {
    const locator = block.locator;
    if (typeof locator.page === "number") return `第 ${locator.page} 页`;
    if (typeof locator.slide === "number") return `第 ${locator.slide} 张幻灯片`;
    if (typeof locator.paragraph === "number") return `第 ${locator.paragraph} 段`;
    if (typeof locator.line === "number") return `第 ${locator.line} 行`;
    if (typeof locator.table === "number") return `表格 ${locator.table} · 第 ${locator.row} 行`;
    return "文档内容";
  }

  return <div className="preview-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title">
      <header className="preview-header"><div><span className="detail-kicker">版本预览 · {versionLabel}</span><h2 id="preview-title">文档预览</h2></div><div className="preview-header-actions"><a className="download-full" href={downloadUrl}><Download size={15} />下载原件</a><button className="top-icon-button" onClick={onClose} aria-label="关闭预览"><X size={18} /></button></div></header>
      {error && <div className="preview-error" role="alert">{error}</div>}
      {loading ? <div className="preview-loading"><LoaderCircle className="spin" size={23} />正在准备预览</div>
        : !data ? <div className="preview-empty">没有可显示的预览内容。</div>
          : <>
            {data.warnings.includes("OCR_LOW_CONFIDENCE") && <div className="preview-warning"><AlertTriangle size={15} />部分文字识别置信度较低，请对照原文核验。</div>}
            {data.parse_state !== "ready" && <div className="preview-warning">{data.error_code ? `解析失败：${data.error_code}。` : `当前状态：${data.parse_state}。`}解析完成后可查看结构化预览。</div>}
            <div className={`preview-body ${data.kind === "pdf" || data.kind === "image" ? "has-visual" : ""}`}>
              {data.kind === "pdf" && <div className="pdf-stage"><div className="pdf-controls"><button className="outline-button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronLeft size={15} />上一页</button><span>第 {page} / {pageLimit} 页</span><button className="outline-button" disabled={page >= pageLimit} onClick={() => setPage((value) => Math.min(pageLimit, value + 1))}>下一页<ChevronRight size={15} /></button><button className="zoom-button" onClick={() => setScale((value) => Math.max(0.65, value - 0.15))} aria-label="缩小">−</button><button className="zoom-button" onClick={() => setScale((value) => Math.min(2, value + 0.15))} aria-label="放大">+</button></div><div className="pdf-page-wrap"><div className="pdf-page"><canvas ref={canvasRef} />{overlays.map((block) => {
                const box = block.locator.bbox as number[];
                const left = Number(box[0]) * scale; const top = Number(box[1]) * scale;
                const width = (Number(box[2]) - Number(box[0])) * scale; const height = (Number(box[3]) - Number(box[1])) * scale;
                return <button key={block.id} className={`ocr-overlay ${selectedBlock === block.id ? "is-selected" : ""}`} style={{ left, top, width, height }} title={block.text} onClick={() => setSelectedBlock(block.id)} aria-label={`识别文字：${block.text}`} />;
              })}</div></div></div>}
              {data.kind === "image" && <div className="image-stage"><div className="image-canvas"><img src={contentUrl} alt="文档原图" />{overlays.map((block) => {
                const box = block.locator.bbox as number[];
                const width = Number(block.locator.width); const height = Number(block.locator.height);
                return <button key={block.id} className={`ocr-overlay ${selectedBlock === block.id ? "is-selected" : ""}`} style={{ left: `${Number(box[0]) / width * 100}%`, top: `${Number(box[1]) / height * 100}%`, width: `${(Number(box[2]) - Number(box[0])) / width * 100}%`, height: `${(Number(box[3]) - Number(box[1])) / height * 100}%` }} title={block.text} onClick={() => setSelectedBlock(block.id)} aria-label={`识别文字：${block.text}`} />;
              })}</div></div>}
              <div className="preview-block-list">{data.blocks.length ? data.blocks.map((block) => <button key={block.id} className={`preview-block ${selectedBlock === block.id ? "is-selected" : ""}`} onClick={() => setSelectedBlock(block.id)}><span>{locatorLabel(block)}{block.ocr_confidence !== null && <small> · OCR {Math.round(block.ocr_confidence * 100)}%</small>}</span><p>{block.text}</p></button>) : <div className="preview-empty">{data.parse_state === "ready" ? "没有提取到可显示的文字。" : "等待解析完成后显示内容。"}</div>}{data.has_more && <p className="preview-more">当前只显示前 500 条，请使用定位查看 PDF 或幻灯片页面。</p>}</div>
            </div>
          </>}
    </section>
  </div>;
}
