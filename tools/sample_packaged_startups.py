#!/usr/bin/env python3
"""Sample packaged Product Atelier startup milestones without touching user data."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from capture_startup_video import find_process_window, stop_process_tree


MILESTONE_RE = re.compile(r"Startup milestone: ([a-z-]+) at (\d+)ms")
WINDOW_RE = re.compile(
    r"Window metrics: scale=([0-9.]+), physical=(\d+)x(\d+), logical=([0-9.]+)x([0-9.]+)"
)


def candidate_processes(executable: Path) -> list[psutil.Process]:
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["exe"]):
        try:
            process_exe = process.info.get("exe")
            if process_exe and Path(process_exe).resolve() == executable:
                matches.append(process)
        except (OSError, psutil.Error):
            continue
    return matches


def wait_for_log(
    log_path: Path,
    offset: int,
    process: subprocess.Popen[bytes],
    timeout_seconds: float,
) -> str:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                text = handle.read()
            if "Startup milestone: workspace-ready" in text:
                return text
        if process.poll() is not None:
            raise RuntimeError(f"Application exited early with code {process.returncode}")
        time.sleep(0.02)
    raise TimeoutError(f"workspace-ready was not logged within {timeout_seconds:g}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    executable = Path(args.exe).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = Path(args.output).resolve()
    if not executable.is_file():
        raise SystemExit(f"Executable not found: {executable}")
    if not data_dir.is_dir():
        raise SystemExit(f"Data directory not found: {data_dir}")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    if args.runs <= 0 or args.timeout <= 0:
        raise SystemExit("runs and timeout must be positive")

    existing = candidate_processes(executable)
    if existing:
        raise SystemExit(f"Candidate is already running: {[item.pid for item in existing]}")

    log_path = data_dir / "app.log"
    samples: list[dict[str, object]] = []
    booted_at = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()

    for run_number in range(1, args.runs + 1):
        if candidate_processes(executable):
            raise RuntimeError(f"Run {run_number}: a candidate process was left behind")
        offset = log_path.stat().st_size if log_path.exists() else 0
        env = os.environ.copy()
        env["PRODUCT_ATELIER_DATA_DIR"] = str(data_dir)
        started = time.perf_counter()
        process = subprocess.Popen([str(executable)], cwd=str(executable.parent), env=env)
        hwnd: int | None = None
        visible_ms: float | None = None
        try:
            deadline = started + args.timeout
            while time.perf_counter() < deadline:
                found = find_process_window(process.pid)
                if found:
                    hwnd = found[0]
                    visible_ms = round((time.perf_counter() - started) * 1000, 3)
                    break
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Run {run_number}: application exited early with code {process.returncode}"
                    )
                time.sleep(0.002)
            if hwnd is None:
                raise TimeoutError(f"Run {run_number}: window was not visible in time")

            text = wait_for_log(log_path, offset, process, args.timeout)
            milestones = {name: int(value) for name, value in MILESTONE_RE.findall(text)}
            missing = {
                "dom-ready",
                "first-paint",
                "backend-connecting",
                "backend-ready",
                "workspace-ready",
            } - milestones.keys()
            if missing:
                raise RuntimeError(f"Run {run_number}: missing milestones {sorted(missing)}")
            window_match = WINDOW_RE.search(text)
            if not window_match:
                raise RuntimeError(f"Run {run_number}: window metrics were not logged")
            samples.append(
                {
                    "run": run_number,
                    "window_visible_ms": visible_ms,
                    "milestones_ms": milestones,
                    "scale_factor": float(window_match.group(1)),
                    "physical_size": [int(window_match.group(2)), int(window_match.group(3))],
                    "logical_size": [float(window_match.group(4)), float(window_match.group(5))],
                }
            )
            print(json.dumps(samples[-1], ensure_ascii=False))
        finally:
            stop_process_tree(process, hwnd)
        time.sleep(0.25)

    fields = ["window_visible_ms", "first-paint", "backend-ready", "workspace-ready"]
    aggregates: dict[str, dict[str, float]] = {}
    for field in fields:
        values = [
            float(item[field])
            if field == "window_visible_ms"
            else float(item["milestones_ms"][field])
            for item in samples
        ]
        aggregates[field] = {
            "min": min(values),
            "max": max(values),
            "average": round(sum(values) / len(values), 3),
        }

    result = {
        "executable": str(executable),
        "data_dir": str(data_dir),
        "sampled_at": datetime.now(tz=timezone.utc).isoformat(),
        "system_booted_at": booted_at,
        "run_count": len(samples),
        "all_workspace_ready": len(samples) == args.runs,
        "aggregates_ms": aggregates,
        "samples": samples,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["aggregates_ms"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
