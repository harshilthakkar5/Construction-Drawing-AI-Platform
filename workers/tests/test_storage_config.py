"""STORAGE_BACKEND resolution, against the SAME fixture the API's test reads.

The API writes the uploaded PDF and this worker reads it back. If the two
sides resolve the switch differently — a different bucket, a different host —
the worker does not degrade, it fails to find every file it is handed. Neither
suite proves anything on its own, so both read
packages/shared/fixtures/storage-backend.json and a change to the rule in one
language fails the other's tests.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import storage_config  # noqa: E402

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2] / "packages/shared/fixtures/storage-backend.json"
    ).read_text()
)

# The fixture is written in the API's camelCase; the worker's dataclass is
# snake_case. Mapping the one field that differs is cheaper than making either
# side's naming foreign to its own language.
_FIELD_NAMES = {"public_endpoint": "publicEndpoint"}


def _as_fixture_shape(cfg: storage_config.StorageConfig) -> dict:
    return {_FIELD_NAMES.get(k, k): v for k, v in asdict(cfg).items()}


def test_fixture_covers_both_backends():
    assert FIXTURE["resolve"], "fixture has no resolution cases"
    assert FIXTURE["errors"], "fixture has no error cases"
    backends = {c["expect"]["backend"] for c in FIXTURE["resolve"]}
    assert backends == {"local", "spaces"}


@pytest.mark.parametrize("case", FIXTURE["resolve"], ids=lambda c: c["name"])
def test_resolve(case):
    assert _as_fixture_shape(storage_config.resolve(case["env"])) == case["expect"]


@pytest.mark.parametrize("case", FIXTURE["errors"], ids=lambda c: c["name"])
def test_rejects(case):
    with pytest.raises(ValueError) as excinfo:
        storage_config.resolve(case["env"])
    for needle in case["messageContains"]:
        assert needle in str(excinfo.value)


def test_resolve_reads_only_the_mapping_it_is_handed(monkeypatch):
    # Pure, so a test can ask "what would this .env produce?" without the
    # answer depending on how the test runner was started.
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    cfg = storage_config.resolve(
        {
            "SPACES_ENDPOINT": "https://blr1.digitaloceanspaces.com",
            "SPACES_BUCKET": "b",
            "SPACES_KEY": "k",
            "SPACES_SECRET": "s",
        }
    )
    assert cfg.backend == "spaces"


def test_public_endpoint_stays_with_its_own_backend():
    # A leftover SPACES_PUBLIC_ENDPOINT must not follow the switch to local, or
    # presigned URLs point at DigitalOcean for bytes on this machine's disk.
    cfg = storage_config.resolve(
        {
            "STORAGE_BACKEND": "local",
            "LOCAL_S3_ENDPOINT": "http://minio:9000",
            "SPACES_PUBLIC_ENDPOINT": "https://cdn.example.com",
        }
    )
    assert cfg.public_endpoint == "http://minio:9000"
