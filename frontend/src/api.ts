export type Envelope<T> = { data: T; request_id?: string };

export class ApiError extends Error {
  status: number;

  constructor(code: string, status: number) {
    super(code);
    this.status = status;
  }
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
    xhr.setRequestHeader("Idempotency-Key", crypto.randomUUID());
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
