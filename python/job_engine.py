# -*- coding: utf-8 -*-
"""Durable, bounded job execution for Product Atelier.

The SQLite :class:`AtelierLedger` is the source of truth.  This module only
coordinates bounded worker threads, fair claims and cooperative cancellation;
it deliberately keeps no second queue or authoritative progress store.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Optional, Union

try:
    from atelier_ledger import AtelierLedger, InvalidStatusTransitionError
except ImportError:  # Allows importing as python.job_engine during local tests.
    from python.atelier_ledger import AtelierLedger, InvalidStatusTransitionError


LOGGER = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = frozenset({"completed", "partial", "failed", "canceled"})
ACTIVE_ITEM_STATUSES = frozenset({"running", "canceling"})
DEFAULT_RESOURCE_LIMITS = MappingProxyType({
    "vlm": 1,
    "cloud-image": 2,
    "local-cutout": 1,
})


class JobCanceled(RuntimeError):
    """Raised at a safe checkpoint after durable cancellation is requested."""


class StaleExecution(RuntimeError):
    """Raised when a worker no longer owns the durable running item."""


class UnknownResourceError(KeyError):
    """Raised when a processor requests an unconfigured resource gate."""


class _ProcessLeaderLock:
    """Cross-process exclusive lock released automatically when a process dies.

    SQLite protects individual claims, but startup recovery must only run after
    the previous scheduler process is gone.  Keeping this advisory file lock
    for the full leader lifetime gives us that ownership boundary without a
    second database or a stale wall-clock lease.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        self._handle: Any | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class JobExecutionError(RuntimeError):
    """A processor failure with a stable machine-readable error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code).strip() or "PROCESSOR_ERROR"
        self.metadata = dict(metadata or {})


CommitCallback = Callable[[], Optional[Mapping[str, Any]]]
CleanupCallback = Callable[[], None]


@dataclass(frozen=True)
class JobProcessorResult:
    """A staged processor result whose publication happens after a checkpoint.

    Processors that create files should write private temporary artifacts first,
    return an atomic ``commit`` callback, and make ``cleanup`` idempotent.  The
    engine checks durable cancellation before and after commit.  Plain metadata
    mappings remain valid processor return values for workflows with no staged
    artifact.
    """

    metadata: Mapping[str, Any] = field(default_factory=dict)
    commit: CommitCallback | None = None
    cleanup: CleanupCallback | None = None
    durable_completion: bool = False


ProcessorResult = Union[Mapping[str, Any], JobProcessorResult, None]
JobProcessor = Callable[["ExecutionContext"], ProcessorResult]


@dataclass(frozen=True)
class _RunState:
    job_id: str
    item_id: str
    engine_key: str
    claim: Mapping[str, Any]
    cancel_event: threading.Event
    admission_resource: str | None = None


class ExecutionContext:
    """Processor-facing view of one atomically claimed job item."""

    def __init__(self, engine: "JobEngine", state: _RunState) -> None:
        self._engine = engine
        self._state = state
        self._metadata: dict[str, Any] = {}

    @property
    def job(self) -> Mapping[str, Any]:
        return self._state.claim["job"]

    @property
    def item(self) -> Mapping[str, Any]:
        return self._state.claim["item"]

    @property
    def attempt_id(self) -> str:
        return str(self._state.claim["attempt_id"])

    @property
    def is_cancel_requested(self) -> bool:
        if self._state.cancel_event.is_set():
            return True
        try:
            return self._engine.ledger.get_job_item(self.item_id)["status"] in {
                "canceling", "canceled"
            }
        except KeyError:
            return True

    @property
    def job_id(self) -> str:
        return self._state.job_id

    @property
    def item_id(self) -> str:
        return self._state.item_id

    @property
    def metadata(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self._metadata))

    def checkpoint(self) -> None:
        """Stop at a safe boundary if cancellation or ownership changed."""
        latest = self._engine.ledger.get_job_item(self.item_id)
        status = str(latest["status"])
        if self._state.cancel_event.is_set() or status in {"canceling", "canceled"}:
            self._state.cancel_event.set()
            raise JobCanceled(f"job item canceled: {self.item_id}")
        if status != "running":
            raise StaleExecution(
                f"job item is no longer owned by this worker: {self.item_id} ({status})"
            )

    def progress(
        self,
        value: float,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist progress (and optional recoverable attempt metadata)."""
        self.checkpoint()
        self._engine.ledger.update_job_item_progress(self.item_id, value)
        if metadata:
            self.record_metadata(metadata)
        self._engine._notify_state_change()

    def record_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Merge and persist metadata without making it authoritative in memory."""
        self.checkpoint()
        self._metadata.update(dict(metadata))
        self._engine.ledger.update_task_attempt_metadata(
            self.item_id, dict(self._metadata)
        )
        self._engine._notify_state_change()

    @contextmanager
    def resource(self, name: str) -> Iterator[None]:
        """Acquire a configured named resource with cooperative cancellation."""
        self.checkpoint()
        self._engine._acquire_resource(str(name), self)
        try:
            self.checkpoint()
            yield
            self.checkpoint()
        finally:
            self._engine._release_resource(str(name))


class JobEngine:
    """Explicit-lifecycle scheduler backed by durable SQLite job state."""

    def __init__(
        self,
        ledger: AtelierLedger,
        processors: Mapping[str, JobProcessor] | None = None,
        *,
        max_workers: int = 4,
        resource_limits: Mapping[str, int] | None = None,
        processor_admission_resources: Mapping[str, str] | None = None,
        poll_interval: float = 0.25,
        thread_name: str = "atelier-job-scheduler",
    ) -> None:
        if int(max_workers) < 1:
            raise ValueError("max_workers must be at least 1")
        if float(poll_interval) <= 0:
            raise ValueError("poll_interval must be positive")
        limits = dict(DEFAULT_RESOURCE_LIMITS if resource_limits is None else resource_limits)
        if not limits:
            raise ValueError("at least one named resource limit is required")
        for name, limit in limits.items():
            if not str(name).strip():
                raise ValueError("resource names must not be empty")
            if int(limit) < 1:
                raise ValueError(f"resource limit must be positive: {name}")

        self.ledger = ledger
        self.max_workers = int(max_workers)
        self.poll_interval = float(poll_interval)
        self.thread_name = str(thread_name)
        self._processors: dict[str, JobProcessor] = dict(processors or {})
        self._resource_limits = {str(name): int(limit) for name, limit in limits.items()}
        self._processor_admission_resources = {
            str(engine_key): str(resource_name)
            for engine_key, resource_name in (processor_admission_resources or {}).items()
        }
        unknown_admission_resources = set(self._processor_admission_resources.values()) - set(
            self._resource_limits
        )
        if unknown_admission_resources:
            names = ", ".join(sorted(unknown_admission_resources))
            raise ValueError(f"processor admission uses unconfigured resources: {names}")

        self._condition = threading.Condition(threading.RLock())
        self._executor: ThreadPoolExecutor | None = None
        self._coordinator: threading.Thread | None = None
        self._started = False
        self._stopping = False
        self._is_leader = False
        self._leader_lock = _ProcessLeaderLock(
            self.ledger.db_path.with_name(f"{self.ledger.db_path.name}.job-engine.lock")
        )
        self._last_job_id: str | None = None
        self._futures: dict[Future[None], _RunState] = {}
        self._states_by_item: dict[str, _RunState] = {}
        self._job_inflight: Counter[str] = Counter()
        self._resource_in_use: Counter[str] = Counter()
        self._resource_peak: Counter[str] = Counter()
        self._admission_in_use: Counter[str] = Counter()
        self._admission_peak: Counter[str] = Counter()
        self._unreconciled_workers: dict[str, _RunState] = {}
        self.recovery_result = {"interrupted": 0, "requeued": 0, "failed": 0}

    @property
    def is_running(self) -> bool:
        with self._condition:
            return self._started and not self._stopping

    @property
    def coordinator_is_daemon(self) -> bool | None:
        with self._condition:
            return self._coordinator.daemon if self._coordinator is not None else None

    @property
    def is_leader(self) -> bool:
        with self._condition:
            return self._is_leader

    def register_processor(
        self,
        engine_key: str,
        processor: JobProcessor,
        *,
        replace: bool = False,
    ) -> None:
        key = str(engine_key).strip()
        if not key:
            raise ValueError("engine_key is required")
        if not callable(processor):
            raise TypeError("processor must be callable")
        with self._condition:
            if key in self._processors and not replace:
                raise ValueError(f"processor is already registered: {key}")
            self._processors[key] = processor
            self._condition.notify_all()

    def start(self) -> dict[str, int]:
        """Start a scheduler participant and recover only after gaining ownership.

        Multiple app/sidecar processes may point at the same ledger.  Exactly
        one holds the process lock and executes work; passive participants keep
        serving API requests and automatically take over after leader exit.
        """
        with self._condition:
            if self._started:
                return dict(self.recovery_result)
            self._stopping = False
            executor: ThreadPoolExecutor | None = None
            became_leader = False
            try:
                became_leader = self._leader_lock.acquire()
                if became_leader:
                    self.recovery_result = self.ledger.recover_interrupted_jobs()
                    executor = ThreadPoolExecutor(
                        max_workers=self.max_workers,
                        thread_name_prefix="atelier-job-worker",
                    )
                else:
                    self.recovery_result = {
                        "interrupted": 0,
                        "requeued": 0,
                        "failed": 0,
                    }
            except Exception:
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=False)
                self._leader_lock.release()
                raise
            coordinator = threading.Thread(
                target=self._coordinator_loop,
                name=self.thread_name,
                daemon=False,
            )
            self._executor = executor
            self._coordinator = coordinator
            self._is_leader = became_leader
            self._started = True
            try:
                coordinator.start()
            except Exception:
                self._started = False
                self._is_leader = False
                self._executor = None
                self._coordinator = None
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=False)
                self._leader_lock.release()
                raise
            self._condition.notify_all()
            return dict(self.recovery_result)

    def stop(self) -> None:
        """Stop claiming new work and wait for already claimed work to finish."""
        with self._condition:
            if not self._started:
                return
            self._stopping = True
            coordinator = self._coordinator
            executor = self._executor
            self._condition.notify_all()
        if coordinator is not None and coordinator is not threading.current_thread():
            coordinator.join()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        with self._condition:
            self._started = False
            self._stopping = False
            self._is_leader = False
            self._executor = None
            self._coordinator = None
            self._condition.notify_all()
        self._leader_lock.release()

    def __enter__(self) -> "JobEngine":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()

    def wake(self) -> None:
        """Notify the scheduler after an API transaction creates or retries work."""
        with self._condition:
            self._condition.notify_all()

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        """Durably cancel queued work and signal all running items in the job."""
        result = self.ledger.request_job_cancel(job_id)
        canceling_ids = {
            str(item["id"])
            for item in result.get("items", [])
            if item.get("status") == "canceling"
        }
        with self._condition:
            for item_id in canceling_ids:
                state = self._states_by_item.get(item_id)
                if state is not None:
                    state.cancel_event.set()
            self._condition.notify_all()
        return result

    def pause_job(self, job_id: str) -> dict[str, Any]:
        """Durably prevent new claims; running work settles normally."""
        result = self.ledger.pause_job(job_id)
        self.wake()
        return result

    def resume_job(self, job_id: str) -> dict[str, Any]:
        """Make a paused job runnable again without creating attempts."""
        result = self.ledger.resume_job(job_id)
        self.wake()
        return result

    def retry_failed(
        self,
        job_id: str,
        item_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Durably requeue only failed/interrupted items, then wake workers."""
        result = self.ledger.retry_job_items(job_id, item_ids=item_ids)
        self.wake()
        return result

    def wait_for_job(self, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
        """Wait on engine state notifications until a durable job is terminal."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                job = self.ledger.get_job(job_id)
                # Recovery commits the durable terminal state before removing
                # its in-memory queue entry. Do not let callers observe that
                # narrow half-reconciled window as a fully settled job.
                reconciliation_pending = any(
                    state.job_id == job_id
                    for state in self._unreconciled_workers.values()
                )
                if (
                    job["status"] in TERMINAL_JOB_STATUSES
                    and not reconciliation_pending
                ):
                    return job
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"job did not finish before timeout: {job_id}")
                self._condition.wait(timeout=min(remaining, self.poll_interval))

    def snapshot(self) -> dict[str, Any]:
        """Return non-authoritative executor metrics for diagnostics and tests."""
        with self._condition:
            return {
                "running": self._started and not self._stopping,
                "leader": self._is_leader,
                "in_flight": len(self._futures),
                "job_in_flight": dict(self._job_inflight),
                "resource_in_use": dict(self._resource_in_use),
                "resource_peak": dict(self._resource_peak),
                "admission_in_use": dict(self._admission_in_use),
                "admission_peak": dict(self._admission_peak),
                "unreconciled_workers": sorted(self._unreconciled_workers),
                "max_workers": self.max_workers,
                "resource_limits": dict(self._resource_limits),
                "processor_admission_resources": dict(self._processor_admission_resources),
            }

    def _notify_state_change(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _coordinator_loop(self) -> None:
        failure_streak = 0
        while True:
            with self._condition:
                if self._stopping:
                    return
                is_leader = self._is_leader
            if not is_leader:
                if self._try_become_leader():
                    continue
                with self._condition:
                    if self._stopping:
                        return
                    self._condition.wait(timeout=self.poll_interval)
                continue
            try:
                reconciled = self._reconcile_unowned_worker_failures()
                made_progress = self._schedule_available() or reconciled
                self._sync_durable_cancellations()
                failure_streak = 0
            except Exception:
                # A transient SQLite/filesystem failure must not silently kill
                # the sole scheduler thread while this process keeps the leader
                # lock. Keep ownership, back off, and retry in-place.
                failure_streak += 1
                LOGGER.exception("Durable job scheduler iteration failed")
                delay = min(
                    max(self.poll_interval, 0.05)
                    * (2 ** min(failure_streak - 1, 5)),
                    2.0,
                )
                with self._condition:
                    if self._stopping:
                        return
                    self._condition.wait(timeout=delay)
                continue
            with self._condition:
                if self._stopping:
                    return
                if not made_progress:
                    self._condition.wait(timeout=self.poll_interval)

    def _try_become_leader(self) -> bool:
        """Acquire scheduler ownership after the prior process exits."""
        if not self._leader_lock.acquire():
            return False
        executor: ThreadPoolExecutor | None = None
        try:
            recovery = self.ledger.recover_interrupted_jobs()
            executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="atelier-job-worker",
            )
            with self._condition:
                if self._stopping:
                    executor.shutdown(wait=True, cancel_futures=False)
                    self._leader_lock.release()
                    return False
                self.recovery_result = recovery
                self._executor = executor
                self._is_leader = True
                self._condition.notify_all()
            return True
        except Exception:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=False)
            self._leader_lock.release()
            LOGGER.exception("Failed to assume durable job scheduler leadership")
            return False

    def _schedule_available(self) -> bool:
        dispatched_any = False
        attempted_jobs: set[str] = set()
        round_progress = False
        while True:
            with self._condition:
                if self._stopping or len(self._futures) >= self.max_workers:
                    return dispatched_any
            heads = self.ledger.list_runnable_job_heads()
            eligible = [
                head
                for head in heads
                if self._head_has_capacity(head)
                and str(head["job_id"]) not in attempted_jobs
            ]
            if not eligible:
                if round_progress:
                    attempted_jobs.clear()
                    round_progress = False
                    continue
                return dispatched_any

            head = self._choose_round_robin(eligible)
            job_id = str(head["job_id"])
            attempted_jobs.add(job_id)
            claim = self.ledger.claim_job_item(str(head["id"]))
            if claim is None:
                continue

            state = _RunState(
                job_id=job_id,
                item_id=str(claim["item"]["id"]),
                engine_key=str(claim["item"]["engine_key"]),
                claim=claim,
                cancel_event=threading.Event(),
                admission_resource=self._processor_admission_resources.get(
                    str(claim["item"]["engine_key"])
                ),
            )
            with self._condition:
                if self._stopping or self._executor is None:
                    # The coordinator is the only submitter, so this can only be
                    # reached during an unexpected lifecycle race. Recovery on
                    # the next start will reconcile the durable running claim.
                    return dispatched_any
                future = self._executor.submit(self._execute, state)
                self._futures[future] = state
                self._states_by_item[state.item_id] = state
                self._job_inflight[state.job_id] += 1
                if state.admission_resource is not None:
                    self._admission_in_use[state.admission_resource] += 1
                    self._admission_peak[state.admission_resource] = max(
                        self._admission_peak[state.admission_resource],
                        self._admission_in_use[state.admission_resource],
                    )
                self._last_job_id = state.job_id
                future.add_done_callback(self._worker_done)
                self._condition.notify_all()
            dispatched_any = True
            round_progress = True

    def _head_has_capacity(self, head: Mapping[str, Any]) -> bool:
        job_id = str(head["job_id"])
        requested = max(1, int(head["requested_concurrency"]))
        admission_resource = self._processor_admission_resources.get(str(head["engine_key"]))
        with self._condition:
            if self._job_inflight[job_id] >= requested:
                return False
            if admission_resource is None:
                return True
            return (
                self._admission_in_use[admission_resource]
                < self._resource_limits[admission_resource]
            )

    def _choose_round_robin(self, heads: list[dict[str, Any]]) -> dict[str, Any]:
        if not heads or self._last_job_id is None:
            return heads[0]
        job_ids = [str(head["job_id"]) for head in heads]
        try:
            index = job_ids.index(self._last_job_id)
        except ValueError:
            return heads[0]
        return heads[(index + 1) % len(heads)]

    def _sync_durable_cancellations(self) -> None:
        with self._condition:
            states = list(self._states_by_item.values())
        changed = False
        for state in states:
            if state.cancel_event.is_set():
                continue
            try:
                status = self.ledger.get_job_item(state.item_id)["status"]
            except KeyError:
                status = "canceled"
            if status in {"canceling", "canceled"}:
                state.cancel_event.set()
                changed = True
        if changed:
            self._notify_state_change()

    def _execute(self, state: _RunState) -> None:
        context = ExecutionContext(self, state)
        staged: JobProcessorResult | None = None
        commit_succeeded = False
        try:
            processor = self._processor_for(state.engine_key)
            raw_result = processor(context)
            staged = self._normalize_result(raw_result)
            context.checkpoint()
            if staged.commit is not None:
                committed_metadata = staged.commit()
                commit_succeeded = True
                if committed_metadata:
                    staged = JobProcessorResult(
                        metadata={**dict(staged.metadata), **dict(committed_metadata)},
                        commit=None,
                        cleanup=staged.cleanup,
                        durable_completion=staged.durable_completion,
                    )
            if staged.durable_completion:
                if not commit_succeeded:
                    raise JobExecutionError(
                        "INVALID_PROCESSOR_RESULT",
                        "durable_completion requires a successful commit callback",
                    )
                # The durable commit owns the result rows and closes the
                # attempt/item/parent in one transaction. Do not perform a
                # fallible verification read here: a read error after commit
                # must never enter the cleanup path and delete valid results.
                return
            context.checkpoint()
            metadata = {**dict(context.metadata), **dict(staged.metadata)}
            actual_status = self._finish_item(
                state, "completed", attempt_metadata=metadata
            )
            if actual_status != "completed":
                # Cancellation may win after the post-commit checkpoint but
                # before the durable terminal write.  A canceled item must not
                # retain a published artifact from that losing race.
                self._safe_cleanup(staged)
                if actual_status == "canceling":
                    self._finish_item(
                        state,
                        "canceled",
                        error_code="USER_CANCELED",
                        error_message="Canceled by user",
                        attempt_metadata=metadata,
                    )
        except JobCanceled:
            self._safe_cleanup(staged)
            self._finish_item(
                state,
                "canceled",
                error_code="USER_CANCELED",
                error_message="Canceled by user",
                attempt_metadata=dict(context.metadata),
            )
        except StaleExecution:
            self._safe_cleanup(staged)
        except JobExecutionError as exc:
            self._safe_cleanup(staged)
            metadata = {**dict(context.metadata), **exc.metadata}
            self._finish_item(
                state,
                "failed",
                error_code=exc.code,
                error_message=str(exc),
                attempt_metadata=metadata,
            )
        except Exception as exc:  # one processor failure must not stop siblings
            self._safe_cleanup(staged)
            self._finish_item(
                state,
                "failed",
                error_code="PROCESSOR_ERROR",
                error_message=str(exc) or type(exc).__name__,
                attempt_metadata={
                    **dict(context.metadata),
                    "exception_type": type(exc).__name__,
                },
            )

    def _processor_for(self, engine_key: str) -> JobProcessor:
        with self._condition:
            processor = self._processors.get(engine_key)
        if processor is None:
            raise JobExecutionError(
                "PROCESSOR_NOT_REGISTERED",
                f"No processor is registered for engine {engine_key!r}",
            )
        return processor

    @staticmethod
    def _normalize_result(result: ProcessorResult) -> JobProcessorResult:
        if result is None:
            return JobProcessorResult()
        if isinstance(result, JobProcessorResult):
            return result
        if isinstance(result, Mapping):
            return JobProcessorResult(metadata=dict(result))
        raise JobExecutionError(
            "INVALID_PROCESSOR_RESULT",
            "Processor must return metadata, JobProcessorResult, or None",
        )

    def _finish_item(
        self,
        state: _RunState,
        status: str,
        *,
        error_code: str = "",
        error_message: str = "",
        attempt_metadata: Mapping[str, Any] | None = None,
    ) -> str | None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self._finish_item_once(
                    state,
                    status,
                    error_code=error_code,
                    error_message=error_message,
                    attempt_metadata=attempt_metadata,
                )
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    raise
                # A committed transaction followed by a transport/read error is
                # safe to retry: _finish_item_once first observes the durable
                # state and returns an existing terminal status idempotently.
                with self._condition:
                    self._condition.wait(
                        timeout=min(max(self.poll_interval, 0.01) * (2 ** attempt), 0.25)
                    )
        assert last_error is not None
        raise last_error

    def _finish_item_once(
        self,
        state: _RunState,
        status: str,
        *,
        error_code: str = "",
        error_message: str = "",
        attempt_metadata: Mapping[str, Any] | None = None,
    ) -> str | None:
        requested_status = status
        try:
            latest = self.ledger.get_job_item(state.item_id)
        except KeyError:
            return None
        current = str(latest["status"])
        if current not in ACTIVE_ITEM_STATUSES:
            return current
        if current == "canceling" and status != "canceled":
            if requested_status == "completed":
                # Keep the durable state at canceling until the caller removes
                # any already-published artifact. Only then write canceled.
                return "canceling"
            status = "canceled"
            error_code = "USER_CANCELED"
            error_message = "Canceled by user"
        try:
            self.ledger.finish_job_item(
                state.item_id,
                status,
                error_code=error_code,
                error_message=error_message,
                attempt_metadata=dict(attempt_metadata or {}),
            )
            return status
        except InvalidStatusTransitionError:
            # Cancellation can win between the status read and terminal write.
            latest = self.ledger.get_job_item(state.item_id)
            if latest["status"] == "canceling":
                if requested_status == "completed":
                    return "canceling"
                self.ledger.finish_job_item(
                    state.item_id,
                    "canceled",
                    error_code="USER_CANCELED",
                    error_message="Canceled by user",
                    attempt_metadata=dict(attempt_metadata or {}),
                )
                return "canceled"
            elif latest["status"] in ACTIVE_ITEM_STATUSES:
                raise
            return str(latest["status"])
        finally:
            self._notify_state_change()

    @staticmethod
    def _safe_cleanup(result: JobProcessorResult | None) -> None:
        if result is None or result.cleanup is None:
            return
        try:
            result.cleanup()
        except Exception:
            LOGGER.exception("Job processor cleanup failed")

    def _worker_done(self, future: Future[None]) -> None:
        infrastructure_failure = False
        try:
            future.result()
        except Exception:
            infrastructure_failure = True
            LOGGER.exception("Unhandled job worker infrastructure failure")
        with self._condition:
            state = self._futures.pop(future, None)
            if state is not None:
                self._states_by_item.pop(state.item_id, None)
                self._job_inflight[state.job_id] -= 1
                if self._job_inflight[state.job_id] <= 0:
                    self._job_inflight.pop(state.job_id, None)
                if state.admission_resource is not None:
                    if self._admission_in_use[state.admission_resource] <= 1:
                        self._admission_in_use.pop(state.admission_resource, None)
                    else:
                        self._admission_in_use[state.admission_resource] -= 1
                if infrastructure_failure:
                    # The future is finished, so no live worker can still own
                    # this item. Keep it on a durable reconciliation queue until
                    # the leader can mark it interrupted/requeued/failed.
                    self._unreconciled_workers[state.item_id] = state
            self._condition.notify_all()

    def _reconcile_unowned_worker_failures(self) -> bool:
        with self._condition:
            item_ids = list(self._unreconciled_workers)
        reconciled = False
        for item_id in item_ids:
            self.ledger.recover_orphaned_job_item(
                item_id,
                error_code="WORKER_INFRASTRUCTURE_FAILURE",
                error_message="Worker stopped before its terminal state could be persisted",
            )
            with self._condition:
                self._unreconciled_workers.pop(item_id, None)
                self._condition.notify_all()
            reconciled = True
        return reconciled

    def _acquire_resource(self, name: str, context: ExecutionContext) -> None:
        with self._condition:
            if name not in self._resource_limits:
                raise UnknownResourceError(f"unconfigured job resource: {name}")
            while self._resource_in_use[name] >= self._resource_limits[name]:
                if context._state.cancel_event.is_set():
                    raise JobCanceled(f"job item canceled: {context.item_id}")
                self._condition.wait(timeout=self.poll_interval)
                context.checkpoint()
            context.checkpoint()
            self._resource_in_use[name] += 1
            self._resource_peak[name] = max(
                self._resource_peak[name], self._resource_in_use[name]
            )
            self._condition.notify_all()

    def _release_resource(self, name: str) -> None:
        with self._condition:
            if self._resource_in_use[name] <= 1:
                self._resource_in_use.pop(name, None)
            else:
                self._resource_in_use[name] -= 1
            self._condition.notify_all()


__all__ = [
    "DEFAULT_RESOURCE_LIMITS",
    "ExecutionContext",
    "JobCanceled",
    "JobEngine",
    "JobExecutionError",
    "JobProcessor",
    "JobProcessorResult",
    "StaleExecution",
    "UnknownResourceError",
]
