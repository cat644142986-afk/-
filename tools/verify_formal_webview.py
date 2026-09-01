#!/usr/bin/env python3
"""Exercise a packaged Product Atelier WebView through its read-only CDP port."""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Any

import psutil
import websocket

SW_RESTORE = 9
SWP_SHOWWINDOW = 0x0040

USER32: Any | None = None
WINTYPES: Any | None = None
FIND_PROCESS_WINDOW: Any | None = None
GET_MONITORS_INFO: Any | None = None

DEFAULT_SIZES = ((960, 600), (1280, 720), (1440, 900))
DEFAULT_PROFILES = ("light", "dark", "high")
DEFAULT_SURFACES = (
    "single",
    "multi-file",
    "group-split",
    "cutout-batch",
    "result-review",
    "task-center",
    "growth",
)

DANGEROUS_SELECTORS = (
    "#btn-generate",
    "#btn-retry",
    "#btn-result-next",
    "#btn-save-all",
    "#btn-adopt",
    "#btn-feedback",
    "#btn-review-adjust",
    "#btn-review-record",
    "#btn-review-suggest",
    "#btn-clear",
    "#btn-new-session",
    "#btn-folder-load",
    "#btn-save-key",
    "#btn-save-settings",
    "#btn-reload-knowledge",
    "#btn-select-output-root",
    "#btn-select-grounding-runtime",
    "#btn-select-grounding-model",
    "#btn-verify-grounding-pack",
    "#btn-disable-grounding-pack",
    "#asset-purge-action",
    "#asset-bulk-action",
    "[data-remove-asset-id]",
    "[data-memory-action]",
    "[data-job-action='retry-item']",
    "[data-job-action='retry-failed']",
    "[data-job-action='pause']",
    "[data-job-action='resume']",
    "[data-job-action='cancel']",
    "[data-purge-asset]",
)

WINDOWS_VIRTUAL_KEYS = {
    "Tab": 9,
    "Enter": 13,
    "Escape": 27,
    "Home": 36,
    "ArrowLeft": 37,
    "ArrowRight": 39,
    "End": 35,
}

KEY_CHARACTER_TEXT = {
    "Enter": "\r",
}


class VerificationError(RuntimeError):
    pass


def load_windows_runtime() -> None:
    """Load Win32-only helpers lazily so this module remains importable on macOS."""
    if os.name != "nt":
        raise VerificationError("Formal WebView verification is available on Windows only")

    global USER32, WINTYPES, FIND_PROCESS_WINDOW, GET_MONITORS_INFO
    if USER32 is not None:
        return

    from ctypes import windll, wintypes

    from capture_startup_video import find_process_window
    from screenshot import get_monitors_info

    USER32 = windll.user32
    WINTYPES = wintypes
    FIND_PROCESS_WINDOW = find_process_window
    GET_MONITORS_INFO = get_monitors_info
    USER32.GetDpiForWindow.argtypes = [WINTYPES.HWND]
    USER32.GetDpiForWindow.restype = WINTYPES.UINT


class CdpClient:
    def __init__(self, websocket_url: str, timeout: float = 12.0) -> None:
        self._socket = websocket.create_connection(
            websocket_url,
            timeout=timeout,
            suppress_origin=True,
        )
        self._next_id = 1
        self.events: list[dict[str, Any]] = []

    def close(self) -> None:
        self._socket.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._socket.send(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
        }))
        while True:
            message = json.loads(self._socket.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise VerificationError(f"CDP {method} failed: {message['error']}")
                return message.get("result") or {}
            if "method" in message:
                self.events.append(message)

    def evaluate(self, expression: str, *, await_promise: bool = True) -> Any:
        response = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True,
        })
        if response.get("exceptionDetails"):
            details = response["exceptionDetails"]
            raise VerificationError(details.get("text") or "WebView JavaScript evaluation failed")
        remote = response.get("result") or {}
        if remote.get("subtype") == "error":
            raise VerificationError(remote.get("description") or "WebView returned an error")
        return remote.get("value")

    def press_key(self, key: str, code: str | None = None) -> None:
        params = key_event_params(key, code)
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **params})
        if key in KEY_CHARACTER_TEXT:
            text = KEY_CHARACTER_TEXT[key]
            self.call("Input.dispatchKeyEvent", {
                "type": "char",
                "text": text,
                "unmodifiedText": text,
                **params,
            })
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **params})

    def screenshot(self, destination: Path) -> dict[str, Any]:
        response = self.call("Page.captureScreenshot", {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
        })
        image_bytes = base64.b64decode(response["data"])
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise VerificationError("CDP screenshot was not a PNG")
        destination.write_bytes(image_bytes)
        dimensions = self.evaluate(
            "({width: innerWidth, height: innerHeight, dpr: devicePixelRatio})"
        )
        return {"path": str(destination), "bytes": len(image_bytes), **dimensions}


