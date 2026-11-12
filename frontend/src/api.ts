export type Envelope<T> = { data: T; request_id?: string };

export class ApiError extends Error {
  status: number;

  constructor(code: string, status: number) {
    super(code);
    this.status = status;
  }
}

// randomUUID is unavailable on plain HTTP origins; getRandomValues remains available.
export function newClientId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<Envelope<T>> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  if (response.status === 204) return { data: undefined as T };
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(body?.error?.code ?? "REQUEST_FAILED", response.status);
  return body as Envelope<T>;
}

export function uploadFile<T>(
  path: string,
  file: File,
  csrf: string,
  onProgress: (progress: number) => void,
  options: { folderId?: string | null; ifMatch?: number } = {},
): Promise<Envelope<T>> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (options.folderId) form.append("folder_id", options.folderId);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.setRequestHeader("Idempotency-Key", newClientId());
    if (options.ifMatch !== undefined) xhr.setRequestHeader("If-Match", `"${options.ifMatch}"`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new ApiError("NETWORK_ERROR", 0));
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = body as { error?: { code?: string } } | null;
        reject(new ApiError(error?.error?.code ?? "REQUEST_FAILED", xhr.status));
        return;
      }
      resolve(body as Envelope<T>);
    };
    xhr.send(form);
  });
}
