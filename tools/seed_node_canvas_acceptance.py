"""Isolated local UI fixtures; never touches formal assets or credentials."""
from __future__ import annotations
import argparse
import io
import json
import shutil
import sqlite3
import tempfile
import wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', required=True); parser.add_argument('--catalog')
    args = parser.parse_args(); root = Path(args.root).resolve(); temp = Path(tempfile.gettempdir()).resolve()
    if root.parent != temp or not root.name.startswith('ProductAtelier-app-test-'):
        raise ValueError('fixture root must be an explicit isolated temporary app-test directory')
    root.mkdir(exist_ok=True); (root / 'webview2-user-data').mkdir(exist_ok=True); (root / 'no-knowledge-vault').mkdir(exist_ok=True)
    if args.catalog:
        source = Path(args.catalog).resolve(); target = root / 'provider-catalog.sqlite3'
        if target.exists(): raise ValueError('do not overwrite an existing catalog')
        # This catalog database contains metadata/snapshots, NOT API keys.
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(target) as copy:
            original.backup(copy)
    fixtures = root / 'fixtures'; fixtures.mkdir(exist_ok=True)
    image = Image.new('RGBA', (3072, 2048), 'white'); draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 250)
    draw.rounded_rectangle((100, 160, 2960, 1880), radius=100, fill='#cf4c20', outline='#f9c75b', width=30)
    draw.text((470, 370), 'PRODUCT ATELIER', font=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 180), fill='white')
    draw.text((1060, 830), '14.9', font=font, fill='#fff5cc')
    draw.text((780, 1320), 'KEEP THE LOGO', font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 140), fill='white')
    image.save(fixtures / 'design-3k.png')
    alpha = Image.new('RGBA', (2048, 1200), (0, 0, 0, 0)); painter = ImageDraw.Draw(alpha)
    painter.rectangle((30, 30, 2018, 1170), fill=(238, 171, 50, 210)); painter.text((120, 400), 'EXACT ALPHA', font=font, fill='#542800')
    alpha.save(fixtures / 'alpha-2k.png')
    with wave.open(str(fixtures / 'audio.wav'), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(b'\0\0' * 16000)
    repo = Path(__file__).resolve().parents[1]
    shutil.copyfile(repo / 'python/video_fixtures/offline-preview-v1/1x1/5s.webm', fixtures / 'offline-video.webm')
    print(json.dumps({'root': str(root), 'fixtures': str(fixtures), 'provider_calls': 0, 'catalog': 'read-only metadata copy' if args.catalog else 'none'}))


if __name__ == '__main__': main()