def parse_size(raw: str) -> tuple[int, int]:
    parts = raw.lower().split("x", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must use WIDTHxHEIGHT")
    try:
        width, height = (int(value) for value in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must use integer dimensions") from exc
    if width < 800 or height < 500:
        raise argparse.ArgumentTypeError("size is below the supported desktop test floor")
    return width, height


def key_event_params(key: str, code: str | None = None) -> dict[str, Any]:
    virtual_key = WINDOWS_VIRTUAL_KEYS.get(key)
    if virtual_key is None:
        raise VerificationError(f"Unsupported keyboard key: {key}")
    return {
        "key": key,
        "code": code or key,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }


def matrix_cases(
    sizes: tuple[tuple[int, int], ...],
    profiles: tuple[str, ...],
    surfaces: tuple[str, ...],
) -> list[tuple[tuple[int, int], str, str]]:
    return [
        (size, profile, surface)
        for size in sizes
        for profile in profiles
        for surface in surfaces
    ]


def locate_page(cdp_port: int) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=5) as response:
        targets = json.load(response)
    pages = [
        target for target in targets
        if target.get("type") == "page"
        and str(target.get("url") or "").startswith("http://tauri.localhost")
    ]
    if len(pages) != 1:
        raise VerificationError(f"Expected one Tauri WebView target, found {len(pages)}")
    return pages[0]


def wait_for(client: CdpClient, expression: str, timeout: float = 12.0) -> Any:
    deadline = time.perf_counter() + timeout
    last_value: Any = None
    while time.perf_counter() < deadline:
        last_value = client.evaluate(expression)
        if last_value:
            return last_value
        time.sleep(0.1)
    raise VerificationError(f"Timed out waiting for WebView condition: {expression}; last={last_value!r}")


def click(client: CdpClient, selector: str) -> dict[str, Any]:
    encoded = json.dumps(selector)
    result = client.evaluate(f"""
      (() => {{
        const selector = {encoded};
        const element = document.querySelector(selector);
        if (!element) return {{ok: false, reason: 'missing', selector}};
        if (element.closest('[hidden]')) return {{ok: false, reason: 'hidden', selector}};
        element.click();
        return {{ok: true, id: element.id || '', text: (element.textContent || '').trim().slice(0, 80)}};
      }})()
    """)
    if not result.get("ok"):
        raise VerificationError(f"Could not click {selector}: {result}")
    return result


def current_window(process_id: int) -> tuple[int, tuple[int, int, int, int]]:
    load_windows_runtime()
    found = FIND_PROCESS_WINDOW(process_id)
    if not found:
        raise VerificationError(f"No visible window found for process {process_id}")
    return found


def move_window(
    process_id: int,
    monitor: dict[str, Any],
    logical_size: tuple[int, int],
    expected_dpi: int,
) -> dict[str, Any]:
    load_windows_runtime()
    hwnd, _ = current_window(process_id)
    work_x, work_y, work_width, work_height = monitor["work"]
    physical_width = round(logical_size[0] * expected_dpi / 96)
    physical_height = round(logical_size[1] * expected_dpi / 96)
    if physical_width > work_width or physical_height > work_height:
        raise VerificationError(
            f"{logical_size[0]}x{logical_size[1]} at {expected_dpi} DPI does not fit monitor work area"
        )
    x = work_x + (work_width - physical_width) // 2
    y = work_y + (work_height - physical_height) // 2
    USER32.ShowWindow(hwnd, SW_RESTORE)
    if not USER32.SetWindowPos(
        hwnd,
        WINTYPES.HWND(0),
        x,
        y,
        physical_width,
        physical_height,
        SWP_SHOWWINDOW,
    ):
        raise OSError("SetWindowPos failed")
    USER32.SetForegroundWindow(hwnd)
    deadline = time.perf_counter() + 5
    actual = current_window(process_id)[1]
    while time.perf_counter() < deadline:
        actual = current_window(process_id)[1]
        if abs(actual[2] - physical_width) <= 32 and abs(actual[3] - physical_height) <= 32:
            break
        time.sleep(0.1)
    actual_dpi = int(USER32.GetDpiForWindow(hwnd))
    if actual_dpi != expected_dpi:
        raise VerificationError(f"Expected {expected_dpi} DPI, window reported {actual_dpi}")
    time.sleep(0.35)
    return {
        "logical_size": list(logical_size),
        "requested_physical_size": [physical_width, physical_height],
        "actual_rect": list(actual),
        "dpi": actual_dpi,
    }


def install_read_only_guard(client: CdpClient) -> dict[str, Any]:
    selectors = json.dumps(DANGEROUS_SELECTORS)
    return client.evaluate(f"""
      (() => {{
        if (window.__productAtelierR9Guard) return window.__productAtelierR9Guard;
        const selectors = {selectors};
        const guard = (event) => {{
          if (selectors.some((selector) => event.target.closest?.(selector))) {{
            event.preventDefault();
            event.stopImmediatePropagation();
          }}
        }};
        document.addEventListener('click', guard, true);
        window.__productAtelierR9Guard = {{installed: true, selectors}};
        return window.__productAtelierR9Guard;
      }})()
    """)


def dismiss_layers(client: CdpClient) -> None:
    client.press_key("Escape")
    client.press_key("Escape")
    time.sleep(0.1)


def open_process(client: CdpClient) -> None:
    dismiss_layers(client)
    click(client, "[data-page='process']")
    wait_for(client, "!document.querySelector('#page-process').hidden")


def open_result_view(client: CdpClient) -> dict[str, Any]:
    open_process(client)
    click(client, "#btn-rail-jobs")
    wait_for(client, "!document.querySelector('#job-drawer').hidden")
    wait_for(client, "document.querySelectorAll('[data-job-action=\"open-results\"]').length > 0")
    result = client.evaluate("""
      (() => {
        const button = document.querySelector('[data-job-action="open-results"]');
        const jobId = button?.dataset.jobId || '';
        button?.click();
        return {ok: Boolean(button), jobId};
      })()
    """)
    if not result.get("ok"):
        raise VerificationError("No historical job with results was available")
    wait_for(client, "!document.querySelector('#canvas-results').hidden", timeout=15)
    wait_for(client, "document.querySelector('#viewer-main-img').complete && document.querySelector('#viewer-main-img').naturalWidth > 0", timeout=15)
    return result


def open_surface(client: CdpClient, surface: str) -> dict[str, Any]:
    if surface in {"single", "multi-file", "group-split", "cutout-batch"}:
        open_process(client)
        click(client, f"[data-mode='{surface}']")
        wait_for(client, f"document.querySelector('[data-mode=\"{surface}\"]').classList.contains('active')")
        return {"surface": surface}
    if surface == "growth":
        dismiss_layers(client)
        click(client, "[data-page='memory']")
        wait_for(client, "!document.querySelector('#page-memory').hidden")
        return {"surface": surface}
    if surface == "task-center":
        open_process(client)
        click(client, "#btn-rail-jobs")
        wait_for(client, "!document.querySelector('#job-drawer').hidden")
        return {"surface": surface}
    if surface == "result-review":
        job = open_result_view(client)
        click(client, "#btn-open-compare")
        wait_for(client, "!document.querySelector('#page-compare').hidden")
        wait_for(client, "!document.querySelector('#compare-view').hidden", timeout=15)
        return {"surface": surface, **job}
    raise VerificationError(f"Unknown surface: {surface}")


def apply_profile(client: CdpClient, profile: str) -> dict[str, Any]:
    settings = {
        "light": ("light", "standard"),
        "dark": ("dark", "standard"),
        "high": ("light", "high"),
    }
    theme, contrast = settings[profile]
    dismiss_layers(client)
    click(client, "[data-page='settings']")
    wait_for(client, "!document.querySelector('#page-settings').hidden")
    result = client.evaluate(f"""
      (() => {{
        const theme = document.querySelector('input[name="appearance-theme"][value="{theme}"]');
        const contrast = document.querySelector('input[name="appearance-contrast"][value="{contrast}"]');
        if (!theme || !contrast) return {{ok: false}};
        theme.click();
        contrast.click();
        return {{
          ok: true,
          theme: document.documentElement.dataset.theme,
          contrast: document.documentElement.dataset.contrast,
        }};
      }})()
    """)
    if not result.get("ok"):
        raise VerificationError(f"Could not apply appearance profile {profile}")
    return result


def dom_snapshot(client: CdpClient) -> dict[str, Any]:
    return client.evaluate("""
      (() => {
        const visible = (element) => element
          && !element.closest('[hidden]')
          && !element.closest('[inert]')
          && element.getClientRects().length > 0;
        const viewport = {width: innerWidth, height: innerHeight, dpr: devicePixelRatio};
        const referencedLabel = (element) => (element.getAttribute('aria-labelledby') || '')
          .split(/\\s+/)
          .filter(Boolean)
          .map((id) => document.getElementById(id)?.textContent || '')
          .join(' ');
        const named = (element) => ([
          element.getAttribute('aria-label'),
          referencedLabel(element),
          [...(element.labels || [])].map((label) => label.textContent || '').join(' '),
          element.closest('label')?.textContent,
          element.querySelector('img[alt]')?.getAttribute('alt'),
          element.getAttribute('title'),
          element.textContent,
        ].find((value) => String(value || '').trim()) || '').trim();
        const visibleControls = [...document.querySelectorAll('button,input,select,textarea,[role="button"],[role="tab"],[role="slider"]')]
          .filter(visible);
        const unnamed = visibleControls.filter((element) => !named(element));
        const positiveTabIndex = visibleControls.filter((element) => element.tabIndex > 0);
        const boundsIssues = [...document.querySelectorAll('.app-page:not([hidden]), .drawer-layer:not([hidden]) .drawer')]
          .filter(visible)
          .map((element) => ({id: element.id || element.className, rect: element.getBoundingClientRect()}))
          .filter(({rect}) => rect.left < -1 || rect.top < -1 || rect.right > innerWidth + 1 || rect.bottom > innerHeight + 1)
          .map(({id, rect}) => ({id, left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom}));
        return {
          viewport,
          profile: {
            theme: document.documentElement.dataset.theme || '',
            contrast: document.documentElement.dataset.contrast || '',
            textScale: document.documentElement.dataset.textScale || '',
            motion: document.documentElement.dataset.motion || '',
          },
          page: document.querySelector('.app-page:not([hidden])')?.dataset.pageName || '',
          drawer: document.querySelector('.drawer-layer:not([hidden])')?.id || '',
          documentOverflowX: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth,
          visibleControlCount: visibleControls.length,
          unnamedControls: unnamed.map((element) => element.id || element.outerHTML.slice(0, 100)),
          positiveTabIndex: positiveTabIndex.map((element) => element.id || element.outerHTML.slice(0, 100)),
          boundsIssues,
          resultImage: {
            visible: visible(document.querySelector('#viewer-main-img')),
            complete: Boolean(document.querySelector('#viewer-main-img')?.complete),
            naturalWidth: document.querySelector('#viewer-main-img')?.naturalWidth || 0,
            naturalHeight: document.querySelector('#viewer-main-img')?.naturalHeight || 0,
          },
        };
      })()
    """)


def focus_snapshot(client: CdpClient) -> dict[str, Any]:
    return client.evaluate("""
      (() => {
        const element = document.activeElement;
        const rect = element?.getBoundingClientRect?.();
        const style = element ? getComputedStyle(element) : null;
        const center = rect ? document.elementFromPoint(
          Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2)),
          Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2)),
        ) : null;
        return {
          tag: element?.tagName || '',
          id: element?.id || '',
          className: typeof element?.className === 'string' ? element.className : '',
          text: (element?.textContent || '').trim().slice(0, 80),
          role: element?.getAttribute?.('role') || '',
          resultTab: element?.dataset?.rtab || '',
          reviewDecision: element?.dataset?.reviewDecision || '',
          visible: Boolean(rect && rect.width && rect.height && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth),
          fullyVisible: Boolean(rect && rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth),
          centerUnobscured: Boolean(element && center && (element === center || element.contains(center) || center.contains(element))),
          outlineWidth: style?.outlineWidth || '',
          outlineStyle: style?.outlineStyle || '',
          ariaSelected: element?.getAttribute?.('aria-selected') || '',
          ariaValueNow: element?.getAttribute?.('aria-valuenow') || '',
        };
      })()
    """)


def run_keyboard_path(client: CdpClient) -> dict[str, Any]:
    report: dict[str, Any] = {}
    job = open_result_view(client)
    report["job_id"] = job["jobId"]
    client.evaluate("document.querySelector('.result-tab[data-rtab=\"main\"]').focus()")
    client.press_key("ArrowRight")
    time.sleep(0.15)
    report["result_tab_arrow_right"] = focus_snapshot(client)
    client.press_key("Home")
    time.sleep(0.15)
    report["result_tab_home"] = focus_snapshot(client)
    client.evaluate("document.querySelector('#btn-open-compare').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#page-compare').hidden")
    client.evaluate("document.querySelector('#btn-compare-help').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#review-guide').hidden")
    report["guide_initial_focus"] = focus_snapshot(client)
    client.press_key("Escape")
    wait_for(client, "document.querySelector('#review-guide').hidden")
    report["guide_focus_restore"] = focus_snapshot(client)
    client.evaluate("document.querySelector('#compare-slider').focus()")
    before = focus_snapshot(client)
    client.press_key("ArrowRight")
    time.sleep(0.15)
    after = focus_snapshot(client)
    report["compare_slider"] = {"before": before, "after": after}
    client.evaluate("document.querySelector('[data-review-decision=\"adjusted\"]').focus()")
    client.press_key("Enter")
    wait_for(client, "!document.querySelector('#review-reason').hidden")
    report["review_decision"] = focus_snapshot(client)
    report["passed"] = all((
        report["result_tab_arrow_right"]["resultTab"] == "cutout",
        report["result_tab_arrow_right"]["ariaSelected"] == "true",
        report["result_tab_home"]["resultTab"] == "main",
        report["guide_initial_focus"]["id"] == "btn-review-guide-done",
        report["guide_focus_restore"]["id"] == "btn-compare-help",
        int(after["ariaValueNow"] or 0) > int(before["ariaValueNow"] or 0),
        all(value["visible"] and value["centerUnobscured"] for value in (
            report["result_tab_arrow_right"],
            report["result_tab_home"],
            report["guide_initial_focus"],
            report["guide_focus_restore"],
            after,
            report["review_decision"],
        )),
    ))
    return report


def classify_process(process: psutil.Process, root_pid: int) -> str:
    name = process.name().lower()
    if process.pid == root_pid:
        return "app_shell"
    if name == "msedgewebview2.exe":
        return "webview2"
    if name == "python-server.exe":
        return "sidecar"
    return "other"


def memory_sample(process_id: int) -> dict[str, int]:
    root = psutil.Process(process_id)
    processes = [root, *root.children(recursive=True)]
    totals = {"app_shell": 0, "webview2": 0, "sidecar": 0, "other": 0, "total": 0}
    for process in processes:
        try:
            working_set = int(process.memory_info().rss)
            totals[classify_process(process, process_id)] += working_set
            totals["total"] += working_set
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return totals


def summarize_memory_samples(samples: list[dict[str, int]]) -> dict[str, Any]:
    if not samples:
        raise VerificationError("No memory samples were collected")
    report: dict[str, Any] = {"sample_count": len(samples), "samples": samples}
    for key in ("app_shell", "webview2", "sidecar", "other", "total"):
        values = [sample[key] for sample in samples]
        report[f"{key}_p50_bytes"] = int(statistics.median(values))
        report[f"{key}_peak_bytes"] = max(values)
    return report


def console_failures(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Runtime.exceptionThrown":
            failures.append({"method": method, "text": params.get("exceptionDetails", {}).get("text", "")})
        elif method == "Log.entryAdded" and params.get("entry", {}).get("level") in {"error", "warning"}:
            entry = params["entry"]
            failures.append({"method": method, "level": entry.get("level"), "text": entry.get("text", "")})
        elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "warning"}:
            failures.append({"method": method, "level": params.get("type")})
    return failures


def main() -> int:
    try:
        load_windows_runtime()
    except VerificationError as exc:
        raise SystemExit(str(exc)) from exc

    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--monitor-index", type=int, required=True)
    parser.add_argument("--expected-dpi", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=parse_size, default=list(DEFAULT_SIZES))
    parser.add_argument("--profiles", nargs="+", choices=DEFAULT_PROFILES, default=list(DEFAULT_PROFILES))
    parser.add_argument("--surfaces", nargs="+", choices=DEFAULT_SURFACES, default=list(DEFAULT_SURFACES))
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output_dir}")
    output_dir.mkdir(parents=True)
    monitors = GET_MONITORS_INFO()
    if args.monitor_index < 0 or args.monitor_index >= len(monitors):
        raise SystemExit(f"Monitor index {args.monitor_index} is unavailable")
    monitor = monitors[args.monitor_index]
    if int(monitor["dpi"]) != args.expected_dpi:
        raise SystemExit(
            f"Monitor {args.monitor_index} reports {monitor['dpi']} DPI, expected {args.expected_dpi}"
        )

    target = locate_page(args.cdp_port)
    client = CdpClient(target["webSocketDebuggerUrl"])
    report: dict[str, Any] = {
        "format_version": 1,
        "target": {key: target.get(key) for key in ("id", "title", "type", "url")},
        "process_id": args.pid,
        "monitor": monitor,
        "expected_dpi": args.expected_dpi,
        "cases": [],
    }
    try:
        client.call("Runtime.enable")
        client.call("Page.enable")
        client.call("Log.enable")
        wait_for(client, "document.readyState === 'complete'")
        wait_for(client, "document.body && !document.querySelector('#boot-screen:not([hidden])')", timeout=20)
        report["read_only_guard"] = install_read_only_guard(client)

        baseline_size = (1280, 720)
        report["keyboard_window"] = move_window(
            args.pid, monitor, baseline_size, args.expected_dpi
        )
        report["keyboard"] = run_keyboard_path(client)

        cases = matrix_cases(tuple(args.sizes), tuple(args.profiles), tuple(args.surfaces))
        for index, (size, profile, surface) in enumerate(cases, start=1):
            window = move_window(args.pid, monitor, size, args.expected_dpi)
            applied_profile = apply_profile(client, profile)
            surface_state = open_surface(client, surface)
            time.sleep(0.3)
            snapshot = dom_snapshot(client)
            filename = (
                f"dpi-{args.expected_dpi}-"
                f"{size[0]}x{size[1]}-{profile}-{surface}.png"
            )
            screenshot = client.screenshot(output_dir / filename)
            case_passed = all((
                snapshot["documentOverflowX"] <= 1,
                not snapshot["unnamedControls"],
                not snapshot["positiveTabIndex"],
                not snapshot["boundsIssues"],
            ))
            report["cases"].append({
                "index": index,
                "size": list(size),
                "profile": profile,
                "surface": surface,
                "window": window,
                "applied_profile": applied_profile,
                "surface_state": surface_state,
                "snapshot": snapshot,
                "screenshot": screenshot,
                "passed": case_passed,
            })

        memory_samples = []
        for _ in range(7):
            memory_samples.append(memory_sample(args.pid))
            time.sleep(0.25)
        report["memory"] = summarize_memory_samples(memory_samples)
        report["console_failures"] = console_failures(client.events)
        report["passed"] = all((
            report["keyboard"]["passed"],
            all(case["passed"] for case in report["cases"]),
            not report["console_failures"],
        ))
    finally:
        client.close()

    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "passed": report["passed"],
        "case_count": len(report["cases"]),
        "failed_cases": [case["index"] for case in report["cases"] if not case["passed"]],
        "keyboard_passed": report["keyboard"]["passed"],
        "console_failure_count": len(report["console_failures"]),
        "memory": {
            key: value for key, value in report["memory"].items()
            if key.endswith("_p50_bytes") or key.endswith("_peak_bytes")
        },
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
