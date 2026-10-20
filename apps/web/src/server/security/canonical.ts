/**
 * Canonical JSON shared with Python (apex_worker.agent.canonical): sorted keys, no whitespace,
 * ASCII only, integers/strings/booleans/null only (floats are rejected so both languages agree byte-for-byte).
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("canonical JSON allows only safe integers; encode decimals as strings");
    return String(value);
  }
  if (typeof value === "string") {
    if (!/^[\x20-\x7e]*$/.test(value)) throw new Error("canonical JSON strings must be printable ASCII");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
    return `{${keys.map((k) => `${canonicalJson(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
  }
  throw new Error(`unsupported canonical JSON type: ${typeof value}`);
}

export function b64url(buf: Uint8Array): string {
  return Buffer.from(buf).toString("base64url");
}

export function fromB64url(s: string): Buffer {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) throw new Error("invalid base64url");
  return Buffer.from(s, "base64url");
}
