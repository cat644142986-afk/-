#!/usr/bin/env python3
"""
DPI-Aware Screenshot Utility for Product Atelier development.
Handles dual-monitor setups with mixed DPI scaling correctly.

Usage:
  python screenshot.py                          # Capture full virtual screen
  python screenshot.py --window "Product Atelier"  # Capture specific window
  python screenshot.py --window "Product Atelier" --pad 20  # Window with padding
  python screenshot.py --region x y w h         # Capture specific region (physical px)
  python screenshot.py -o path.png              # Save to specific path
  python screenshot.py --monitor N              # Capture specific monitor (0,1)
  python screenshot.py --list-windows           # List visible windows
"""
import sys
import os
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path

# ============================================================
# Win32 API setup with Per-Monitor DPI Awareness V2
# ============================================================
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shcore = ctypes.windll.shcore
kernel32 = ctypes.windll.kernel32

# Enable per-monitor DPI awareness V2 (highest quality)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
try:
    user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except Exception:
    try:
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

# Constants
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
MONITOR_DEFAULTTONEAREST = 2
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# Fix stdout encoding for Chinese
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def get_monitors_info():
    """Get all monitors with physical coordinates and DPI."""
    monitors = []

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", ctypes.c_wchar * 32),
        ]

    def callback(hmon, hdc, lprect, lparam):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        try:
            shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        except Exception:
            dpi_x.value = 96
            dpi_y.value = 96
        r = mi.rcMonitor
        w = mi.rcWork
        monitors.append({
            "hmon": hmon,
            "device": mi.szDevice,
            "bounds": (r.left, r.top, r.right - r.left, r.bottom - r.top),
            "work": (w.left, w.top, w.right - w.left, w.bottom - w.top),
            "primary": bool(mi.dwFlags & 1),
            "dpi": dpi_x.value,
            "scale": dpi_x.value / 96.0,
        })
        return True

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    user32.EnumDisplayMonitors(0, 0, callback_type(callback), 0)
    return monitors


