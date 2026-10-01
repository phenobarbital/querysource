"""Tests for Microsoft Graph source primitives."""
import platform
import threading
from datetime import datetime, timedelta, timezone

import pytest

from querysource.queries.multi.sources.graph import StaticTokenCredential, kiota_platform_version_patch


def test_kiota_patch_restores_and_nests() -> None:
    """The patch remains installed through nested context managers."""
    original = platform.version
    with kiota_platform_version_patch():
        with kiota_platform_version_patch():
            assert platform.version() == original().strip()
        assert platform.version is not original
    assert platform.version is original


def test_kiota_patch_restores_on_exception() -> None:
    """The patch is restored when a context body raises."""
    original = platform.version
    with pytest.raises(RuntimeError):
        with kiota_platform_version_patch():
            raise RuntimeError("boom")
    assert platform.version is original


def test_kiota_patch_concurrent_threads() -> None:
    """Concurrent users retain the patch until every context exits."""
    original = platform.version
    barrier = threading.Barrier(8)

    def worker() -> None:
        with kiota_platform_version_patch():
            barrier.wait()
            assert platform.version() == original().strip()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert platform.version is original


async def test_static_token_credential_get_token() -> None:
    """A known expiry is returned as an epoch timestamp."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    cred = StaticTokenCredential("tok", exp)
    tok = await cred.get_token("https://graph.microsoft.com/.default")
    assert tok.token == "tok" and tok.expires_on == int(exp.timestamp())


async def test_static_token_credential_unknown_expiry_and_close() -> None:
    """An unknown expiry gets the short fallback and close does nothing."""
    cred = StaticTokenCredential("tok", None)
    before = int(datetime.now(timezone.utc).timestamp()) + 300
    tok = await cred.get_token("https://graph.microsoft.com/.default")
    after = int(datetime.now(timezone.utc).timestamp()) + 300

    assert before - 5 <= tok.expires_on <= after + 5
    assert await cred.close() is None
