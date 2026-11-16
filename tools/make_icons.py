"""Erzeugt die PNG-Icons (PWA, Apple Touch, Podcast-Artwork) passend zu frontend/icons/icon.svg.

Nur für Entwickler nötig (Pillow ist keine Laufzeitabhängigkeit):
    pip install pillow && python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "frontend" / "icons"
TOP, BOTTOM = (0x8B, 0x6C, 0xFF), (0x2F, 0x80, 0xED)


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    return img


def render(size: int, rounded: bool, padding: float = 0.0) -> Image.Image:
    scale = 4  # Supersampling für glatte Kanten
    s = size * scale
    base = gradient(s).convert("RGBA")
    if rounded:
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 112 / 512), fill=255)
        base.putalpha(mask)
    draw = ImageDraw.Draw(base)
    k = s / 512
    inner = 1 - 2 * padding
    off = s * padding

    def p(x, y):
        return (off + x * k * inner, off + y * k * inner)

    w = int(36 * k * inner)
    for line in ([(256, 112), (256, 308)], [(176, 236), (256, 316), (336, 236)], [(136, 392), (376, 392)]):
        pts = [p(*pt) for pt in line]
        draw.line(pts, fill="white", width=w, joint="curve")
        r = w / 2
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill="white")
    return base.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, rounded=True).save(OUT / "icon-192.png", optimize=True)
    render(512, rounded=True).save(OUT / "icon-512.png", optimize=True)
    # maskable: volle Fläche, Motiv mit Sicherheitsabstand
    render(512, rounded=False, padding=0.1).save(OUT / "icon-maskable-512.png", optimize=True)
    # iOS rundet selbst ab -> kein Alpha
    render(180, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    # Podcast-Artwork: 1400x1400, RGB, ohne Transparenz
    render(1400, rounded=False, padding=0.12).convert("RGB").save(OUT / "podcast-1400.png", optimize=True)
    print("Icons geschrieben nach", OUT)


if __name__ == "__main__":
    main()