def find_window(title_part):
    """Find window by partial title match. Returns (hwnd, rect, title) or None."""
    results = []

    def callback(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_part.lower() in buf.value.lower():
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    results.append((hwnd, (rect.left, rect.top, rect.right, rect.bottom), buf.value))
        return True

    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(cb(callback), 0)
    return results[0] if results else None


def list_windows():
    """List all visible windows with titles."""
    windows = []
    def callback(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                title = buf.value.encode("ascii", errors="replace").decode("ascii")
                windows.append((hwnd, title, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
        return True
    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(cb(callback), 0)
    return windows


def capture_region(x, y, w, h):
    """Capture a screen region using BitBlt (DPI-aware physical pixels)."""
    hdc_screen = user32.GetDC(0)
    hdc_memory = gdi32.CreateCompatibleDC(hdc_screen)
    hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old_bitmap = gdi32.SelectObject(hdc_memory, hbitmap)

    gdi32.BitBlt(hdc_memory, 0, 0, w, h, hdc_screen, x, y, SRCCOPY | CAPTUREBLT)

    # Create BMP info header
    class BITMAPINFOHEADER(ctypes.Structure):
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

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h  # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0  # BI_RGB

    stride = w * 4
    buf_size = stride * h
    buf = (ctypes.c_ubyte * buf_size)()
    gdi32.GetDIBits(hdc_memory, hbitmap, 0, h, buf, ctypes.byref(bi), 0)

    # Convert BGRA to RGB (or RGBA for PIL)
    gdi32.SelectObject(hdc_memory, old_bitmap)
    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_memory)
    user32.ReleaseDC(0, hdc_screen)

    return bytes(buf), w, h


def save_png(raw_data, w, h, output_path):
    """Save raw BGRA data as PNG using PIL."""
    try:
        from PIL import Image
    except ImportError:
        # Fallback: write BMP
        return save_bmp(raw_data, w, h, output_path)

    # BGRA -> RGBA
    img = Image.frombytes("RGBA", (w, h), raw_data, "raw", "BGRA")
    # Save as RGB for screenshots (no transparency needed for screen capture)
    img_rgb = Image.new("RGB", img.size, (0, 0, 0))
    img_rgb.paste(img, mask=img.split()[3])
    img_rgb.save(str(output_path), "PNG", quality=95)
    return True


def save_bmp(raw_data, w, h, output_path):
    """Fallback BMP save if PIL not available."""
    # Write BMP file manually
    bmp_path = str(output_path).replace(".png", ".bmp")
    import struct
    stride = w * 4
    row_padding = (4 - (w * 3) % 4) % 4
    pixel_data = bytearray()
    for row in range(h):
        for col in range(w):
            i = row * stride + col * 4
            b, g, r = raw_data[i], raw_data[i+1], raw_data[i+2]
            pixel_data.extend([b, g, r])
        pixel_data.extend(b"\x00" * row_padding)
    file_size = 54 + len(pixel_data)
    with open(bmp_path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<I", file_size))
        f.write(b"\x00\x00\x00\x00")
        f.write(struct.pack("<I", 54))
        f.write(struct.pack("<I", 40))
        f.write(struct.pack("<i", w))
        f.write(struct.pack("<i", -h))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", 24))
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", len(pixel_data)))
        f.write(struct.pack("<i", 2835))
        f.write(struct.pack("<i", 2835))
        f.write(b"\x00\x00\x00\x00")
        f.write(b"\x00\x00\x00\x00")
        f.write(pixel_data)
    return True


def main():
    parser = argparse.ArgumentParser(description="DPI-Aware Screen Capture")
    parser.add_argument("--window", "-w", type=str, help="Capture window by title (partial match)")
    parser.add_argument("--pad", type=int, default=0, help="Padding around window in px")
    parser.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="Capture region (physical px)")
    parser.add_argument("--monitor", "-m", type=int, help="Capture monitor by index (0,1,...)")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--list-windows", action="store_true", help="List visible windows")
    parser.add_argument("--list-monitors", action="store_true", help="List monitors")
    parser.add_argument("--open", action="store_true", help="Open image after capture")
    args = parser.parse_args()

    # Info commands
    if args.list_monitors:
        for i, m in enumerate(get_monitors_info()):
            print(f"Monitor {i}: {m['bounds'][2]}x{m['bounds'][3]} @ ({m['bounds'][0]},{m['bounds'][1]}) "
                  f"DPI={m['dpi']} Scale={m['scale']*100:.0f}% Primary={m['primary']} Device={m['device']}")
        return

    if args.list_windows:
        for hwnd, title, x, y, w, h in list_windows():
            print(f"  [{hwnd}] ({w}x{h} @ {x},{y}) {title}")
        return

    # Determine capture region
    x, y, w, h = None, None, None, None

    if args.window:
        result = find_window(args.window)
        if not result:
            print(f"ERROR: Window '{args.window}' not found!")
            sys.exit(1)
        hwnd, (left, top, right, bottom), title = result
        x = left - args.pad
        y = top - args.pad
        w = (right - left) + args.pad * 2
        h = (bottom - top) + args.pad * 2
        print(f"Capturing window: '{title}' at ({x},{y}) {w}x{h}")
    elif args.region:
        x, y, w, h = args.region
        print(f"Capturing region: ({x},{y}) {w}x{h}")
    elif args.monitor is not None:
        monitors = get_monitors_info()
        if args.monitor >= len(monitors):
            print(f"ERROR: Monitor {args.monitor} not found. Only {len(monitors)} monitors.")
            sys.exit(1)
        m = monitors[args.monitor]
        x, y, w, h = m["bounds"]
        print(f"Capturing monitor {args.monitor}: {w}x{h} @ ({x},{y})")
    else:
        # Full virtual screen
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        print(f"Capturing virtual screen: {w}x{h} @ ({x},{y})")

    # Clamp to virtual screen bounds
    vs_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vs_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vs_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vs_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    x = max(vs_x, x)
    y = max(vs_y, y)
    w = min(w, vs_x + vs_w - x)
    h = min(h, vs_y + vs_h - y)

    if w <= 0 or h <= 0:
        print("ERROR: Invalid capture region (width/height <= 0)")
        sys.exit(1)

    # Capture
    print(f"Capturing {w}x{h} at physical coordinates ({x},{y})...")
    raw_data, cw, ch = capture_region(x, y, w, h)

    # Determine output path
    if args.output:
        out = Path(args.output)
    else:
        out = Path(r"D:\ProductAtelier-Desktop\tools\last_capture.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    save_png(raw_data, cw, ch, out)
    print(f"Saved: {out}")

    if args.open:
        os.startfile(str(out))

    return str(out)


if __name__ == "__main__":
    result = main()
