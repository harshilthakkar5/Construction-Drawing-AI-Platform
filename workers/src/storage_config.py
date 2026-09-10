"""Which object store this deployment reads from.

``STORAGE_BACKEND=local|spaces`` is ONE word that swaps the whole storage
layer: ``spaces`` is DigitalOcean Spaces, ``local`` is the MinIO container on
the same machine, writing to a folder on that machine's disk. Both sets of
credentials live in the env file at the same time — that is what makes it a
switch rather than a rewrite.

MIRRORED IN TYPESCRIPT: apps/api/src/storageConfig.ts. The API writes the
uploaded PDF and this worker reads it back, so the two must resolve to the
same bucket on the same host — a drift is not a degraded system, it is a
worker that cannot find any file it is asked to process. Both test suites read
packages/shared/fixtures/storage-backend.json, so changing the rule in one
language fails the other's tests.

The endpoint SHAPE (the bucket-in-the-hostname correction) is not resolved
here — that is ``storage.normalize_endpoint``, applied to what this returns.
"""

from dataclasses import dataclass
from typing import Literal, Mapping, Optional

Backend = Literal["local", "spaces"]

# Chosen so STORAGE_BACKEND=local needs no other variable at all. The endpoint
# is localhost, not `minio`, because that is the value that works for a worker
# started outside Docker (`python src/worker.py`); the compose files override
# it with the container name.
_LOCAL_DEFAULTS = {
    "endpoint": "http://localhost:9000",
    "bucket": "cdip-local",
    "region": "us-east-1",
    "key": "minioadmin",
    "secret": "minioadmin",
}

_SPACES_DEFAULT_REGION = "us-east-1"


@dataclass(frozen=True)
class StorageConfig:
    backend: Backend
    #: Endpoint the WORKER talks to.
    endpoint: str
    #: Endpoint a browser is sent to. Resolved for parity with the API (which
    #: signs URLs for browsers); the worker itself never uses it.
    public_endpoint: str
    bucket: str
    region: str
    key: str
    secret: str


def _read(env: Mapping[str, str], name: str) -> Optional[str]:
    """Trimmed, with the empty string treated as absent — a hand-edited .env
    carries ``FOO=`` far more often than an intentional empty value."""
    raw = env.get(name)
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


def _require(env: Mapping[str, str], name: str, backend: str) -> str:
    value = _read(env, name)
    if value is None:
        raise ValueError(
            f"{name} is required when STORAGE_BACKEND={backend}. Set it in .env, "
            f"or switch to STORAGE_BACKEND=local to use the MinIO container on "
            f"this machine instead."
        )
    return value


#: The four values a Spaces deployment cannot work without. Region and ACL are
#: deliberately excluded: they are modifiers with defaults, and a stray
#: SPACES_REGION left in a file should not be read as "this is a Spaces
#: deployment" when nothing else about it is.
_SPACES_REQUIRED = ("SPACES_ENDPOINT", "SPACES_BUCKET", "SPACES_KEY", "SPACES_SECRET")


def _default_backend(env: Mapping[str, str]) -> str:
    """An unset STORAGE_BACKEND means whichever set you actually filled in.

    That keeps every .env written before this switch existed on the backend it
    is already using, and leaves a bare checkout runnable against the MinIO
    container with no configuration at all — which is how this worker has
    always started. It is not a silent fallback either way: three of the four
    Spaces variables still resolves to ``spaces`` and then fails naming the
    fourth, rather than quietly writing production drawings to a local disk.
    """
    return "spaces" if any(_read(env, name) for name in _SPACES_REQUIRED) else "local"


def resolve(env: Mapping[str, str]) -> StorageConfig:
    raw = (_read(env, "STORAGE_BACKEND") or _default_backend(env)).lower()
    if raw not in ("local", "spaces"):
        raise ValueError(f'STORAGE_BACKEND must be "local" or "spaces", got "{raw}".')

    if raw == "local":
        endpoint = _read(env, "LOCAL_S3_ENDPOINT") or _LOCAL_DEFAULTS["endpoint"]
        return StorageConfig(
            backend="local",
            endpoint=endpoint,
            public_endpoint=_read(env, "LOCAL_S3_PUBLIC_ENDPOINT") or endpoint,
            bucket=_read(env, "LOCAL_S3_BUCKET") or _LOCAL_DEFAULTS["bucket"],
            region=_read(env, "LOCAL_S3_REGION") or _LOCAL_DEFAULTS["region"],
            key=_read(env, "LOCAL_S3_KEY") or _LOCAL_DEFAULTS["key"],
            secret=_read(env, "LOCAL_S3_SECRET") or _LOCAL_DEFAULTS["secret"],
        )

    endpoint = _require(env, "SPACES_ENDPOINT", raw)
    return StorageConfig(
        backend="spaces",
        endpoint=endpoint,
        public_endpoint=_read(env, "SPACES_PUBLIC_ENDPOINT") or endpoint,
        bucket=_require(env, "SPACES_BUCKET", raw),
        region=_read(env, "SPACES_REGION") or _SPACES_DEFAULT_REGION,
        key=_require(env, "SPACES_KEY", raw),
        secret=_require(env, "SPACES_SECRET", raw),
    )
