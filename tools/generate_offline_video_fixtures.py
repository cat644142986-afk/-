"""Generate tiny deterministic WebM fixtures with the installed Edge runtime.

The files exercise Product Atelier's offline image-to-video task and media
pipeline. They are not model output and must never be presented as such.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "python" / "video_fixtures" / "offline-preview-v1"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
SPECS = {
    "1x1": (320, 320),
    "16x9": (320, 180),
    "9x16": (180, 320),
    "4x3": (320, 240),
    "3x4": (240, 320),
}
DURATIONS = (3, 5, 8, 10)

_INFO_ID = b"\x15\x49\xa9\x66"
_TIMECODE_SCALE_ID = b"\x2a\xd7\xb1"
_DURATION_ID = b"\x44\x89"


def _read_vint(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data) or data[offset] == 0:
        raise ValueError("invalid EBML variable-length integer")
    width = 1
    marker = 0x80
    while width <= 8 and not data[offset] & marker:
        marker >>= 1
        width += 1
    if width > 8 or offset + width > len(data):
        raise ValueError("truncated EBML variable-length integer")
    value = data[offset] & (marker - 1)
    for byte in data[offset + 1 : offset + width]:
        value = (value << 8) | byte
    if value == (1 << (7 * width)) - 1:
        raise ValueError("unknown EBML size is not valid for this element")
    return value, width


def _encode_vint(value: int, width: int) -> bytes:
    limit = (1 << (7 * width)) - 1
    if value < 0 or value >= limit:
        raise ValueError("EBML element size does not fit its existing width")
    encoded = value | (1 << (7 * width))
    return encoded.to_bytes(width, "big")


def _read_unsigned_element(payload: bytes, element_id: bytes, default: int) -> int:
    offset = payload.find(element_id)
    if offset < 0:
        return default
    size, width = _read_vint(payload, offset + len(element_id))
    start = offset + len(element_id) + width
    end = start + size
    if size < 1 or size > 8 or end > len(payload):
        raise ValueError("invalid unsigned EBML element")
    return int.from_bytes(payload[start:end], "big")


def inject_webm_duration(data: bytes, seconds: int) -> bytes:
    """Add a finite Matroska Duration without transcoding the VP8 fixture."""
    info_offset = data.find(_INFO_ID)
    if info_offset < 0:
        raise ValueError("WebM Info element is missing")
    size_offset = info_offset + len(_INFO_ID)
    info_size, size_width = _read_vint(data, size_offset)
    payload_start = size_offset + size_width
    payload_end = payload_start + info_size
    if payload_end > len(data):
        raise ValueError("WebM Info element is truncated")
    info_payload = data[payload_start:payload_end]
    if _DURATION_ID in info_payload:
        raise ValueError("WebM fixture already contains a Duration element")
    timecode_scale = _read_unsigned_element(
        info_payload,
        _TIMECODE_SCALE_ID,
        1_000_000,
    )
    if timecode_scale <= 0:
        raise ValueError("WebM TimecodeScale must be positive")
    duration_units = float(seconds) * 1_000_000_000 / timecode_scale
    duration_element = _DURATION_ID + b"\x88" + struct.pack(">d", duration_units)
    size_bytes = _encode_vint(info_size + len(duration_element), size_width)
    return (
        data[:size_offset]
        + size_bytes
        + info_payload
        + duration_element
        + data[payload_end:]
    )


def read_webm_duration_seconds(data: bytes) -> float:
    info_offset = data.find(_INFO_ID)
    if info_offset < 0:
        raise ValueError("WebM Info element is missing")
    size_offset = info_offset + len(_INFO_ID)
    info_size, size_width = _read_vint(data, size_offset)
    payload_start = size_offset + size_width
    info_payload = data[payload_start : payload_start + info_size]
    timecode_scale = _read_unsigned_element(
        info_payload,
        _TIMECODE_SCALE_ID,
        1_000_000,
    )
    duration_offset = info_payload.find(_DURATION_ID)
    if duration_offset < 0:
        raise ValueError("WebM Duration element is missing")
    duration_size, duration_width = _read_vint(
        info_payload,
        duration_offset + len(_DURATION_ID),
    )
    duration_start = duration_offset + len(_DURATION_ID) + duration_width
    duration_end = duration_start + duration_size
    raw_duration = info_payload[duration_start:duration_end]
    if duration_size == 4:
        duration_units = struct.unpack(">f", raw_duration)[0]
    elif duration_size == 8:
        duration_units = struct.unpack(">d", raw_duration)[0]
    else:
        raise ValueError("WebM Duration must be a 32-bit or 64-bit float")
    duration_seconds = duration_units * timecode_scale / 1_000_000_000
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("WebM Duration must be finite and positive")
    return duration_seconds


def page() -> bytes:
    specs = json.dumps(SPECS, separators=(",", ":"))
    durations = json.dumps(DURATIONS)
    return f"""<!doctype html><meta charset=utf-8><title>fixture generator</title>
