from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FIXTURES = BACKEND / "app" / "tests" / "evaluation" / "fixtures"
sys.path.insert(0, str(BACKEND))

from app.evaluation.scoring import (  # noqa: E402
    CANDIDATE_PENDING_COVERAGE_RULE_VERSION,
    RULE_VERSION,
    canonical_dataset_hash,
    load_cases,
    score_report,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated WeeklyReport Agent evaluations.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--split", choices=("dev", "frozen"))
    selection.add_argument("--case")
    parser.add_argument("--live", action="store_true", help="Explicitly enable real model calls.")
    parser.add_argument("--judge", action="store_true", help="Run supplemental Judge on frozen cases.")
    parser.add_argument(
        "--candidate-pending-coverage",
        action="store_true",
        help="Opt in to candidate pending-coverage diagnostics; does not replace Baseline metrics.",
    )
    parser.add_argument("--output", type=Path, help="Write a metrics-only JSON summary to this path.")
    parser.add_argument("--timeout", type=int, default=240, help="Maximum seconds per workflow phase.")
    return parser.parse_args()


def _all_cases() -> list[dict[str, Any]]:
    return load_cases(FIXTURES / "dev_cases.json") + load_cases(FIXTURES / "frozen_cases.json")


def _run_offline(cases: list[dict[str, Any]], *, candidate_pending_coverage: bool) -> dict[str, Any]:
    result = {
        "mode": "offline-plan",
        "notice": "No model was called. Pass --live to run the isolated workflow.",
        "cases": [{"id": item["id"], "attachment": bool(item["attachment"])} for item in cases],
        "development_dataset_hash": canonical_dataset_hash(FIXTURES / "dev_cases.json"),
        "frozen_dataset_hash": canonical_dataset_hash(FIXTURES / "frozen_cases.json"),
        "evaluation_rule_version": RULE_VERSION,
    }
    if candidate_pending_coverage:
        result["candidate_pending_coverage"] = {
            "rule_version": CANDIDATE_PENDING_COVERAGE_RULE_VERSION,
            "notice": "Offline-plan mode has no reports to rescore; no model was called.",
        }
    return result


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "task_success", "route_correct", "section_completeness", "period_accuracy",
        "critical_fact_recall", "status_classification_accuracy", "revision_success",
        "items_to_confirm_coverage", "artifact_success",
    )
    summary: dict[str, Any] = {"case_count": len(results)}
    for name in metric_names:
        values = [result["metrics"].get(name) for result in results]
        bool_values = [value for value in values if isinstance(value, bool)]
        numeric_values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if bool_values:
            summary[name] = sum(bool_values) / len(bool_values)
        elif numeric_values:
            summary[name] = sum(numeric_values) / len(numeric_values)
        else:
            summary[name] = None
    summary["forbidden_claim_hits"] = sum(
        len(result["metrics"].get("forbidden_claim_hits", [])) for result in results
    )
    summary["judge_average"] = _mean_judge(results)
    return summary


def _mean_judge(results: list[dict[str, Any]]) -> dict[str, float | None]:
    values: dict[str, list[float]] = {"groundedness": [], "completeness": []}
    for result in results:
        judge = result.get("judge") or {}
        if not judge.get("valid"):
            continue
        for key in values:
            score = judge.get(key)
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                values[key].append(float(score))
    return {key: sum(items) / len(items) if items else None for key, items in values.items()}


def _isolated_app(temp_root: Path):
    from app.services.config_service import ConfigService

    settings = ConfigService().load().model_copy(deep=True)
    settings.storage.artifact_root = temp_root / "artifacts"
    settings.storage.sqlite_path = temp_root / "sqlite" / "evaluation.db"
    settings.logging.path = temp_root / "logs" / "evaluation.log.jsonl"
    settings.observability.provider = "none"
    ConfigService.load = lambda self, settings_path=None: settings  # type: ignore[method-assign]

    from app import main

    return main.app, settings


