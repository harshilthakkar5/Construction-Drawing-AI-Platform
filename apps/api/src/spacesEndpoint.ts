/**
 * The S3 endpoint must be the REGION host (https://blr1.digitaloceanspaces.com),
 * not the bucket URL the DigitalOcean console shows you
 * (https://<bucket>.blr1.digitaloceanspaces.com).
 *
 * With `forcePathStyle` — required by MinIO and used for both backends — the
 * SDK appends the bucket itself. A bucket-scoped endpoint therefore addresses
 * `<bucket>.host/<bucket>/<key>`: Spaces reads the bucket from the host and
 * treats the rest as the key, which silently doubles the prefix on writes and
 * makes ListObjectsV2 arrive as a GetObject — failing with a bewildering
 * `NoSuchKey` during project deletion.
 *
 * Pure so it can be unit-tested without constructing a client.
 */
export function normalizeSpacesEndpoint(
  endpoint: string,
  bucket: string,
): { endpoint: string; corrected: boolean } {
  try {
    const url = new URL(endpoint);
    if (url.hostname.startsWith(`${bucket}.`)) {
      url.hostname = url.hostname.slice(bucket.length + 1);
      return { endpoint: url.origin, corrected: true };
    }
    return { endpoint, corrected: false };
  } catch {
    return { endpoint, corrected: false }; // not a URL — let the SDK complain
  }
}
