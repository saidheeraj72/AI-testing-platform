"""Screenshots for the model, with element refs drawn on them ("set of marks").

The model sees what a person sees: cards, icons, layout, greyed-out
controls. Each actionable element in the viewport gets a box and a small
label with its ref, so the model can answer with a ref (reliable) rather
than pixel coordinates (small models get those wrong).

Screenshots are taken at CSS pixel scale, so coordinates in the image are
the coordinates a mouse click uses.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.schemas.observation import Element

MAX_MARKS = 80
_COLORS = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#9a6324", "#800000")


def mark(png: bytes, elements: list[Element], out: Path) -> Path:
    """Draw ref labels for the visible elements onto the screenshot and save it as `out`."""
    image = Image.open(BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(12)
    width, height = image.size
    visible = [e for e in elements if e.box and e.in_viewport and e.box.width > 1 and e.box.height > 1]
    for i, e in enumerate(visible[:MAX_MARKS]):
        b = e.box
        x0, y0 = max(0, b.x), max(0, b.y)
        x1, y1 = min(width - 1, b.x + b.width), min(height - 1, b.y + b.height)
        if x1 <= x0 or y1 <= y0:
            continue
        color = _COLORS[i % len(_COLORS)]
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = e.ref
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        # Label above the box when there is room, else inside its top-left corner.
        ly = y0 - th - 3 if y0 - th - 3 >= 0 else y0
        draw.rectangle([x0, ly, x0 + tw + 4, ly + th + 3], fill=color)
        draw.text((x0 + 2, ly + 1), label, fill="white", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "JPEG", quality=80)
    return out


def _font(size: int):
    for name in ("Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)