def _response(client, method: str, path: str, **kwargs):
    last_error = None
    for attempt in range(2):
        try:
            response = getattr(client, method)(path, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt == 0 and exc.__class__.__name__ in {"ConnectError", "ReadTimeout", "TimeoutException"}:
                time.sleep(1)
                continue
            raise
    raise RuntimeError("Evaluation API request failed after one retry.") from last_error


def _wait_for_state(client, task_id: str, timeout: int, *, min_versions: int = 1) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout
    last_task: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_task = _response(client, "get", f"/api/tasks/{task_id}").json()
        versions = _response(client, "get", f"/api/tasks/{task_id}/weekly-report-versions").json()
        status = last_task.get("status")
        if status in {"failed", "canceled"}:
            try:
                events = _response(client, "get", f"/api/tasks/{task_id}/events").json().get("events", [])
                failures = [event.get("content") for event in events if event.get("event_type") == "node_failed"]
                stages = [event.get("agent") for event in events if event.get("event_type") in {"node_started", "node_failed"}]
            except Exception:
                stages = []
            raise RuntimeError(
                f"Task reached terminal status {status} at node {last_task.get('current_node')!r}; "
                f"observed stages: {stages!r}; failure summaries: {failures!r}."
            )
        if status in {"waiting", "completed"} and len(versions) >= min_versions:
            return last_task, versions
        time.sleep(0.5)
    raise TimeoutError(f"Task {task_id} did not reach a stable state in {timeout}s.")


def _judge(chat_model, case: dict[str, Any], report: str) -> dict[str, Any]:
    from app.evaluation.scoring import parse_judge_json
    from langchain_core.messages import HumanMessage

    prompt = {
        "task_name": case["task_name"],
        "notes": case["notes"],
        "attachment_note": "Image attached; judge only claims supported by visible material.",
        "report": report,
    }
    response = chat_model.invoke([
        HumanMessage(content=(
            "Evaluate this weekly report using the source material. Return only JSON with numeric "
            "groundedness and completeness scores from 0 to 5 and concise reasons for each. "
            "This is supplemental; do not re-evaluate exact numeric facts.\n" + json.dumps(prompt, ensure_ascii=False)
        ))
    ])
    return parse_judge_json(str(response.content))


def _run_live(
    cases: list[dict[str, Any]],
    timeout: int,
    with_judge: bool,
    *,
    candidate_pending_coverage: bool,
) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from app.evaluation.scoring import parse_judge_json

    run_dir = Path(tempfile.mkdtemp(prefix="weekly-report-eval-"))
    app, settings = _isolated_app(run_dir)
    outputs: list[dict[str, Any]] = []
    try:
      with TestClient(app) as client:
        for case in cases:
            notes = case["notes"]
            period = case.get("report_period")
            if period and period not in notes and period not in case["task_name"]:
                notes = f"{notes.rstrip()}\n\n报告周期：{period}"
            fields = {"task_name": case["task_name"], "task_type": "weekly_report", "notes": notes}
            attachment = case.get("attachment")
            files = None
            if attachment:
                path = (FIXTURES / attachment).resolve()
                if not path.is_relative_to(FIXTURES.resolve()) or not path.is_file():
                    raise ValueError(f"Unsafe or missing fixture attachment for {case['id']}.")
                files = {"files": (path.name, path.read_bytes(), "image/png")}
            created = _response(client, "post", "/api/tasks", data=fields, files=files).json()["task"]
            task_id = created["task_id"]
            base = f"/api/tasks/{task_id}"
            _response(client, "post", f"{base}/start")
            task, versions = _wait_for_state(client, task_id, timeout)
            initial_versions = versions
            if case.get("revision_instruction"):
                _response(client, "post", f"{base}/feedback", data={
                    "feedback_text": case["revision_instruction"], "request_id": f"eval_{uuid4().hex[:16]}"
                })
                task, versions = _wait_for_state(client, task_id, timeout, min_versions=2)
            version0 = next((item for item in initial_versions if item["version"] == "V0"), initial_versions[0])
            final_version = versions[-1]
            state_payload = app.state.artifact_service.load_workflow_state(task_id)
            review_decision = (state_payload.get("latest_review_result") or {}).get("decision")
            if task["status"] == "waiting" and review_decision == "approved":
                _response(client, "post", f"{base}/confirm")
                task, versions = _wait_for_state(client, task_id, timeout, min_versions=len(versions))
                final_version = versions[-1]
            timeline = app.state.trace_service.get_trace_timeline(task_id)
            stages = {log.stage for log in timeline.logs}
            material_seen = "material_understanding" in stages
            runtime = {
                "task_status": task["status"],
                "material_understanding": material_seen,
                "artifact_success": bool(final_version.get("markdown")),
            }
            report_metrics = score_report(
                case,
                final_version.get("markdown", ""),
                runtime=runtime,
                include_candidate_pending_coverage=candidate_pending_coverage,
            )
            record: dict[str, Any] = {
                "case_id": case["id"], "task_id": task_id,
                "task_status": task["status"], "metrics": report_metrics,
                "review_decision": review_decision,
                "versions": [item["version"] for item in versions],
                "version_comparison": {
                    "label": "Writer-only V0 vs. Review/revision pipeline final output",
                    "v0": score_report(
                        case,
                        version0.get("markdown", ""),
                        runtime={"task_status": "completed"},
                        include_candidate_pending_coverage=candidate_pending_coverage,
                    ),
                    "final": score_report(
                        case,
                        final_version.get("markdown", ""),
                        runtime={"task_status": task["status"]},
                        include_candidate_pending_coverage=candidate_pending_coverage,
                    ),
                    "attribution_note": "Preset human feedback is included where configured; gains are not attributed solely to Review.",
                } if case.get("revision_instruction") else None,
            }
            if with_judge and case["split"] == "frozen":
                from app.services.chat_model_factory import ChatModelFactory
                record["judge"] = _judge(ChatModelFactory(settings.model).chat(), case, final_version.get("markdown", ""))
            outputs.append(record)
      result = {
        "mode": "live-isolated",
        "run_timestamp": datetime.now(UTC).isoformat(),
        "run_directory": "<temporary isolated directory>",
        "application_commit": "cf36c83ed34d3b422865eaf8c461cec9e477130c",
        "text_model": settings.model.chat.model,
        "vision_model": settings.model.vision.model,
        "development_dataset_hash": canonical_dataset_hash(FIXTURES / "dev_cases.json"),
        "frozen_dataset_hash": canonical_dataset_hash(FIXTURES / "frozen_cases.json"),
        "evaluation_rule_version": RULE_VERSION,
        "candidate_pending_coverage_rule_version": CANDIDATE_PENDING_COVERAGE_RULE_VERSION
        if candidate_pending_coverage else None,
        "results": outputs,
        "summary": _aggregate(outputs),
      }
      return result
    finally:
      shutil.rmtree(run_dir, ignore_errors=True)


def main() -> int:
    args = _arguments()
    all_cases = _all_cases()
    if args.case:
        cases = [case for case in all_cases if case["id"] == args.case]
        if not cases:
            raise SystemExit(f"Unknown evaluation case: {args.case}")
    elif args.split:
        dataset_path = FIXTURES / ("dev_cases.json" if args.split == "dev" else "frozen_cases.json")
        cases = load_cases(dataset_path)
    else:
        cases = all_cases
    if len(cases) > 10:
        raise SystemExit("A single evaluation run is limited to 10 cases.")
    result = (
        _run_live(
            cases,
            args.timeout,
            args.judge,
            candidate_pending_coverage=args.candidate_pending_coverage,
        )
        if args.live else _run_offline(cases, candidate_pending_coverage=args.candidate_pending_coverage)
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
