import threading
import time
import unittest

from fugu_local.execution import FanoutExecutor, FanoutTask


class FanoutExecutorTests(unittest.TestCase):
    def test_dispatches_all_tasks_concurrently_and_reuses_fixed_pool(self):
        active = 0
        peak_active = 0
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def callback(index):
            nonlocal active, peak_active
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                barrier.wait(timeout=1.0)
                return index
            finally:
                with lock:
                    active -= 1

        executor = FanoutExecutor(4, thread_name_prefix="test-fanout-fixed")
        try:
            tasks = [
                FanoutTask(str(index), lambda index=index: callback(index))
                for index in range(4)
            ]
            first = executor.run(tasks)
            second = executor.run(tasks)
            self.assertTrue(
                any(thread.name.startswith("test-fanout-fixed") for thread in threading.enumerate())
            )
        finally:
            executor.close()

        self.assertEqual([result.value for result in first], [0, 1, 2, 3])
        self.assertEqual([result.value for result in second], [0, 1, 2, 3])
        self.assertEqual(peak_active, 4)
        self.assertEqual(executor.active_task_count, 0)
        self.assertTrue(executor.closed)
        self.assertFalse(
            any(thread.name.startswith("test-fanout-fixed") for thread in threading.enumerate())
        )

    def test_preserves_order_and_isolates_callback_exception(self):
        timings = []

        def callback(key):
            if key == "failed":
                raise ValueError("expected failure")
            return f"result:{key}"

        executor = FanoutExecutor(3, thread_name_prefix="test-fanout-isolation")
        try:
            results = executor.run(
                [
                    FanoutTask("first", lambda: callback("first")),
                    FanoutTask("failed", lambda: callback("failed")),
                    FanoutTask("last", lambda: callback("last")),
                ],
                timing_hook=timings.append,
            )
        finally:
            executor.close()

        self.assertEqual([result.key for result in results], ["first", "failed", "last"])
        self.assertEqual([result.state for result in results], ["completed", "failed", "completed"])
        self.assertEqual(results[0].value, "result:first")
        self.assertIsInstance(results[1].error, ValueError)
        self.assertEqual(results[2].value, "result:last")
        self.assertEqual({timing.key for timing in timings}, {"first", "failed", "last"})
        self.assertTrue(all(timing.queue_wait_ms is not None for timing in timings))
        self.assertTrue(any(timing.state == "started" for timing in timings))

    def test_deadline_does_not_start_queued_tasks_and_tracks_running_task(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_callback():
            calls.append("blocking")
            started.set()
            release.wait(timeout=1.0)
            return "done"

        def queued_callback():
            calls.append("queued")
            return "should not run"

        executor = FanoutExecutor(1, thread_name_prefix="test-fanout-deadline")
        run_results = []
        run_thread = threading.Thread(
            target=lambda: run_results.append(
                executor.run(
                    [
                        FanoutTask("blocking", blocking_callback),
                        FanoutTask("queued-1", queued_callback),
                        FanoutTask("queued-2", queued_callback),
                    ],
                    deadline=time.perf_counter() + 0.05,
                )
            )
        )
        run_thread.start()
        self.assertTrue(started.wait(timeout=1.0))
        run_thread.join(timeout=1.0)
        self.assertFalse(run_thread.is_alive())

        results = run_results[0]
        self.assertEqual(
            [result.state for result in results],
            ["running_at_deadline", "not_started", "not_started"],
        )
        self.assertEqual(calls, ["blocking"])
        self.assertGreaterEqual(executor.active_task_count, 1)

        release.set()
        executor.close()
        self.assertEqual(executor.active_task_count, 0)

    def test_close_rejects_new_work_and_is_idempotent(self):
        executor = FanoutExecutor(1, thread_name_prefix="test-fanout-close")
        executor.close()
        executor.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            executor.run([FanoutTask("late", lambda: None)])


if __name__ == "__main__":
    unittest.main()
