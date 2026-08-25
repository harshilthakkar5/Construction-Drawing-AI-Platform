"""S3-compatible object storage (DigitalOcean Spaces / local MinIO).

The key builders are GENERATED from packages/shared/src/index.ts (the single
source for the bucket layout) and re-exported here — see generated.py.
"""

from urllib.parse import urlparse, urlunparse

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

import config
import logutil

log = logutil.get("storage")


def normalize_endpoint(endpoint: str, bucket: str) -> str:
    """The endpoint must be the REGION host, not the bucket URL.

    Path addressing appends the bucket itself, so a bucket-scoped endpoint
    addresses `<bucket>.host/<bucket>/<key>` — the object lands under a
    doubled prefix and listing fails. Mirrors normalizeSpacesEndpoint in
    apps/api/src/spacesEndpoint.ts; keep the two in sync.
    """
    try:
        parts = urlparse(endpoint)
        if parts.hostname and parts.hostname.startswith(f"{bucket}."):
            host = parts.hostname[len(bucket) + 1 :]
            if parts.port:
                host = f"{host}:{parts.port}"
            fixed = urlunparse((parts.scheme, host, "", "", "", ""))
            log.warning(
                "SPACES_ENDPOINT included the bucket name — using %s instead; "
                "set SPACES_ENDPOINT=%s in .env",
                fixed,
                fixed,
            )
            return fixed
    except ValueError:
        pass
    return endpoint


_s3 = boto3.client(
    "s3",
    endpoint_url=normalize_endpoint(config.SPACES_ENDPOINT, config.SPACES_BUCKET),
    aws_access_key_id=config.SPACES_KEY,
    aws_secret_access_key=config.SPACES_SECRET,
    region_name=config.SPACES_REGION,
    config=Config(
        s3={"addressing_style": "path"},
        # Must cover the page threads uploading at once, or urllib3 throws away
        # each returning connection and the next upload re-handshakes TLS.
        max_pool_connections=config.SPACES_POOL_SIZE,
    ),
)


# Re-exported from the generated contract so call sites keep importing them
# from `storage`, where they read naturally, without this module being a second
# copy of the bucket layout.
from generated import (  # noqa: E402,F401
    original_pdf_key,
    page_image_key,
    page_text_key,
    page_thumb_key,
)


# Explicit rather than boto3's default, because config.SPACES_POOL_SIZE is
# sized from this number — a library default that changed underneath would
# undersize the connection pool without anything here moving.
_DOWNLOAD_CONFIG = TransferConfig(max_concurrency=config.SPACES_DOWNLOAD_CONCURRENCY)


def download_to_file(key: str, path: str) -> None:
    """Streams to disk — the whole PDF is never held in memory.

    Parallel across parts, not a single stream: the transfer manager pulls
    several 8 MB ranges at once, which is why one download can occupy
    SPACES_DOWNLOAD_CONCURRENCY connections on its own.
    """
    _s3.download_file(config.SPACES_BUCKET, key, path, Config=_DOWNLOAD_CONFIG)


def delete_key(key: str) -> None:
    """Remove one object. Used by benchmarks/extract_throughput.py --upload to
    clean up after itself; the pipeline never deletes individual objects (the
    API removes whole prefixes when a document or project goes)."""
    _s3.delete_object(Bucket=config.SPACES_BUCKET, Key=key)


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    extra = {"ACL": config.SPACES_ACL} if config.SPACES_ACL else {}
    _s3.put_object(
        Bucket=config.SPACES_BUCKET, Key=key, Body=data, ContentType=content_type, **extra
    )
