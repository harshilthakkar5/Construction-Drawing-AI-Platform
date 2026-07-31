/**
 * Input sanitization helpers (Phase 5). Zod handles shape/length validation;
 * this covers content: object keys never embed filenames (they are
 * UUID-based), but filenames ARE rendered in the UI and embedded in Claude
 * prompts as chunk metadata, so strip anything that could smuggle path
 * segments, control characters, or markup-ish angle brackets.
 */

const CONTROL_CHARS = /[\u0000-\u001f\u007f]/g;

export function sanitizeFilename(raw: string): string {
  const basename = raw.split(/[\\/]/).pop() ?? "";
  const cleaned = basename.replace(CONTROL_CHARS, "").replace(/[<>]/g, "").trim();
  return cleaned.slice(0, 255);
}

/**
 * Free-text fields that get rendered back into the UI (profile names, support
 * tickets). Keeps newlines and tabs — a support message is multi-line — but
 * drops the other control characters and trims.
 */
export function sanitizeText(raw: string): string {
  return raw.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim();
}
/** True when the sanitized name still looks like a usable PDF filename. */
export function isValidPdfFilename(sanitized: string): boolean {
  return sanitized.length > 0 && /\.pdf$/i.test(sanitized);
}
