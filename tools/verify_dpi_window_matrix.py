#!/usr/bin/env python3
"""Verify packaged window behavior across a real Windows DPI configuration."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import psutil

from capture_startup_video import WindowCapture, find_process_window, stop_process_tree


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
DWMWA_WINDOW_CORNER_PREFERENCE = 33

user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def window_rect(process_id: int) -> tuple[int, int, int, int]:
    found = find_process_window(process_id)
    if not found:
        raise RuntimeError("Candidate window was not found")
    return found[1]


def move_window(hwnd: int, process_id: int, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    deadline = time.perf_counter() + 5
    settled = False
    while time.perf_counter() < deadline:
        if not user32.SetWindowPos(
            hwnd,
            wintypes.HWND(HWND_TOPMOST),
            x,
            y,
            width,
            height,
            SWP_SHOWWINDOW,
        ):
            raise ctypes.WinError()
        time.sleep(0.1)
        actual = window_rect(process_id)
        settled = (
            abs(actual[0] - x) <= 64
            and abs(actual[1] - y) <= 64
            and abs(actual[2] - width) <= 64
            and abs(actual[3] - height) <= 64
        )
        if settled:
            break
    if not settled:
        raise RuntimeError(f"Window did not settle at {rect}; actual={window_rect(process_id)}")
    time.sleep(0.4)
    return window_rect(process_id)


def capture(rect: tuple[int, int, int, int], path: Path, maximum_width: int = 2560) -> dict[str, object]:
    scale = min(1.0, maximum_width / rect[2])
    grabber = WindowCapture(rect, scale)
    try:
        frame = grabber.frame()
    finally:
        grabber.close()
    if not cv2.imwrite(str(path), frame):
        raise RuntimeError(f"Could not save screenshot: {path}")
    return {
        "path": str(path),
        "source_rect": list(rect),
        "saved_size": [int(frame.shape[1]), int(frame.shape[0])],
    }


def candidate_processes(executable: Path) -> list[int]:
    matches: list[int] = []
    for process in psutil.process_iter(["exe"]):
        try:
            process_exe = process.info.get("exe")
            if process_exe and Path(process_exe).resolve() == executable:
                matches.append(process.pid)
        except (OSError, psutil.Error):
            continue
    return matches


def build_window_checks(
    result: dict[str, object],
    *,
    expected_primary_dpi: int,
    expected_secondary_dpi: int | None,
) -> dict[str, bool]:
    checks = {
        "primary_dpi_matches": result["primary_restored"]["dpi"] == expected_primary_dpi,
        "primary_return_dpi_matches": result["primary_return"]["dpi"] == expected_primary_dpi,
        "dwm_rounded": (
            result["dwm"]["corner_query_hresult"] == 0
            and result["dwm"]["corner_preference"] == 2
        ),
        "no_hard_window_region": result["dwm"]["window_region_type"] == 0,
        "minimize_restore": (
            result["minimize_restore"]["minimized"]
            and result["minimize_restore"]["restored"]
        ),
        "maximize_restore": (
            result["maximized"]["is_zoomed"]
            and result["restore_after_maximize"]["dpi"] == expected_primary_dpi
        ),
    }
    if expected_secondary_dpi is not None:
        checks["secondary_dpi_matches"] = (
            result["secondary_restored"]["dpi"] == expected_secondary_dpi
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--expected-primary-dpi", type=int, required=True)
    parser.add_argument("--primary-x", type=int, default=0)
    parser.add_argument("--primary-y", type=int, default=0)
    parser.add_argument("--primary-width", type=int, default=3840)
    parser.add_argument("--primary-height", type=int, default=2088)
    parser.add_argument("--secondary-x", type=int, default=3840)
    parser.add_argument("--secondary-y", type=int, default=420)
    parser.add_argument("--secondary-width", type=int, default=2560)
    parser.add_argument("--secondary-height", type=int, default=1392)
    parser.add_argument("--secondary-dpi", type=int, default=96)
    parser.add_argument(
        "--skip-secondary",
        action="store_true",
        help="Verify only the active primary display and record that cross-monitor coverage was skipped",
    )
    args = parser.parse_args()

    executable = Path(args.exe).resolve()
    data_dir = Path(args.data_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not executable.is_file() or not data_dir.is_dir():
        raise SystemExit("Executable or data directory does not exist")
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output_dir}")
    if candidate_processes(executable):
        raise SystemExit("Candidate is already running")
    output_dir.mkdir(parents=True)

    log_path = data_dir / "app.log"
    offset = log_path.stat().st_size if log_path.exists() else 0
    env = os.environ.copy()
    env["PRODUCT_ATELIER_DATA_DIR"] = str(data_dir)
    process = subprocess.Popen([str(executable)], cwd=str(executable.parent), env=env)
    hwnd: int | None = None
    result: dict[str, object] = {
        "label": args.label,
        "screenshots": {},
        "coverage": {
            "primary_monitor_verified": True,
            "secondary_monitor_verified": not args.skip_secondary,
            "cross_monitor_transition_verified": not args.skip_secondary,
            "secondary_skip_reason": (
                "No active secondary display was available for this requested run"
                if args.skip_secondary else None
            ),
        },
    }
    try:
        if not wait_until(lambda: find_process_window(process.pid) is not None, 15):
            raise RuntimeError("Candidate window did not become visible")
        hwnd = find_process_window(process.pid)[0]
        deadline = time.perf_counter() + 20
        log_text = ""
        while time.perf_counter() < deadline:
            if log_path.exists():
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    log_text = handle.read()
                if "Startup milestone: workspace-ready" in log_text:
                    break
            if process.poll() is not None:
                raise RuntimeError(f"Candidate exited early with code {process.returncode}")
            time.sleep(0.05)
        else:
            raise RuntimeError("workspace-ready was not logged")

        corner_preference = wintypes.DWORD()
        corner_hresult = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner_preference),
            ctypes.sizeof(corner_preference),
        )
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            region_type = int(user32.GetWindowRgn(hwnd, region))
        finally:
            gdi32.DeleteObject(region)
        result["dwm"] = {
            "corner_query_hresult": int(corner_hresult),
            "corner_preference": int(corner_preference.value),
            "window_region_type": region_type,
        }

        logical_width, logical_height = 1280, 800
        primary_width = round(logical_width * args.expected_primary_dpi / 96)
        primary_height = round(logical_height * args.expected_primary_dpi / 96)
        primary_rect = (
            args.primary_x + max(0, (args.primary_width - primary_width) // 2),
            args.primary_y + max(0, (args.primary_height - primary_height) // 2),
            primary_width,
            primary_height,
        )
        actual_primary = move_window(hwnd, process.pid, primary_rect)
        primary_dpi = int(user32.GetDpiForWindow(hwnd))
        result["primary_restored"] = {
            "dpi": primary_dpi,
            "rect": list(actual_primary),
        }
        result["screenshots"]["primary_restored"] = capture(
            actual_primary, output_dir / f"{args.label}-primary-restored.png"
        )

        user32.ShowWindow(hwnd, SW_MINIMIZE)
        minimized = wait_until(lambda: bool(user32.IsIconic(hwnd)))
        user32.ShowWindow(hwnd, SW_RESTORE)
        restored_from_minimize = wait_until(lambda: not bool(user32.IsIconic(hwnd)))
        result["minimize_restore"] = {
            "minimized": minimized,
            "restored": restored_from_minimize,
        }

        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        maximized = wait_until(lambda: bool(user32.IsZoomed(hwnd)))
        time.sleep(0.4)
        maximized_rect = window_rect(process.pid)
        result["maximized"] = {
            "is_zoomed": maximized,
            "dpi": int(user32.GetDpiForWindow(hwnd)),
            "rect": list(maximized_rect),
        }
        result["screenshots"]["maximized"] = capture(
            maximized_rect, output_dir / f"{args.label}-primary-maximized.png"
        )

        user32.ShowWindow(hwnd, SW_RESTORE)
        wait_until(lambda: not bool(user32.IsZoomed(hwnd)))
        actual_primary_after_restore = move_window(hwnd, process.pid, primary_rect)
        result["restore_after_maximize"] = {
            "dpi": int(user32.GetDpiForWindow(hwnd)),
            "rect": list(actual_primary_after_restore),
        }

        if not args.skip_secondary:
            secondary_width = round(logical_width * args.secondary_dpi / 96)
            secondary_height = round(logical_height * args.secondary_dpi / 96)
            secondary_rect = (
                args.secondary_x + max(0, (args.secondary_width - secondary_width) // 2),
                args.secondary_y + max(0, (args.secondary_height - secondary_height) // 2),
                secondary_width,
                secondary_height,
            )
            actual_secondary = move_window(hwnd, process.pid, secondary_rect)
            result["secondary_restored"] = {
                "dpi": int(user32.GetDpiForWindow(hwnd)),
                "rect": list(actual_secondary),
            }
            result["screenshots"]["secondary_restored"] = capture(
                actual_secondary, output_dir / f"{args.label}-secondary-restored.png"
            )

        actual_primary_return = move_window(hwnd, process.pid, primary_rect)
        result["primary_return"] = {
            "dpi": int(user32.GetDpiForWindow(hwnd)),
            "rect": list(actual_primary_return),
        }
        result["screenshots"]["primary_return"] = capture(
            actual_primary_return, output_dir / f"{args.label}-primary-return.png"
        )

        result["checks"] = build_window_checks(
            result,
            expected_primary_dpi=args.expected_primary_dpi,
            expected_secondary_dpi=None if args.skip_secondary else args.secondary_dpi,
        )
        result["passed"] = all(result["checks"].values())
        (output_dir / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    finally:
        stop_process_tree(process, hwnd)


if __name__ == "__main__":
    raise SystemExit(main())
