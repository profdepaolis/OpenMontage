"""Known-content image used to *prove* a multimodal capability.

The image contains three unambiguous facts (a number, a background colour and a
shape). A model that really received the image must report the number: this is
what KORA checks before marking a model as vision-capable. A text-only model
cannot guess "47" reliably, so it does not get promoted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

PROBE_NUMBER = "47"
PROBE_BG = (14, 124, 134)  # teal
PROBE_SHAPE = "triangolo rosso"
PROBE_SIZE = (900, 600)


def build_probe_image(target: Optional[Path] = None) -> dict[str, Any]:
    from kora_studio import config as kora_config

    if target is None:
        base = Path(getattr(kora_config, "DATA_DIR", Path.home() / "kora_data")) / "ai_engine_workspace"
        base.mkdir(parents=True, exist_ok=True)
        target = base / "probe_visione.png"
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGB", PROBE_SIZE, PROBE_BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, PROBE_SIZE[0] - 40, PROBE_SIZE[1] - 40], outline=(250, 247, 242), width=6)
    draw.text((PROBE_SIZE[0] // 2 - 120, PROBE_SIZE[1] // 2 - 90), PROBE_NUMBER, fill=(250, 247, 242))
    # a red triangle in the lower right corner
    draw.polygon(
        [
            (PROBE_SIZE[0] - 260, PROBE_SIZE[1] - 70),
            (PROBE_SIZE[0] - 90, PROBE_SIZE[1] - 70),
            (PROBE_SIZE[0] - 175, PROBE_SIZE[1] - 210),
        ],
        fill=(200, 40, 40),
    )
    image.save(target, "PNG")
    return {
        "path": target,
        "expected": PROBE_NUMBER,
        "facts": {
            "number": PROBE_NUMBER,
            "background": "verde acqua / teal",
            "shape": PROBE_SHAPE,
        },
    }
