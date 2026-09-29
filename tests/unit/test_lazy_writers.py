"""FEAT-154 — lazy-import output writers: import weight, registry contract, off-loop PDF."""
import asyncio
import subprocess
import sys
import threading
import time

import pytest

import querysource.outputs.output as output_module
import querysource.outputs.writers as writers_pkg
from querysource.outputs.output import WRITERS, LazyWriterRegistry, resolve_writer
from querysource.outputs.writers import pdf as pdf_module
from querysource.outputs.writers.abstract import AbstractWriter

ENTRYPOINTS = [
    "querysource.queries.qs",
    "querysource.outputs.dt",
    "querysource.outputs.output",
    "querysource.services",
]


@pytest.fixture
def fresh_writers():
    """Restore the complete WRITERS mapping (specs + cached classes) after the test (codex S9)."""
    snapshot = dict(dict.items(output_module.WRITERS))
    yield output_module.WRITERS
    dict.clear(output_module.WRITERS)
    dict.update(output_module.WRITERS, snapshot)


def _run(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its last stdout line."""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


def _imports_module(target: str, probe: str) -> bool:
    """Import ``target`` in a fresh interpreter and report whether ``probe`` got loaded."""
    return _run(f"import sys, {target}; print({probe!r} in sys.modules)") == "True"


class _Req:
    headers: dict = {}
    app: dict = {"templating": object()}


class _Response:
    """Minimal response object needed by ``PDFWriter.get_response``."""

    def __init__(self) -> None:
        self.headers: dict = {}
        self.content_length = None


def _pdf_writer(sink: list) -> "pdf_module.PDFWriter":
    """Build a PDFWriter with render/response/stream stubbed; ``sink`` receives the streamed bytes."""
    writer = pdf_module.PDFWriter(request=_Req(), resultset=[], filename="r", ctype="pdf")

    async def fake_render_content() -> str:
        return "<p>x</p>"

    async def fake_response(_response_type: str) -> _Response:
        return _Response()

    async def fake_stream_response(_response: _Response, data: bytes) -> bytes:
        sink.append(data)
        return data

    writer.render_content = fake_render_content
    writer.response = fake_response
    writer.stream_response = fake_stream_response
    return writer


def test_render_pdf_imports_weasyprint_lazily():
    assert not _imports_module("querysource.outputs.writers.pdf", "weasyprint")


async def test_pdf_render_runs_off_loop(monkeypatch):
    sink = []
    threads = []
    ticks = 0
    finished = asyncio.Event()

    def fake_render_pdf(_html: str) -> bytes:
        threads.append(threading.current_thread().name)
        time.sleep(0.3)
        return b"%PDF-fake"

    async def ticker() -> None:
        nonlocal ticks
        while not finished.is_set():
            await asyncio.sleep(0.01)
            ticks += 1

    monkeypatch.setattr(pdf_module, "_render_pdf", fake_render_pdf)
    ticker_task = asyncio.create_task(ticker())
    try:
        await _pdf_writer(sink).get_response()
    finally:
        finished.set()
        await ticker_task

    assert ticks >= 10
    assert threads[0].startswith("qs-pdf")
    assert sink == [b"%PDF-fake"]


async def test_pdf_render_error_propagates(monkeypatch):
    sink = []

    def broken_render_pdf(_html: str) -> bytes:
        raise ValueError("boom")

    def working_render_pdf(_html: str) -> bytes:
        return b"%PDF-fake"

    monkeypatch.setattr(pdf_module, "_render_pdf", broken_render_pdf)
    with pytest.raises(ValueError, match="boom"):
        await _pdf_writer(sink).get_response()

    monkeypatch.setattr(pdf_module, "_render_pdf", working_render_pdf)
    assert await _pdf_writer(sink).get_response() == b"%PDF-fake"
    assert sink == [b"%PDF-fake"]


def test_pdf_executor_bounded():
    from querysource.conf import PDF_RENDER_WORKERS

    ex = pdf_module._pdf_executor()
    assert ex is pdf_module._pdf_executor()
    assert ex._max_workers == PDF_RENDER_WORKERS


def test_pdf_render_workers_config_default():
    from querysource.conf import PDF_RENDER_WORKERS

    assert isinstance(PDF_RENDER_WORKERS, int)
    assert _run(
        "import os; os.environ.pop('PDF_RENDER_WORKERS', None); "
        "from querysource.conf import PDF_RENDER_WORKERS; print(PDF_RENDER_WORKERS)"
    ) == "4"


def test_writers_package_lazy_attr():
    assert _run(
        "import sys; from querysource.outputs.writers import CSVWriter; "
        "print('querysource.outputs.writers.pdf' in sys.modules)"
    ) == "False"


def test_writers_package_unknown_attr():
    with pytest.raises(AttributeError):
        writers_pkg.Nope  # noqa: B018


def test_writers_package_dir():
    assert set(dir(writers_pkg)) >= set(writers_pkg.__all__)


def test_writers_package_star_import():
    assert _run(
        "from querysource.outputs.writers import *; import querysource.outputs.writers as writers; "
        "print(all(name in globals() for name in writers.__all__))"
    ) == "True"


def test_writers_package_identity():
    assert writers_pkg.PDFWriter is WRITERS["pdf"]


def test_every_registered_writer_resolves():
    assert len(WRITERS) == 18
    for key in list(WRITERS):
        assert issubclass(WRITERS[key], AbstractWriter), key
        assert resolve_writer(key) is WRITERS[key]


def test_registry_aliases_share_class():
    assert WRITERS["xls"] is WRITERS["xlsx"] is WRITERS["xlsm"] is WRITERS["ods"] is WRITERS["excel"]
    assert WRITERS["plain"] is WRITERS["txt"]


def test_registry_starts_as_specs():
    assert _run(
        "import sys; import querysource.outputs.output as output; "
        "print(all(isinstance(dict.__getitem__(output.WRITERS, key), str) for key in output.WRITERS) "
        "and 'querysource.outputs.writers.pdf' not in sys.modules)"
    ) == "True"


def test_resolve_writer_caches_class(fresh_writers):
    dict.__setitem__(fresh_writers, "csv", "csv:CSVWriter")
    cls = fresh_writers["csv"]
    assert dict.__getitem__(fresh_writers, "csv") is cls


def test_registry_get_resolves():
    assert issubclass(WRITERS.get("csv"), AbstractWriter)
    assert WRITERS.get("nope") is None
    assert isinstance(WRITERS, LazyWriterRegistry) and isinstance(WRITERS, dict)


def test_resolve_writer_class_passthrough(monkeypatch):
    class Stub(AbstractWriter):
        """Writer class used to verify an injected registry override."""

    monkeypatch.setitem(WRITERS, "json", Stub)
    assert resolve_writer("json") is Stub


def test_resolve_writer_unknown_falls_back_to_json(caplog):
    with caplog.at_level("WARNING", logger="QS.Output"):
        assert resolve_writer("nope") is WRITERS["json"]
    assert "Invalid Writer nope, default to JSON." in caplog.messages


def test_resolve_writer_import_error_propagates(fresh_writers):
    dict.__setitem__(fresh_writers, "bad_module", "does_not_exist:Nope")
    dict.__setitem__(fresh_writers, "bad_class", "json:NoSuchClass")
    with pytest.raises(ImportError):
        resolve_writer("bad_module")
    with pytest.raises(ImportError):
        resolve_writer("bad_class")


def test_override_wins_after_cache(monkeypatch):
    class Stub(AbstractWriter):
        """Writer class used to verify an override after cache write-back."""

    WRITERS["json"]
    monkeypatch.setitem(WRITERS, "json", Stub)
    assert resolve_writer("json") is Stub


def test_outputs_package_lazy_dataoutput():
    assert _run(
        "import sys, querysource.outputs.dt; unloaded = 'querysource.outputs.output' not in sys.modules; "
        "from querysource.outputs import DataOutput; print(unloaded and DataOutput.__name__ == 'DataOutput')"
    ) == "True"


@pytest.mark.parametrize("target", ENTRYPOINTS)
def test_entrypoints_do_not_import_weasyprint(target):
    assert not _imports_module(target, "weasyprint")


def test_http_startup_loads_no_writer_submodules():
    assert _run(
        "import sys, querysource.services; "
        "print(all(name == 'querysource.outputs.writers.abstract' "
        "for name in sys.modules if name.startswith('querysource.outputs.writers.')))"
    ) == "True"
