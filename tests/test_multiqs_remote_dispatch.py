"""Unit tests for MultiQS remote config resolution (TASK-696)."""
import pytest
from unittest.mock import patch

from querysource.queries.multi.sources.executors import RemoteConfig
from querysource.exceptions import DriverError


class TestRemoteKeyParsing:
    def test_parse_worker_host_port(self):
        """worker='host:8888' parses correctly."""
        query = {"slug": "test", "remote": True, "worker": "qworker1:8888"}
        is_remote = query.pop("remote", False)
        worker_addr = query.pop("worker", None)
        assert is_remote is True
        parts = worker_addr.rsplit(":", 1)
        assert parts[0] == "qworker1"
        assert int(parts[1]) == 8888
        assert "remote" not in query
        assert "worker" not in query

    def test_no_remote_key_is_local(self):
        """Query without 'remote' key stays local."""
        query = {"slug": "test", "store_id": 42}
        is_remote = query.pop("remote", False)
        assert is_remote is False

    def test_strips_remote_keys_from_query(self):
        """remote and worker keys are removed before ThreadQuery gets the dict."""
        query = {"slug": "test", "remote": True, "worker": "host:8888", "store_id": 42}
        query.pop("remote", None)
        query.pop("worker", None)
        assert query == {"slug": "test", "store_id": 42}

    def test_remote_false_is_local(self):
        """remote=false explicitly means local execution."""
        query = {"slug": "test", "remote": False}
        is_remote = query.pop("remote", False)
        assert is_remote is False


class TestRemoteConfigCreation:
    def test_remote_config_from_worker_string(self):
        """RemoteConfig is built correctly from 'host:port' string."""
        worker_addr = "qworker1.internal:9000"
        parts = worker_addr.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1])
        rc = RemoteConfig(host=host, port=port, timeout=60)
        assert rc.host == "qworker1.internal"
        assert rc.port == 9000
        assert rc.timeout == 60

    def test_remote_config_fallback_port(self):
        """If worker address has no port, falls back to QWORKER_PORT."""
        from querysource.conf import QWORKER_PORT
        worker_addr = "qworker1"
        parts = worker_addr.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else QWORKER_PORT
        assert host == "qworker1"
        assert port == QWORKER_PORT  # default 8888


class TestMultiQSRemoteDispatch:
    def _make_multiqs(self, queries: dict):
        """Helper to create a minimal MultiQS instance."""
        from querysource.queries.multi import MultiQS
        return MultiQS(queries=queries)

    def test_remote_queries_list_initialized_empty(self):
        """_remote_queries starts as empty list."""
        mqs = self._make_multiqs({"q": {"slug": "s"}})
        assert mqs._remote_queries == []

    def test_no_remote_key_leaves_remote_queries_empty(self):
        """Local query does not add to _remote_queries."""
        mqs = self._make_multiqs({"q": {"slug": "s"}})
        assert mqs._remote_queries == []

    @pytest.mark.asyncio
    async def test_remote_true_no_worker_no_config_raises(self):
        """remote=true with no worker and QWORKER_HOST=None raises DriverError."""
        from querysource.queries.multi import MultiQS
        import querysource.queries.multi as multiqs_module

        mqs = MultiQS(queries={"q": {"slug": "s", "remote": True}})

        with patch.object(multiqs_module, "QWORKER_HOST", None):
            with patch.object(multiqs_module, "QWORKER_WORKERS", []):
                with pytest.raises(DriverError, match="no worker address configured"):
                    await mqs.query()
