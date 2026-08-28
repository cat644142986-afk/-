#!/usr/bin/env python3
"""Record and analyse a DPI-aware Windows application startup at fixed FPS."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import psutil


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HALFTONE = 4
WM_CLOSE = 0x0010
SW_RESTORE = 9
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040

try:
    user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except Exception:
    user32.SetProcessDPIAware()


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def find_process_window(process_id: int) -> tuple[int, tuple[int, int, int, int]] | None:
    found: list[tuple[int, tuple[int, int, int, int]]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != process_id:
            return True
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width > 100 and height > 100:
                found.append((hwnd, (rect.left, rect.top, width, height)))
        return True

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(callback_type(callback), 0)
    return found[0] if found else None


class WindowCapture:
    def __init__(self, rect: tuple[int, int, int, int], scale: float):
        self.x, self.y, self.source_width, self.source_height = rect
        self.width = max(2, int(round(self.source_width * scale)) // 2 * 2)
        self.height = max(2, int(round(self.source_height * scale)) // 2 * 2)
        self.screen_dc = user32.GetDC(0)
        self.memory_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.bitmap = gdi32.CreateCompatibleBitmap(self.screen_dc, self.width, self.height)
        self.old_bitmap = gdi32.SelectObject(self.memory_dc, self.bitmap)
        gdi32.SetStretchBltMode(self.memory_dc, HALFTONE)
        self.buffer = (ctypes.c_ubyte * (self.width * self.height * 4))()
        self.header = BitmapInfoHeader()
        self.header.biSize = ctypes.sizeof(BitmapInfoHeader)
        self.header.biWidth = self.width
        self.header.biHeight = -self.height
        self.header.biPlanes = 1
        self.header.biBitCount = 32

    def frame(self) -> np.ndarray:
        ok = gdi32.StretchBlt(
            self.memory_dc,
            0,
            0,
            self.width,
            self.height,
            self.screen_dc,
            self.x,
            self.y,
            self.source_width,
            self.source_height,
            SRCCOPY | CAPTUREBLT,
        )
        if not ok:
            raise RuntimeError("StretchBlt failed")
        lines = gdi32.GetDIBits(
            self.memory_dc,
            self.bitmap,
            0,
            self.height,
            self.buffer,
            ctypes.byref(self.header),
            0,
        )
        if lines != self.height:
            raise RuntimeError("GetDIBits returned an incomplete frame")
        bgra = np.ctypeslib.as_array(self.buffer).reshape(self.height, self.width, 4)
        return np.ascontiguousarray(bgra[:, :, :3])

    def close(self) -> None:
        gdi32.SelectObject(self.memory_dc, self.old_bitmap)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.memory_dc)
        user32.ReleaseDC(0, self.screen_dc)


def stop_process_tree(process: subprocess.Popen[bytes], hwnd: int | None) -> None:
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
    except psutil.Error:
        children = []
    if hwnd:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    for child in children:
        try:
            if child.is_running():
                child.terminate()
                child.wait(timeout=3)
        except (psutil.Error, psutil.TimeoutExpired):
            try:
                child.kill()
            except psutil.Error:
                pass


def analyse_video(path: Path) -> tuple[list[dict[str, float | int]], np.ndarray, np.ndarray]:
    reader = cv2.VideoCapture(str(path))
    if not reader.isOpened():
        raise RuntimeError(f"OpenCV could not read the recorded video: {path}")
    metrics: list[dict[str, float | int]] = []
    first_frame: np.ndarray | None = None
    darkest_frame: np.ndarray | None = None
    darkest_mean = float("inf")
    fps = float(reader.get(cv2.CAP_PROP_FPS) or 0)
    try:
        index = 0
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_luma = float(gray.mean())
            black_fraction = float(np.count_nonzero(gray <= 8) / gray.size)
            metrics.append({
                "index": index,
                "at_ms": round(index * 1000 / fps, 3) if fps else 0,
                "mean_luma": round(mean_luma, 3),
                "black_fraction": round(black_fraction, 6),
            })
            if first_frame is None:
                first_frame = frame.copy()
            if mean_luma < darkest_mean:
                darkest_mean = mean_luma
                darkest_frame = frame.copy()
            index += 1
    finally:
        reader.release()
    if first_frame is None or darkest_frame is None:
        raise RuntimeError("Recorded video contained no decodable frames")
    return metrics, first_frame, darkest_frame


def save_analysis_artifacts(
    output: Path,
    summary: dict,
    frame_metrics: list[dict[str, float | int]],
    first_frame: np.ndarray,
    darkest_frame: np.ndarray,
) -> None:
    stem = output.with_suffix("")
    cv2.imwrite(str(stem.with_name(stem.name + "-first.png")), first_frame)
    cv2.imwrite(str(stem.with_name(stem.name + "-darkest.png")), darkest_frame)
    output.with_suffix(".json").write_text(
        json.dumps({"summary": summary, "frames": frame_metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--ffmpeg", help="Use gdigrab through this ffmpeg executable")
    parser.add_argument("--crop-width", type=int, default=960)
    parser.add_argument("--crop-height", type=int, default=600)
    parser.add_argument("--move-x", type=int)
    parser.add_argument("--move-y", type=int)
    parser.add_argument("--move-width", type=int)
    parser.add_argument("--move-height", type=int)
    args = parser.parse_args()

    exe = Path(args.exe).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = Path(args.output).resolve()
    if not exe.is_file():
        raise SystemExit(f"Executable not found: {exe}")
    if not data_dir.is_dir():
        raise SystemExit(f"Data directory not found: {data_dir}")
    if args.fps <= 0 or args.duration <= 0 or not 0 < args.scale <= 1:
        raise SystemExit("fps, duration, and scale must be positive")
    if (args.move_x is None) != (args.move_y is None):
        raise SystemExit("move-x and move-y must be provided together")
    if (args.move_width is None) != (args.move_height is None):
        raise SystemExit("move-width and move-height must be provided together")
    if args.move_width is not None and (args.move_x is None or args.move_width <= 0 or args.move_height <= 0):
        raise SystemExit("move size requires positive move coordinates and dimensions")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite an existing recording: {output}")
    ffmpeg = Path(args.ffmpeg).resolve() if args.ffmpeg else None
    if ffmpeg and not ffmpeg.is_file():
        raise SystemExit(f"ffmpeg executable not found: {ffmpeg}")
    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PRODUCT_ATELIER_DATA_DIR"] = str(data_dir)
    process = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    hwnd: int | None = None
    capture: WindowCapture | None = None
    writer: cv2.VideoWriter | None = None
    first_frame: np.ndarray | None = None
    darkest_frame: np.ndarray | None = None
    darkest_mean = float("inf")
    frame_metrics: list[dict[str, float | int]] = []
    capture_times: list[float] = []
    window_visible_after_ms = 0.0

    launched_at = time.perf_counter()
    try:
        deadline = launched_at + 15
        found = None
        while time.perf_counter() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Application exited early with code {process.returncode}")
            found = find_process_window(process.pid)
            if found:
                break
            time.sleep(0.002)
        if not found:
            raise RuntimeError("Application window did not become visible within 15 seconds")
        hwnd, rect = found
        window_visible_after_ms = (time.perf_counter() - launched_at) * 1000
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if args.move_x is not None and args.move_y is not None:
            move_deadline = time.perf_counter() + 2
            moved = None
            move_result = False
            while time.perf_counter() < move_deadline:
                move_flags = SWP_SHOWWINDOW
                move_width = args.move_width or 0
                move_height = args.move_height or 0
                if args.move_width is None:
                    move_flags |= SWP_NOSIZE
                move_result = bool(user32.SetWindowPos(
                    hwnd,
                    wintypes.HWND(HWND_TOPMOST),
                    args.move_x,
                    args.move_y,
                    move_width,
                    move_height,
                    move_flags,
                ))
                time.sleep(0.05)
                moved = find_process_window(process.pid)
                position_ok = moved and abs(moved[1][0] - args.move_x) <= 64
                size_ok = bool(moved) and (
                    args.move_width is None
                    or (
                        abs(moved[1][2] - args.move_width) <= 64
                        and abs(moved[1][3] - args.move_height) <= 64
                    )
                )
                if position_ok and size_ok:
                    break
            if not moved or not position_ok or not size_ok:
                raise RuntimeError(
                    "Application window did not settle on the requested monitor: "
                    f"set_window_pos={move_result}, actual={moved[1] if moved else None}, "
                    f"requested={(args.move_x, args.move_y, args.move_width, args.move_height)}"
                )
            hwnd, rect = moved
            # Let DWM present the resized surface on the destination monitor;
            # GetWindowRect can settle one composition frame earlier.
            time.sleep(0.25)
        user32.SetWindowPos(
            hwnd,
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )

        if ffmpeg:
            crop_width = min(args.crop_width, rect[2]) // 2 * 2
            crop_height = min(args.crop_height, rect[3]) // 2 * 2
            offset_x = rect[0] + (rect[2] - crop_width) // 2
            offset_y = rect[1] + (rect[3] - crop_height) // 2
            completed = subprocess.run(
                [
                    str(ffmpeg), "-y", "-hide_banner",
                    "-f", "gdigrab", "-framerate", str(args.fps),
                    "-offset_x", str(offset_x), "-offset_y", str(offset_y),
                    "-video_size", f"{crop_width}x{crop_height}", "-i", "desktop",
                    "-t", str(args.duration), "-r", str(args.fps),
                    "-c:v", "h264_nvenc", "-preset", "p1",
                    "-pix_fmt", "yuv420p", str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=args.duration + 15,
                check=False,
            )
            output.with_suffix(".log").write_text(completed.stderr, encoding="utf-8")
            if completed.returncode != 0:
                raise RuntimeError(f"ffmpeg failed with code {completed.returncode}")
            frame_counters = re.findall(r"frame=\s*(\d+)", completed.stderr)
            if not frame_counters:
                raise RuntimeError("ffmpeg log did not contain frame counters")
            duplicate_counters = re.findall(r"dup=(\d+)", completed.stderr)
            drop_counters = re.findall(r"drop=(\d+)", completed.stderr)
            frame_count = int(frame_counters[-1])
            duplicate_count = int(duplicate_counters[-1]) if duplicate_counters else 0
            drop_count = int(drop_counters[-1]) if drop_counters else 0
            frame_metrics, first_frame, darkest_frame = analyse_video(output)
            dark_frames = [
                item for item in frame_metrics
                if float(item["mean_luma"]) < 10 or float(item["black_fraction"]) > 0.98
            ]
            darkest_mean = min(float(item["mean_luma"]) for item in frame_metrics)
            summary = {
                "executable": str(exe),
                "data_dir": str(data_dir),
                "video": str(output),
                "capture_backend": "ffmpeg-gdigrab-h264_nvenc",
                "requested_fps": args.fps,
                "captured_frames": frame_count,
                "decoded_frames": len(frame_metrics),
                "duplicate_frames": duplicate_count,
                "drop_frames": drop_count,
                "independent_frames": frame_count - duplicate_count,
                "independent_fps": round((frame_count - duplicate_count) / args.duration, 3),
                "duration_seconds": args.duration,
                "window_visible_after_ms": round(window_visible_after_ms, 3),
                "source_window": {
                    "x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]
                },
                "capture_region": {
                    "x": offset_x, "y": offset_y,
                    "width": crop_width, "height": crop_height,
                },
                "dark_frame_count": len(dark_frames),
                "darkest_mean_luma": round(darkest_mean, 3),
                "maximum_black_fraction": max(
                    (float(item["black_fraction"]) for item in frame_metrics), default=0
                ),
            }
            save_analysis_artifacts(
                output, summary, frame_metrics, first_frame, darkest_frame
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        capture = WindowCapture(rect, args.scale)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(output), fourcc, args.fps, (capture.width, capture.height)
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV could not open the MP4 writer")

        total_frames = int(round(args.duration * args.fps))
        recording_started = time.perf_counter()
        for frame_index in range(total_frames):
            target = recording_started + frame_index / args.fps
            remaining = target - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            captured_at = time.perf_counter()
            frame = capture.frame()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_luma = float(gray.mean())
            black_fraction = float(np.count_nonzero(gray <= 8) / gray.size)
            writer.write(frame)
            capture_times.append(captured_at)
            frame_metrics.append({
                "index": frame_index,
                "at_ms": round((captured_at - recording_started) * 1000, 3),
                "mean_luma": round(mean_luma, 3),
                "black_fraction": round(black_fraction, 6),
            })
            if first_frame is None:
                first_frame = frame.copy()
            if mean_luma < darkest_mean:
                darkest_mean = mean_luma
                darkest_frame = frame.copy()

        actual_span = capture_times[-1] - capture_times[0] if len(capture_times) > 1 else 0
        achieved_fps = (len(capture_times) - 1) / actual_span if actual_span else 0.0
        intervals = [
            (capture_times[index] - capture_times[index - 1]) * 1000
            for index in range(1, len(capture_times))
        ]
        dark_frames = [
            item for item in frame_metrics
            if float(item["mean_luma"]) < 10 or float(item["black_fraction"]) > 0.98
        ]
        summary = {
            "executable": str(exe),
            "data_dir": str(data_dir),
            "video": str(output),
            "requested_fps": args.fps,
            "captured_frames": len(frame_metrics),
            "duration_seconds": args.duration,
            "window_visible_after_ms": round(window_visible_after_ms, 3),
            "source_window": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]},
            "encoded_size": {"width": capture.width, "height": capture.height},
            "achieved_capture_fps": round(achieved_fps, 3),
            "interval_ms": {
                "min": round(min(intervals), 3) if intervals else 0,
                "average": round(sum(intervals) / len(intervals), 3) if intervals else 0,
                "max": round(max(intervals), 3) if intervals else 0,
            },
            "dark_frame_count": len(dark_frames),
            "darkest_mean_luma": round(darkest_mean, 3),
            "maximum_black_fraction": max(
                (float(item["black_fraction"]) for item in frame_metrics), default=0
            ),
        }
        save_analysis_artifacts(
            output, summary, frame_metrics, first_frame, darkest_frame
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        if writer is not None:
            writer.release()
        if capture is not None:
            capture.close()
        stop_process_tree(process, hwnd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
