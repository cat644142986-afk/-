#!/usr/bin/env python3
"""Export a privacy-reduced generation baseline from an existing local ledger.

The exporter is read-only: it does not open images, call providers, read API
keys, or mutate the SQLite database.  Job identifiers are hashed by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python.generation_baseline import (  # noqa: E402
    GENERATION_TRACE_CONTRACT_VERSION,
    summarize_trace_timings,
)


def _decode_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _decode_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 3)


def _job_key(job_id: str, include_job_ids: bool) -> str:
    if include_job_ids:
        return job_id
    return hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]


def _read_rows(database: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    uri = f"file:{quote(database.resolve().as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        traces = [dict(row) for row in connection.execute(
            """
            SELECT job_id, stage, status, parameters_json, output_json
            FROM execution_traces
            WHERE job_id IS NOT NULL AND job_id != ''
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()]
        reviews = [dict(row) for row in connection.execute(
            """
            SELECT job_id, decision, reason_codes_json
            FROM result_reviews
            WHERE job_id IS NOT NULL AND job_id != '' AND status = 'submitted'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()]
    return traces, reviews


def build_report(
    trace_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    *,
    include_job_ids: bool = False,
) -> dict[str, Any]:
    traces_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reviews_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_timings: dict[str, list[float]] = defaultdict(list)
    for row in trace_rows:
        job_id = str(row.get("job_id") or "")
        output = _decode_object(row.get("output_json"))
        parameters = _decode_object(row.get("parameters_json"))
        trace = {
            "stage": str(row.get("stage") or ""),
            "status": str(row.get("status") or ""),
            "parameters": parameters,
            "output": output,
        }
        traces_by_job[job_id].append(trace)
        elapsed = output.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            stage_timings[trace["stage"]].append(max(0.0, float(elapsed)))
    for row in review_rows:
        reviews_by_job[str(row.get("job_id") or "")].append(row)

    jobs = []
    decision_totals: Counter[str] = Counter()
    reason_totals: Counter[str] = Counter()
    for job_id, traces in sorted(traces_by_job.items()):
        summary = summarize_trace_timings(traces)
        prompt_snapshots = []
        provider_contracts = []
        for trace in traces:
            output = trace["output"]
            parameters = trace["parameters"]
            snapshot = output.get("prompt_snapshot")
            if isinstance(snapshot, dict):
                prompt_snapshots.append(snapshot)
            contract = parameters.get("capability_contract")
            if isinstance(contract, dict) and contract not in provider_contracts:
                provider_contracts.append(contract)
        decisions: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        for review in reviews_by_job.get(job_id, []):
            decision = str(review.get("decision") or "pending")
            decisions[decision] += 1
            decision_totals[decision] += 1
            for reason in _decode_list(review.get("reason_codes_json")):
                reason_text = str(reason).strip()
                if reason_text:
                    reasons[reason_text] += 1
                    reason_totals[reason_text] += 1
        jobs.append({
            "job_key": _job_key(job_id, include_job_ids),
            "timing": summary,
            "prompt_snapshots": prompt_snapshots,
            "provider_contracts": provider_contracts,
            "quality_feedback": {
                "submitted_review_count": sum(decisions.values()),
                "decisions": dict(sorted(decisions.items())),
                "reason_codes": dict(sorted(reasons.items())),
            },
        })

    timing_summary = {
        stage: {
            "sample_count": len(values),
            "p50_ms": round(statistics.median(values), 3),
            "p95_ms": _percentile(values, 0.95),
        }
        for stage, values in sorted(stage_timings.items())
    }
    instrumented = sum(
        any(trace["stage"] == "workflow.complete" for trace in traces)
        for traces in traces_by_job.values()
    )
    prompt_snapshot_count = sum(len(job["prompt_snapshots"]) for job in jobs)
    billable_calls = sum(job["timing"]["billable_call_count"] for job in jobs)
    priced_calls = sum(job["timing"]["priced_call_count"] for job in jobs)
    return {
        "schema_version": "1.0",
        "trace_contract_version": GENERATION_TRACE_CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "privacy": {
            "job_ids": "included" if include_job_ids else "sha256-prefix",
            "prompts_included": False,
            "images_read": False,
            "api_keys_read": False,
        },
        "coverage": {
            "job_count": len(jobs),
            "instrumented_job_count": instrumented,
            "prompt_snapshot_count": prompt_snapshot_count,
            "submitted_review_count": sum(decision_totals.values()),
        },
        "timings": timing_summary,
        "cost": {
            "billable_call_count": billable_calls,
            "priced_call_count": priced_calls,
            "status": "available" if billable_calls and priced_calls == billable_calls else "incomplete",
        },
        "quality_feedback": {
            "decisions": dict(sorted(decision_totals.items())),
            "reason_codes": dict(sorted(reason_totals.items())),
        },
        "jobs": jobs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True, help="Existing atelier.sqlite3")
    parser.add_argument("--output", type=Path, required=True, help="Destination JSON")
    parser.add_argument(
        "--include-job-ids",
        action="store_true",
        help="Include raw job IDs instead of privacy-reduced hashes",
    )
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"database does not exist: {args.db}")
    traces, reviews = _read_rows(args.db)
    report = build_report(traces, reviews, include_job_ids=args.include_job_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["coverage"], ensure_ascii=False))
    print(f"cost_status={report['cost']['status']}")
    print(f"report={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
