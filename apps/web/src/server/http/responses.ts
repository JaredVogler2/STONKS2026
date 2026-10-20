import "server-only";
import { NextResponse } from "next/server";

export function json<T>(body: T, status = 200, headers: Record<string, string> = {}): NextResponse {
  return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

export function problem(status: number, code: string, message: string, extra: Record<string, unknown> = {}): NextResponse {
  return json({ error: { code, message, ...extra } }, status);
}

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export const MAX_BODY_BYTES = 256 * 1024;

/** Read a request body with a hard byte cap (413) without trusting Content-Length. */
export async function readBodyCapped(req: Request, cap = MAX_BODY_BYTES): Promise<Uint8Array> {
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (declared > cap) throw new HttpError(413, "PAYLOAD_TOO_LARGE", `body exceeds ${cap} bytes`);
  if (!req.body) return new Uint8Array();
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > cap) {
      await reader.cancel();
      throw new HttpError(413, "PAYLOAD_TOO_LARGE", `body exceeds ${cap} bytes`);
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}

const MAX_DEPTH = 12;

/** Parse JSON defensively: bounded depth and key count (nested-bomb protection). */
export function parseJsonBounded(bytes: Uint8Array): unknown {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new HttpError(400, "INVALID_ENCODING", "body must be UTF-8");
  }
  let depth = 0;
  let maxDepth = 0;
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (ch === "\\") i++;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{" || ch === "[") {
      depth++;
      if (depth > maxDepth) maxDepth = depth;
      if (maxDepth > MAX_DEPTH) throw new HttpError(400, "JSON_TOO_DEEP", `JSON nesting exceeds ${MAX_DEPTH}`);
    } else if (ch === "}" || ch === "]") depth--;
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, "INVALID_JSON", "body is not valid JSON");
  }
}

export function errorResponse(err: unknown): NextResponse {
  if (err instanceof HttpError) return problem(err.status, err.code, err.message, err.extra);
  const name = err instanceof Error ? err.name : "Error";
  if (name === "DatabaseNotReady") return problem(503, "DATABASE_NOT_READY", "database migrations are pending");
  // never echo internal error text (could contain SQL or secrets)
  console.error("[apex] unhandled error", name);
  return problem(500, "INTERNAL", "internal error");
}