<script>
const specs = {specs};
const durations = {durations};

async function makeFixture(slug, size, seconds) {{
  const [width, height] = size;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  const stream = canvas.captureStream(8);
  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8')
    ? 'video/webm;codecs=vp8'
    : 'video/webm';
  const recorder = new MediaRecorder(stream, {{ mimeType, videoBitsPerSecond: 90000 }});
  const chunks = [];
  recorder.ondataavailable = (event) => {{ if (event.data.size) chunks.push(event.data); }};
  const stopped = new Promise((resolve) => recorder.onstop = resolve);
  recorder.start(250);
  const started = performance.now();
  await new Promise((resolve) => {{
    function draw(now) {{
      const elapsed = Math.min(seconds, (now - started) / 1000);
      const phase = elapsed / seconds;
      context.fillStyle = '#f4f0e9';
      context.fillRect(0, 0, width, height);
      const radius = Math.max(18, Math.min(width, height) * 0.12);
      const travel = Math.max(0, width - radius * 3);
      context.fillStyle = '#c85f3b';
      context.beginPath();
      context.arc(radius * 1.5 + travel * phase, height / 2, radius, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = '#3f3b37';
      context.font = `${{Math.max(12, Math.round(Math.min(width, height) * 0.06))}}px sans-serif`;
      context.textAlign = 'center';
      context.fillText('PRODUCT ATELIER', width / 2, height - Math.max(14, height * 0.08));
      if (elapsed >= seconds) return resolve();
      requestAnimationFrame(draw);
    }}
    requestAnimationFrame(draw);
  }});
  recorder.stop();
  await stopped;
  stream.getTracks().forEach((track) => track.stop());
  const blob = new Blob(chunks, {{ type: 'video/webm' }});
  const fixtureUrl = `/fixture/${{slug}}/${{seconds}}s.webm`;
  const response = await fetch(fixtureUrl, {{ method: 'POST', body: blob }});
  if (!response.ok) throw new Error(await response.text());
  const patchedResponse = await fetch(fixtureUrl);
  if (!patchedResponse.ok) throw new Error(await patchedResponse.text());
  const patchedBlob = await patchedResponse.blob();
  const mediaUrl = URL.createObjectURL(patchedBlob);
  const video = document.createElement('video');
  video.preload = 'auto';
  video.src = mediaUrl;
  await new Promise((resolve, reject) => {{
    video.onloadedmetadata = resolve;
    video.onerror = () => reject(new Error(`unable to read ${{fixtureUrl}} metadata`));
  }});
  const browserDuration = video.duration;
  if (!Number.isFinite(browserDuration) || Math.abs(browserDuration - seconds) > 0.05) {{
    throw new Error(`invalid browser duration for ${{fixtureUrl}}: ${{browserDuration}}`);
  }}
  const seekTarget = Math.min(seconds / 2, browserDuration - 0.05);
  let seekSucceeded = false;
  if (seekTarget > 0) {{
    await new Promise((resolve, reject) => {{
      const timer = setTimeout(() => reject(new Error(`seek timed out for ${{fixtureUrl}}`)), 3000);
      video.onseeked = () => {{ clearTimeout(timer); seekSucceeded = true; resolve(); }};
      video.currentTime = seekTarget;
    }});
  }}
  video.removeAttribute('src');
  video.load();
  URL.revokeObjectURL(mediaUrl);
  return {{
    slug,
    seconds,
    width,
    height,
    bytes: patchedBlob.size,
    browserDuration,
    seekSucceeded,
  }};
}}

Promise.all(Object.entries(specs).flatMap(([slug, size]) =>
  durations.map((seconds) => makeFixture(slug, size, seconds))
)).then(async (results) => {{
  await fetch('/done', {{ method: 'POST', body: JSON.stringify(results) }});
  document.title = 'done';
}}).catch(async (error) => {{
  await fetch('/failed', {{ method: 'POST', body: String(error?.stack || error) }});
  document.title = 'failed';
}});
</script>""".encode("utf-8")


class FixtureServer(ThreadingHTTPServer):
    complete = threading.Event()
    error = ""
    metrics: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path.startswith("/fixture/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[1] not in SPECS or parts[2] not in {
                f"{duration}s.webm" for duration in DURATIONS
            }:
                self.send_error(400)
                return
            target = OUTPUT / parts[1] / parts[2]
            if not target.is_file():
                self.send_error(404)
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/webm")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path != "/":
            self.send_error(404)
            return
        content = page()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        path = unquote(urlparse(self.path).path)
        if path.startswith("/fixture/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[1] not in SPECS or parts[2] not in {
                f"{duration}s.webm" for duration in DURATIONS
            }:
                self.send_error(400)
                return
            target = OUTPUT / parts[1] / parts[2]
            target.parent.mkdir(parents=True, exist_ok=True)
            seconds = int(parts[2].removesuffix("s.webm"))
            patched = inject_webm_duration(body, seconds)
            if abs(read_webm_duration_seconds(patched) - seconds) > 0.001:
                self.send_error(500, "fixture duration metadata is invalid")
                return
            target.write_bytes(patched)
            self.send_response(204)
            self.end_headers()
            return
        if path == "/done":
            self.server.metrics = json.loads(body.decode("utf-8"))
            self.server.complete.set()
            self.send_response(204)
            self.end_headers()
            return
        if path == "/failed":
            self.server.error = body.decode("utf-8", errors="replace")
            self.server.complete.set()
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    edge = next((candidate for candidate in EDGE_CANDIDATES if candidate.is_file()), None)
    if edge is None:
        raise SystemExit("Microsoft Edge is required to generate video fixtures")

    server = FixtureServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    process = subprocess.Popen(
        [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--autoplay-policy=no-user-gesture-required",
            f"http://127.0.0.1:{port}/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not server.complete.wait(timeout=max(10, args.timeout)):
            raise SystemExit("timed out while Edge generated video fixtures")
        if server.error:
            raise SystemExit(server.error)
        expected = len(SPECS) * len(DURATIONS)
        files = sorted(OUTPUT.glob("*/*.webm"))
        if len(server.metrics) != expected or len(files) != expected:
            raise SystemExit(f"expected {expected} fixtures, got {len(files)}")
        if any(path.stat().st_size < 1024 for path in files):
            raise SystemExit("one or more generated fixtures are unexpectedly small")
        if any(
            not bool(metric.get("seekSucceeded"))
            or abs(float(metric.get("browserDuration", 0)) - int(metric["seconds"])) > 0.05
            for metric in server.metrics
        ):
            raise SystemExit("one or more fixtures failed the real browser metadata gate")
        manifest = {
            "provider": "offline-preview-v1",
            "fixture_count": len(files),
            "total_bytes": sum(path.stat().st_size for path in files),
            "fixtures": [],
        }
        for path in files:
            slug = path.parent.name
            seconds = int(path.stem.removesuffix("s"))
            content = path.read_bytes()
            width, height = SPECS[slug]
            manifest["fixtures"].append({
                "path": path.relative_to(OUTPUT).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "width": width,
                "height": height,
                "duration_seconds": seconds,
            })
        (OUTPUT / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "fixture_count": len(files),
            "total_bytes": sum(path.stat().st_size for path in files),
            "metrics": server.metrics,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
