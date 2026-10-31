const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const KEY = "acr_token";

export const API_BASE = BASE;
export const getToken = () => (typeof window === "undefined" ? null : localStorage.getItem(KEY));
export const setToken = (t: string | null) => (t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY));
export const wsUrl = (path: string) => BASE.replace(/^http/, "ws") + path;

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) { super(message); }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.json !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(init.json); }
  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { const b = await res.json(); detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail); } catch {}
    if (res.status === 429) detail = `${detail}. Try again in ${res.headers.get("Retry-After") ?? "a few"}s.`;
    throw new ApiError(res.status, detail, Number(res.headers.get("Retry-After")) || undefined);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}
