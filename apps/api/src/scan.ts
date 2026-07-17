import { GetObjectCommand } from "@aws-sdk/client-s3";
import { env } from "./env.js";
import { presignGetObject, s3 } from "./s3.js";

/**
 * Upload validation + malware-scan hook (Phase 5), both run server-side after
 * the multipart upload completes and BEFORE the document is queued for
 * processing — the API never proxies file bytes; it reads at most the first
 * few bytes from object storage.
 */

/** Cheap content check: the stored object must start with the %PDF- magic. */
export async function objectLooksLikePdf(key: string): Promise<boolean> {
  const res = await s3.send(
    new GetObjectCommand({ Bucket: env.SPACES_BUCKET, Key: key, Range: "bytes=0-4" }),
  );
  const head = Buffer.from(await res.Body!.transformToByteArray());
  return head.toString("latin1").startsWith("%PDF-");
}

export type ScanVerdict = "clean" | "infected" | "skipped";

/**
 * Pluggable malware scan. When MALWARE_SCAN_URL is set, POSTs
 * {key, url: <presigned GET>} and expects {status: "clean" | "infected"}
 * (e.g. a small ClamAV REST sidecar). Unset → "skipped" (documented no-op
 * hook). Scanner errors fail CLOSED: an unreachable scanner blocks the file.
 */
export async function scanUploadedObject(key: string): Promise<ScanVerdict> {
  const scannerUrl = process.env.MALWARE_SCAN_URL;
  if (!scannerUrl) return "skipped";
  const res = await fetch(scannerUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, url: await presignGetObject(key, 300) }),
  });
  if (!res.ok) throw new Error(`malware scanner returned ${res.status}`);
  const body = (await res.json()) as { status?: string };
  return body.status === "clean" ? "clean" : "infected";
}
