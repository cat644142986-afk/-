#!/usr/bin/env python3
"""Quick dev helper: launch Product Atelier + capture screenshot after delay."""
import sys, os, time, subprocess, ctypes
from pathlib import Path

# Add tools dir to path
sys.path.insert(0, str(Path(__file__).parent))
from screenshot import find_window, capture_region, save_png

def main():
    exe = Path(r"D:\ProductAtelier-Desktop\release\ProductAtelier-Portable\Product Atelier.exe")
    if not exe.exists():
        print(f"ERROR: EXE not found at {exe}")
        sys.exit(1)

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\ProductAtelier-Desktop\tools\last_capture.png")
    wait_time = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    pad = int(sys.argv[3]) if len(sys.argv) > 3 else 30

    # Kill existing instances first
    subprocess.run(["taskkill", "/F", "/IM", "Product Atelier.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "python-server.exe"], capture_output=True)
    time.sleep(2)

    print(f"Launching Product Atelier...")
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    print(f"Waiting {wait_time}s for app to start...")
    time.sleep(wait_time)

    result = find_window("Product Atelier")
    if not result:
        print("ERROR: Window not found after launch!")
        sys.exit(1)

    hwnd, (left, top, right, bottom), title = result
    x = left - pad
    y = top - pad
    w = (right - left) + pad * 2
    h = (bottom - top) + pad * 2

    print(f"Window found: {w-2*pad}x{h-2*pad} at ({left},{top})")
    print(f"Capturing with {pad}px padding: {w}x{h}...")
    raw_data, cw, ch = capture_region(x, y, w, h)
    save_png(raw_data, cw, ch, out_path)
    print(f"Saved: {out_path}")
    return str(out_path)

if __name__ == "__main__":
    main()
