"""S3-compatible object storage (DigitalOcean Spaces / local MinIO).

Key builders mirror packages/shared/src/index.ts `objectKeys` — keep in sync.
"""

import boto3
from botocore.config import Config

import config

_s3 = boto3.client(
    "s3",
    endpoint_url=config.SPACES_ENDPOINT,
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
    _s3.put_object(Bucket=config.SPACES_BUCKET, Key=key, Body=data, ContentType=content_type)
