import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  ListPartsCommand,
  S3Client,
  S3ServiceException,
  UploadPartCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { env } from "./env.js";

/**
 * DigitalOcean Spaces in prod, MinIO locally — S3 API with endpoint override.
 * Path-style addressing is required for MinIO.
 */
export const s3 = new S3Client({
  endpoint: env.SPACES_ENDPOINT,
  region: env.SPACES_REGION,
  credentials: {
    accessKeyId: env.SPACES_KEY,
    secretAccessKey: env.SPACES_SECRET,
  },
  // Path-style URLs (endpoint.com/bucket/key) work for both MinIO (required)
  // and DO Spaces; SPACES_ENDPOINT must therefore be the REGION endpoint
  // (e.g. https://blr1.digitaloceanspaces.com), never the bucket subdomain.
  forcePathStyle: true,
});

const BUCKET = env.SPACES_BUCKET;

/** Optional canned ACL (off by default — objects stay private and are served
 * via presigned URLs). Applied at multipart initiation; the ACL set there
 * carries to the completed object. */
const ACL = env.SPACES_ACL;

/** 8 MiB parts (S3 minimum is 5 MiB for all but the last part). */
export const PART_SIZE = 8 * 1024 * 1024;

export async function createMultipartUpload(key: string, contentType: string) {
  const res = await s3.send(
    new CreateMultipartUploadCommand({
      Bucket: BUCKET,
      Key: key,
      ContentType: contentType,
      ...(ACL ? { ACL } : {}),
    }),
  );
  if (!res.UploadId) throw new Error("S3 returned no UploadId");
  return res.UploadId;
}

export function presignUploadPart(key: string, uploadId: string, partNumber: number) {
  return getSignedUrl(
    s3,
    new UploadPartCommand({ Bucket: BUCKET, Key: key, UploadId: uploadId, PartNumber: partNumber }),
    { expiresIn: 3600 },
  );
}

/** Uploaded parts so far — used both for resume and for server-side completion. */
export async function listUploadedParts(key: string, uploadId: string) {
  const parts: { PartNumber: number; ETag: string; Size: number }[] = [];
  let marker: string | undefined;
  do {
    const res = await s3.send(
      new ListPartsCommand({ Bucket: BUCKET, Key: key, UploadId: uploadId, PartNumberMarker: marker }),
    );
    for (const p of res.Parts ?? []) {
      if (p.PartNumber && p.ETag) {
        parts.push({ PartNumber: p.PartNumber, ETag: p.ETag, Size: p.Size ?? 0 });
      }
    }
    marker = res.IsTruncated ? res.NextPartNumberMarker : undefined;
  } while (marker);
  return parts.sort((a, b) => a.PartNumber - b.PartNumber);
}

export async function completeMultipartUpload(key: string, uploadId: string) {
  const parts = await listUploadedParts(key, uploadId);
  if (parts.length === 0) throw new Error("no uploaded parts to complete");
  await s3.send(
    new CompleteMultipartUploadCommand({
      Bucket: BUCKET,
      Key: key,
      UploadId: uploadId,
      MultipartUpload: {
        Parts: parts.map((p) => ({ PartNumber: p.PartNumber, ETag: p.ETag })),
      },
    }),
  );
}

export async function abortMultipartUpload(key: string, uploadId: string) {
  await s3.send(new AbortMultipartUploadCommand({ Bucket: BUCKET, Key: key, UploadId: uploadId }));
}

export function presignGetObject(key: string, expiresIn = 3600) {
  return getSignedUrl(s3, new GetObjectCommand({ Bucket: BUCKET, Key: key }), { expiresIn });
}

/** Used to purge objects that fail upload validation / malware scanning. */
export async function deleteObject(key: string) {
  await s3.send(new DeleteObjectCommand({ Bucket: BUCKET, Key: key }));
}

/** True when the object exists (reprocess guard: no point queueing a job for
 * a file that was never fully uploaded or was deleted by validation). */
export async function objectExists(key: string): Promise<boolean> {
  try {
    await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: key }));
    return true;
  } catch (err) {
    if (err instanceof S3ServiceException && (err.$metadata.httpStatusCode === 404 || err.name === "NotFound")) {
      return false;
    }
    throw err;
  }
}

/** Delete every object under a prefix (project/document deletion cleanup).
 *
 * Deletes objects individually rather than via the batch DeleteObjects API:
 * DigitalOcean Spaces is inconsistent with S3's multi-object delete (it can
 * return a NoSuchKey 404 for the batch request), whereas single-object
 * DeleteObject works identically on Spaces and MinIO. Already-gone keys are
 * tolerated so a partially-cleaned prefix (e.g. a re-run) still succeeds.
 * Deletes run with bounded concurrency. */
export async function deletePrefix(prefix: string): Promise<number> {
  const CONCURRENCY = 16;
  let deleted = 0;
  let continuationToken: string | undefined;
  do {
    const listing = await s3.send(
      new ListObjectsV2Command({ Bucket: BUCKET, Prefix: prefix, ContinuationToken: continuationToken }),
    );
    const keys = (listing.Contents ?? []).flatMap((o) => (o.Key ? [o.Key] : []));
    for (let i = 0; i < keys.length; i += CONCURRENCY) {
      const slice = keys.slice(i, i + CONCURRENCY);
      const results = await Promise.allSettled(slice.map((Key) => deleteObject(Key)));
      for (const r of results) {
        if (r.status === "fulfilled") deleted += 1;
        else if (!isNotFound(r.reason)) throw r.reason; // real error — surface it
      }
    }
    continuationToken = listing.IsTruncated ? listing.NextContinuationToken : undefined;
  } while (continuationToken);
  return deleted;
}

function isNotFound(err: unknown): boolean {
  return (
    err instanceof S3ServiceException &&
    (err.$metadata.httpStatusCode === 404 || err.name === "NoSuchKey" || err.name === "NotFound")
  );
}
