import {
  AbortMultipartUploadCommand,
  CompleteMultipartUploadCommand,
  CreateMultipartUploadCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  ListPartsCommand,
  S3Client,
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
  region: "us-east-1",
  credentials: {
    accessKeyId: env.SPACES_KEY,
    secretAccessKey: env.SPACES_SECRET,
  },
  forcePathStyle: true,
});

const BUCKET = env.SPACES_BUCKET;

/** 8 MiB parts (S3 minimum is 5 MiB for all but the last part). */
export const PART_SIZE = 8 * 1024 * 1024;

export async function createMultipartUpload(key: string, contentType: string) {
  const res = await s3.send(
    new CreateMultipartUploadCommand({ Bucket: BUCKET, Key: key, ContentType: contentType }),
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
