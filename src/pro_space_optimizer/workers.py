"""Background workers.

All filesystem scanning, size calculation and deletion run off the GUI thread.
Workers are ``QObject`` subclasses living on a ``QThread`` and communicate
exclusively through Qt signals, so widgets are never touched from a worker
thread.

Cancellation is cooperative: the UI sets a flag (``cancel()``) and the worker
checks it at safe points.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .deletion import DeletionManager, DeleteOutcome
from .models import ScanResult, SizeState
from .scanner import PolicyOptions, Scanner
from .size_calculator import calculate_size

log = logging.getLogger(__name__)


class _Cancellable:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        self._event.clear()


class ScanWorker(QObject, _Cancellable):
    """Recursively scans parent folders for matches (with pruning)."""

    progress = Signal(int, int, str)          # processed, total, current
    match_found = Signal(object)              # ScanResult
    parent_error = Signal(str, str)           # parent, error
    finished = Signal(object)                 # ScanReport
    failed = Signal(str)

    def __init__(self, parents: Sequence[str], names: Sequence[str],
                 options: Optional[PolicyOptions] = None):
        QObject.__init__(self)
        _Cancellable.__init__(self)
        self._parents = list(parents)
        self._names = list(names)
        self._options = options or PolicyOptions()

    @Slot()
    def run(self) -> None:
        try:
            scanner = Scanner(follow_symlinks=False, options=self._options)
            report = scanner.scan(
                self._parents,
                self._names,
                should_stop=self.is_cancelled,
                progress=self._emit_progress,
                match_found=self.match_found.emit,
            )
            for pr in report.parents:
                if pr.error:
                    self.parent_error.emit(pr.parent, pr.error)
            self.finished.emit(report)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Scan worker crashed")
            self.failed.emit(str(exc))

    def _emit_progress(self, processed: int, total: int, current: str) -> None:
        self.progress.emit(processed, total, current)


class SizeWorker(QObject, _Cancellable):
    """Calculates sizes for a batch of results, one at a time."""

    size_ready = Signal(str, object, object)  # path, size|None, SizeState
    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, results: Sequence[ScanResult]):
        QObject.__init__(self)
        _Cancellable.__init__(self)
        self._results = list(results)

    @Slot()
    def run(self) -> None:
        try:
            total = len(self._results)
            for index, result in enumerate(self._results):
                if self.is_cancelled():
                    break
                sr = calculate_size(result.path, should_stop=self.is_cancelled)
                size = sr.size
                state = sr.state
                if sr.state == SizeState.UNKNOWN and result.error:
                    state = SizeState.UNKNOWN
                self.size_ready.emit(result.path, size, state)
                self.progress.emit(index + 1, total)
            self.finished.emit()
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Size worker crashed")
            self.failed.emit(str(exc))


class DeleteWorker(QObject, _Cancellable):
    """Deletes a batch of results using a :class:`DeletionManager`."""

    progress = Signal(int, int, str)          # done, total, path
    one_done = Signal(object)                 # DeleteOutcome
    finished = Signal(object)                 # List[DeleteOutcome]
    failed = Signal(str)

    def __init__(self, manager: DeletionManager, results: Sequence[ScanResult],
                 mode: str):
        QObject.__init__(self)
        _Cancellable.__init__(self)
        self._manager = manager
        self._results = list(results)
        self._mode = mode

    @Slot()
    def run(self) -> None:
        try:
            outcomes: List[DeleteOutcome] = []
            total = len(self._results)
            for index, result in enumerate(self._results):
                if self.is_cancelled():
                    break
                outcome = self._manager.delete(result, mode=self._mode)
                outcomes.append(outcome)
                self.one_done.emit(outcome)
                self.progress.emit(index + 1, total, outcome.path)
            self.finished.emit(outcomes)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Delete worker crashed")
            self.failed.emit(str(exc))


class ThreadController:
    """Runs a worker on a fresh ``QThread`` and cleans up afterwards.

    Cleanup is event-loop friendly: the worker's completion signal is wired to
    ``QThread.quit`` and the blocking ``wait()`` only ever happens on the
    thread that owns the controller (the GUI thread).  This avoids the
    "QThread::wait: Thread tried to wait on itself" deadlock that a naive
    ``cleanup()`` inside a worker-thread signal slot would cause.
    """

    def __init__(self):
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def worker(self) -> Optional[QObject]:
        return self._worker

    def start(self, worker: QObject, done_signals: Sequence[str] = ()) -> QThread:
        """Move *worker* to a new thread and start it.

        ``done_signals`` lists the signal names that indicate the worker has
        finished; each is connected to ``QThread.quit`` so the thread's event
        loop stops on its own without the GUI having to wait from the worker
        thread.
        """
        if self.running:
            raise RuntimeError("A worker is already running in this controller")
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        for signal_name in done_signals:
            sig = getattr(worker, signal_name, None)
            if sig is not None:
                # AutoConnection: thread.quit() runs in the thread that owns
                # the QThread object (the GUI thread), never in the worker.
                sig.connect(thread.quit)
        self._thread = thread
        self._worker = worker
        thread.start()
        return thread

    def _in_thread(self, thread: Optional[QThread]) -> bool:
        return thread is not None and QThread.currentThread() is thread

    def stop_and_wait(self, timeout_ms: int = 10000) -> bool:
        """Ask the worker to cancel and wait for the thread to finish.

        Safe to call from the GUI thread.  If called from the worker thread
        itself it only requests cancellation and returns immediately, so it
        can never deadlock waiting on itself.
        """
        worker = self._worker
        if worker is not None and hasattr(worker, "cancel"):
            try:
                worker.cancel()  # type: ignore[attr-defined]
            except Exception:
                pass

        thread = self._thread
        if thread is None:
            return True
        if self._in_thread(thread):
            # Do not wait on ourselves; ask the thread to exit when it can.
            thread.quit()
            return False
        if thread.isRunning():
            thread.quit()
            ok = thread.wait(timeout_ms)
            if not ok:
                log.warning("Worker thread did not stop within timeout")
            return ok
        return True

    def cleanup(self) -> None:
        """Release the thread/worker references.

        This only waits when it is safe to do so (i.e. not from the worker
        thread itself).  In the normal flow the worker's completion signal has
        already stopped the thread via ``QThread.quit``.
        """
        thread = self._thread
        worker = self._worker
        self._thread = None
        self._worker = None
        if thread is None:
            return
        if self._in_thread(thread):
            # Called from inside the worker thread: never wait on ourselves.
            thread.quit()
            return
        self._teardown(thread, worker)

    @staticmethod
    def _teardown(thread: QThread, worker: Optional[QObject]) -> None:
        try:
            if thread.isRunning():
                thread.quit()
                thread.wait(5000)
        except Exception:
            pass
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
