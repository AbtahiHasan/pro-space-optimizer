"""Tests for background workers and thread lifecycle.

These cover the regression where a worker's completion signal was handled on
the worker thread and ``cleanup()`` then tried to ``wait()`` on its own
thread, aborting the process.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pro_space_optimizer.deletion import DeletionManager  # noqa: E402
from pro_space_optimizer.models import ScanResult  # noqa: E402
from pro_space_optimizer.workers import (  # noqa: E402
    DeleteWorker,
    ScanWorker,
    SizeWorker,
    ThreadController,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def run_until(predicate, timeout_ms=8000):
    """Spin the event loop until *predicate* is true or the timeout elapses."""
    loop = QEventLoop()
    state = {"done": False}

    def check():
        if predicate():
            state["done"] = True
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(25)
    QTimer.singleShot(timeout_ms, loop.quit)
    check()
    loop.exec()
    timer.stop()
    return state["done"]


class TestThreadController:
    def test_cleanup_from_gui_thread_is_safe(self, qapp):
        controller = ThreadController()
        worker = SizeWorker([])
        done = []
        worker.finished.connect(lambda: done.append(True))
        controller.start(worker, done_signals=("finished", "failed"))
        assert run_until(lambda: bool(done))
        # cleanup() must not deadlock or wait on itself here.
        controller.cleanup()
        assert controller.worker is None

    def test_worker_signal_handler_runs_after_completion(self, qapp, tmp_path):
        """A handler calling cleanup() must not be invoked on the worker
        thread before the thread has stopped."""
        d = tmp_path / "dir"
        d.mkdir()
        (d / "f").write_bytes(b"x" * 10)

        controller = ThreadController()
        worker = SizeWorker([ScanResult(name="dir", parent=str(tmp_path),
                                        path=str(d))])
        results = []
        worker.size_ready.connect(lambda path, size, state: results.append(size))
        controller.start(worker, done_signals=("finished", "failed"))
        assert run_until(lambda: bool(results))
        assert results == [10]
        controller.cleanup()

    def test_stop_and_wait_cancels(self, qapp):
        controller = ThreadController()
        worker = SizeWorker([])
        controller.start(worker, done_signals=("finished",))
        controller.stop_and_wait(timeout_ms=3000)
        controller.cleanup()


class TestScanWorker:
    def test_emits_matches(self, qapp, tmp_path):
        p = tmp_path / "projects"
        (p / "node_modules").mkdir(parents=True)
        controller = ThreadController()
        worker = ScanWorker([str(p)], ["node_modules"])
        found = []
        reports = []
        worker.match_found.connect(found.append)
        worker.finished.connect(reports.append)
        controller.start(worker, done_signals=("finished", "failed"))
        assert run_until(lambda: bool(reports))
        assert len(found) == 1
        assert found[0].name == "node_modules"
        controller.cleanup()


class TestDeleteWorker:
    def test_delete_worker_completes_without_crash(self, qapp, tmp_path):
        p = tmp_path / "projects"
        p.mkdir()
        match = p / "node_modules"
        match.mkdir()
        (match / "f").write_text("x")
        st = os.lstat(match)
        result = ScanResult(
            name="node_modules", parent=str(p), path=str(match),
            st_dev=st.st_dev, st_ino=st.st_ino,
        )
        manager = DeletionManager([str(p)], ["node_modules"])
        controller = ThreadController()
        worker = DeleteWorker(manager, [result], "permanent")
        outcomes = []
        worker.finished.connect(outcomes.append)
        controller.start(worker, done_signals=("finished", "failed"))
        assert run_until(lambda: bool(outcomes))
        # The worker thread must have stopped before we tear down.
        assert outcomes[0][0].ok
        assert not match.exists()
        controller.cleanup()
