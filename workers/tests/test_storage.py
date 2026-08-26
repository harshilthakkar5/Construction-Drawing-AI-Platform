"""Spaces client configuration.

The connection pool is the one setting here that fails QUIETLY: an undersized
pool is not an error, it is urllib3 discarding each returning connection and
the next upload paying a fresh TCP + TLS handshake to the region. It shows up
as a slow document, not a failed one, so it needs a test rather than a log.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import storage  # noqa: E402


class TestConnectionPool:
    def test_covers_every_page_thread_that_can_upload_at_once(self):
        """PROCESS_CONCURRENCY x PAGE_CONCURRENCY pages are in flight, and each
        uploads a PNG, a thumbnail and a text file."""
        assert config.SPACES_POOL_SIZE >= config.PROCESS_CONCURRENCY * config.PAGE_CONCURRENCY

    def test_covers_a_download_which_is_parallel_across_parts(self):
        """The bug the first version of this shipped with: `download_file` runs
        a transfer manager pulling several 8 MB ranges at once, so ONE download
        can occupy the whole pool. Counting only page threads left a worker at
        PROCESS_CONCURRENCY=2 on the floor of 10 — the exact configuration whose
        logs showed the pool exhausted."""
        assert config.SPACES_POOL_SIZE >= config.SPACES_DOWNLOAD_CONCURRENCY

    def test_covers_both_queues_that_touch_storage_at_once(self):
        """process-document and scrape-region run concurrently and both
        download; sizing for either alone undersizes the pool."""
        worst_case = (
            config.PROCESS_CONCURRENCY
            * max(config.PAGE_CONCURRENCY, config.SPACES_DOWNLOAD_CONCURRENCY)
            + config.SCRAPE_CONCURRENCY * config.SPACES_DOWNLOAD_CONCURRENCY
        )
        assert config.SPACES_POOL_SIZE >= worst_case

    def test_never_below_botocore_s_own_default(self):
        """A small worker must not end up with a SMALLER pool than the stock
        client would have given it."""
        assert config.SPACES_POOL_SIZE >= 10

    def test_the_download_concurrency_is_ours_not_the_library_s(self):
        """The pool is sized FROM this number, so it cannot be left to a
        default that could move underneath us."""
        from boto3.s3.transfer import TransferConfig

        assert storage._DOWNLOAD_CONFIG.max_concurrency == config.SPACES_DOWNLOAD_CONCURRENCY
        # Guard the assumption the sizing rests on, rather than trusting memory.
        assert TransferConfig().max_concurrency == 10

    def test_the_client_actually_uses_it(self):
        """The value is only worth computing if it reaches boto3 — this is the
        wiring the log warning proved was missing."""
        assert storage._s3.meta.config.max_pool_connections == config.SPACES_POOL_SIZE

    def test_path_addressing_survived_the_config_change(self):
        """Spaces needs path addressing; adding the pool size must not drop it."""
        assert storage._s3.meta.config.s3["addressing_style"] == "path"
