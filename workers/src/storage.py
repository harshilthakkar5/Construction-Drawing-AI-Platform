"""S3-compatible object storage (DigitalOcean Spaces / local MinIO).

Key builders mirror packages/shared/src/index.ts `objectKeys` — keep in sync.
"""

from urllib.parse import urlparse, urlunparse

import boto3
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
    config=Config(s3={"addressing_style": "path"}),
)


def original_pdf_key(project_id: str, document_id: str) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/original.pdf"


def page_image_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/pages/{page}.png"


def page_thumb_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/thumbs/{page}.jpg"


def page_text_key(project_id: str, document_id: str, page: int) -> str:
    return f"projects/{project_id}/pdfs/{document_id}/text/{page}.txt"


def download_to_file(key: str, path: str) -> None:
    """Streams to disk — the whole PDF is never held in memory."""
    _s3.download_file(config.SPACES_BUCKET, key, path)


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    extra = {"ACL": config.SPACES_ACL} if config.SPACES_ACL else {}
    _s3.put_object(
        Bucket=config.SPACES_BUCKET, Key=key, Body=data, ContentType=content_type, **extra
    )
