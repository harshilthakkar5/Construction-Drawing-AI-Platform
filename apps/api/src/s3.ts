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
import { normalizeSpacesEndpoint } from "./spacesEndpoint.js";
import { storage } from "./storage.js";

/** Applies the bucket-in-the-hostname correction and says so once. */
function endpointFor(raw: string, label: string): string {
  const { endpoint, corrected } = normalizeSpacesEndpoint(raw, storage.bucket);
  if (corrected) {
    console.warn(
      `[s3] ${label} included the bucket name — using ${endpoint} instead. ` +
        `Set ${label}=${endpoint} in .env. Objects uploaded before this ` +
        `correction were stored under a "${storage.bucket}/" key prefix and will not be found.`,
    );
  }
  return endpoint;
}

const internalEndpoint = endpointFor(
  storage.endpoint,
  storage.backend === "local" ? "LOCAL_S3_ENDPOINT" : "SPACES_ENDPOINT",
);
const publicEndpoint = endpointFor(
  storage.publicEndpoint,
  storage.backend === "local" ? "LOCAL_S3_PUBLIC_ENDPOINT" : "SPACES_PUBLIC_ENDPOINT",
);

function client(endpoint: string) {
  return new S3Client({
    endpoint,
    region: storage.region,
    credentials: { accessKeyId: storage.key, secretAccessKey: storage.secret },
    // Path-style URLs (endpoint.com/bucket/key) work for both MinIO (required)
    // and DO Spaces; the endpoint must therefore be the REGION host
    // (e.g. https://blr1.digitaloceanspaces.com), never the bucket subdomain.
    forcePathStyle: true,
  });
}

/**
 * The store as the SERVER reaches it: DigitalOcean Spaces, or the MinIO
 * container beside this one. Every request the API makes itself goes here.
 */
export const s3 = client(internalEndpoint);

/**
 * The store as a BROWSER reaches it, used ONLY to sign URLs that leave this
 * process. The two differ whenever the server's route to storage is a name
 * only the server can resolve — `http://minio:9000` is the whole storage layer
 * for this container and nothing at all for a laptop across the office.
 *
 * It has to be a separate client rather than a string substitution afterwards:
 * SigV4 signs the Host header, so a URL signed for `minio:9000` and requested
 * at `192.168.1.50:9000` is rejected as SignatureDoesNotMatch. When the two
 * endpoints are the same — the ordinary Spaces deployment — this IS the same
 * client, so nothing is signed twice.
 */
const s3Public = publicEndpoint === internalEndpoint ? s3 : client(publicEndpoint);

const BUCKET = storage.bucket;

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

/** Handed to the browser, which PUTs the bytes straight at object storage —
 * so it is signed against the PUBLIC endpoint. */
export function presignUploadPart(key: string, uploadId: string, partNumber: number) {
  return getSignedUrl(
    s3Public,
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

/** A URL for the BROWSER: page images, thumbnails, the original PDF. */
export function presignGetObject(key: string, expiresIn = 3600) {
  return getSignedUrl(s3Public, new GetObjectCommand({ Bucket: BUCKET, Key: key }), { expiresIn });
}

/** A URL for another SERVER-side service (the malware scanner), which sits on
 * this deployment's own network and must not be sent a LAN address that only
 * means something to a browser. */
export function presignGetObjectInternal(key: string, expiresIn = 3600) {
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
    let listing;
    try {
      listing = await s3.send(
        new ListObjectsV2Command({
          Bucket: BUCKET,
          Prefix: prefix,
          ContinuationToken: continuationToken,
        }),
      );
    } catch (err) {
      // A LIST can't legitimately 404 on a key — that means the request was
      // routed as a GetObject, i.e. the endpoint/bucket pair is wrong.
      if (isNotFound(err)) {
        throw new Error(
          `Listing "${prefix}" in bucket "${BUCKET}" returned NoSuchKey. That means the ` +
            `request was addressed as an object, not a listing — check SPACES_ENDPOINT is ` +
            `the region host (e.g. https://blr1.digitaloceanspaces.com) and not the bucket URL.`,
          { cause: err },
        );
      }
      throw err;
    }
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
