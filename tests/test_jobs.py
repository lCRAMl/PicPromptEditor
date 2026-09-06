"""Tests von JobManager, Jobisolation und Abbruch."""

from __future__ import annotations

import threading
import time

import pytest

from app.core.cancellation import CancellationToken
from app.jobs.job import Job, JobState
from app.jobs.job_manager import JobManager, PoolConfig
from tests.conftest import pump_until


class _Simple(Job):
    pool = "cpu"
    title = "Test"

    def __init__(self, reporter, value=42, delay=0.0, token=None):
        super().__init__(reporter, token)
        self._value = value
        self._delay = delay

    def run_job(self):
        if self._delay:
            time.sleep(self._delay)
        return self._value


class _Boom(Job):
    pool = "cpu"
    title = "Fehlerhaft"

    def run_job(self):
        raise ValueError("absichtlich kaputt")


class _Counting(Job):
    pool = "cpu"
    title = "Zaehlt"

    def __init__(self, reporter, steps=200, token=None):
        super().__init__(reporter, token)
        self._steps = steps
        self.completed = 0

    def run_job(self):
        for _ in range(self._steps):
            self.check_cancelled()
            self.completed += 1
            time.sleep(0.002)
        return self.completed


@pytest.fixture
def manager(qapp, reporter):
    instance = JobManager(reporter, PoolConfig(interactive=2, io=2, cpu=2))
    yield instance
    instance.shutdown()


class TestJobManager:
    def test_pools_are_separate(self, manager) -> None:
        sizes = manager.pool_sizes()
        assert sizes["interactive"] == 2
        assert sizes["io"] == 2
        assert sizes["cpu"] == 2

    def test_cpu_pool_defaults_to_cores_minus_two(self, qapp, reporter) -> None:
        config = PoolConfig(cpu=0)
        assert config.resolved_cpu() >= 2
        manager = JobManager(reporter, config)
        try:
            assert manager.pool_sizes()["cpu"] == config.resolved_cpu()
        finally:
            manager.shutdown()

    def test_result_is_delivered(self, qapp, manager, reporter) -> None:
        received = []
        job = _Simple(reporter, value=7)
        job.signals.result.connect(lambda _id, payload: received.append(payload))
        manager.submit(job)
        assert pump_until(qapp, lambda: received), "Ergebnis kam nicht an"
        assert received[0] == 7

    def test_failure_is_isolated(self, qapp, manager, reporter, collected) -> None:
        """Spezifikation 08: ein Jobfehler darf keine anderen Jobs stoppen."""
        good = []
        failed = []

        boom = _Boom(reporter)
        boom.signals.failed.connect(lambda _id, message: failed.append(message))
        manager.submit(boom)

        after = _Simple(reporter, value=99)
        after.signals.result.connect(lambda _id, payload: good.append(payload))
        manager.submit(after)

        assert pump_until(qapp, lambda: failed and good)
        assert good[0] == 99, "der folgende Job muss trotzdem laufen"
        assert any(e.source.startswith("jobs.") for e in collected)
        assert manager.stats()["failed"] == 1

    def test_cancellation_stops_a_running_job(self, qapp, manager, reporter) -> None:
        token = CancellationToken()
        job = _Counting(reporter, steps=5000, token=token)
        states = []
        job.signals.finished.connect(lambda _id, state: states.append(state))

        manager.submit(job)
        pump_until(qapp, lambda: job.completed > 3, timeout_ms=5000)
        job.cancel()

        assert pump_until(qapp, lambda: states)
        assert states[0] == JobState.CANCELLED.value
        assert job.completed < 5000

    def test_cancel_group(self, qapp, manager, reporter) -> None:
        finished = []
        for _ in range(3):
            job = _Counting(reporter, steps=3000)
            job.signals.finished.connect(lambda _id, state: finished.append(state))
            manager.submit(job, group="stapel")

        manager.cancel_group("stapel")
        assert pump_until(qapp, lambda: len(finished) == 3)
        assert all(state == JobState.CANCELLED.value for state in finished)

    def test_job_cancelled_before_start_does_not_run(self, qapp, manager, reporter) -> None:
        token = CancellationToken()
        token.cancel()
        job = _Counting(reporter, steps=100, token=token)
        states = []
        job.signals.finished.connect(lambda _id, state: states.append(state))
        manager.submit(job)
        assert pump_until(qapp, lambda: states)
        assert states[0] == JobState.CANCELLED.value
        assert job.completed == 0

    def test_active_count_returns_to_zero(self, qapp, manager, reporter) -> None:
        done = []
        for i in range(5):
            job = _Simple(reporter, value=i, delay=0.01)
            job.signals.finished.connect(lambda _id, _s: done.append(1))
            manager.submit(job)
        assert pump_until(qapp, lambda: len(done) == 5)
        assert pump_until(qapp, lambda: manager.active_count == 0)

    def test_activity_signal_reports_load(self, qapp, manager, reporter) -> None:
        seen = []
        manager.activity_changed.connect(lambda count, text: seen.append(count))
        job = _Simple(reporter, delay=0.05)
        manager.submit(job)
        assert pump_until(qapp, lambda: manager.active_count == 0 and len(seen) >= 2)
        assert max(seen) >= 1
        assert seen[-1] == 0

    def test_unknown_pool_is_rejected(self, manager, reporter) -> None:
        job = _Simple(reporter)
        job.pool = "gibt-es-nicht"
        with pytest.raises(ValueError):
            manager.submit(job)

    def test_jobs_run_off_the_calling_thread(self, qapp, manager, reporter) -> None:
        threads = []

        class _Recording(Job):
            pool = "cpu"

            def run_job(self):
                threads.append(threading.get_ident())
                return None

        manager.submit(_Recording(reporter))
        assert pump_until(qapp, lambda: threads)
        assert threads[0] != threading.get_ident()
