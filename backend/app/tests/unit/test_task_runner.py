from __future__ import annotations

from app.services.task_runner import LocalTaskRunner


def test_local_task_runner_executes_background_call() -> None:
    runner = LocalTaskRunner(max_workers=1)
    try:
        submission = runner.submit("sum", lambda x, y: x + y, 1, 2)

        assert submission.backend == "local"
        assert runner.get_result(submission.task_id, timeout=2) == 3
        assert runner.get_status(submission.task_id) == "completed"
    finally:
        runner.shutdown(wait=True)
