"""Hyperframes Caption & Motion Typography Engine for KORA STUDIO.

Bridges KORA Brand Brain / OpenMontage `hyperframes_style_bridge` into an HTML/CSS/GSAP
HyperFrames composition (`hyperframes_captions/index.html` + `DESIGN.md`) and renders
deterministic 1080x1920 RGBA caption overlays (including word-level progressive
highlight and kinetic gold bars) compatible with both offline FFmpeg and HyperFrames CLI.
"""

from __future__ import annotations

import html
import re
import textwrap
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from kora_studio.config import ASSETS_KORA_DIR, DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from kora_studio.db.models import StoryboardScene

CAPTION_STYLES: dict[str, dict[str, str]] = {
    "serif_ivory": {
        "id": "serif_ivory",
        "label": "KORA Classic Serif",
        "engine": "kora_native",
        "description": "Playfair Display avorio con scrim morbido calibrato.",
    },
    "serif_minimal": {
        "id": "serif_minimal",
        "label": "Editorial Minimal",
        "engine": "kora_native",
        "description": "Tipografia leggera senza box, solo ombra discreta.",
    },
    "clean_sans": {
        "id": "clean_sans",
        "label": "Space Grotesk Clean",
        "engine": "kora_native",
        "description": "Sans contemporaneo ad alta leggibilità mobile.",
    },
    "hyperframes_kinetic": {
        "id": "hyperframes_kinetic",
        "label": "Hyperframes · Kinetic Gold",
        "engine": "hyperframes",
        "description": "Composizione Hyperframes con accento oro KORA (#9A8052) e ingresso cinetico.",
    },
    "hyperframes_glass": {
        "id": "hyperframes_glass",
        "label": "Hyperframes · Mediterranean Glass",
        "engine": "hyperframes",
        "description": "Card vetro satinato con bordo sottile sabbia e tipografia editoriale.",
    },
    "hyperframes_word_highlight": {
        "id": "hyperframes_word_highlight",
        "label": "Hyperframes · Word Highlight (Karaoke)",
        "engine": "hyperframes",
        "description": "Evidenziazione progressiva parola per parola sincronizzata alla scena.",
    },
}


def load_kora_font(size: int, italic: bool = False, sans: bool = False) -> ImageFont.FreeTypeFont:
    candidates: list[Path] = []
    if sans:
        candidates.append(ASSETS_KORA_DIR / "SpaceGrotesk.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    elif italic:
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay-Italic.ttf")
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))
    else:
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))
    for c in candidates:
        if c.is_file():
            try:
                return ImageFont.truetype(str(c), size=size)
            except Exception:
                continue
    return ImageFont.load_default()  # type: ignore


def export_hyperframes_workspace(
    scenes: list[StoryboardScene],
    caption_style: str,
    title: str,
    out_dir: Path,
    fps: int = DEFAULT_FPS,
) -> dict[str, str]:
    """Emit a real HyperFrames HTML/CSS/GSAP workspace (`index.html` + `DESIGN.md`)
    using OpenMontage's `lib.hyperframes_style_bridge` when available.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    total_sec = max(1.0, sum(s.duration_frames for s in scenes) / max(1, fps))

    css_vars = {
        "--color-bg": "transparent",
        "--color-fg": "#F7F3EA",
        "--color-accent": "#9A8052",
        "--color-primary": "#224C66",
        "--color-surface": "rgba(18, 17, 15, 0.72)",
        "--font-heading": "'Playfair Display', Georgia, serif",
        "--font-body": "'Space Grotesk', system-ui, sans-serif",
    }
    design_md = (
        "# KORA STUDIO · HyperFrames Caption System\n\n"
        f"- Title: {title}\n"
        f"- Caption Style: {caption_style}\n"
        "- Palette: Warm Ivory `#F7F3EA`, KORA Gold `#9A8052`, Adriatic Blue `#224C66`, Obsidian `#12110F`\n"
        "- Safe Area: 9:16 `1080x1920`, bottom zone `72%..84%`\n"
    )
    try:
        from lib.hyperframes_style_bridge import style_bridge  # type: ignore

        kora_playbook = {
            "name": f"KORA {caption_style}",
            "visual_language": {
                "color_palette": {
                    "background": ["#0B0C0B"],
                    "text": ["#F7F3EA"],
                    "accent": ["#9A8052"],
                    "primary": ["#224C66"],
                }
            },
            "typography": {
                "heading": {"font": "Playfair Display"},
                "body": {"font": "Space Grotesk"},
            },
            "motion": {"pace": "slow"},
        }
        bridged_css, bridged_md = style_bridge(kora_playbook, {"render_runtime": "hyperframes"})
        css_vars.update(bridged_css)
        design_md = bridged_md + "\n\n" + design_md
    except Exception:
        pass

    root_css = "\n".join(f"  {k}: {v};" for k, v in css_vars.items())
    scene_divs = []
    for sc in scenes:
        if sc.is_outro or not (sc.subtitle_text or "").strip():
            continue
        txt = html.escape(sc.subtitle_text.strip())
        scene_divs.append(
            f'  <div class="hf-caption style-{html.escape(caption_style)}" '
            f'data-scene="{sc.scene_number}" data-start="{sc.start_sec:.3f}" data-end="{sc.end_sec:.3f}">'
            f'<span class="hf-accent"></span><p>{txt}</p></div>'
        )

    html_doc = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=1080,height=1920,initial-scale=1.0" />
<title>{html.escape(title)} · HyperFrames Captions</title>
<style>
:root {{
{root_css}
}}
html, body {{
  margin: 0; padding: 0; width: 1080px; height: 1920px;
  background: transparent; overflow: hidden;
  font-family: var(--font-heading);
}}
.stage {{
  position: relative; width: 1080px; height: 1920px;
}}
.hf-caption {{
  position: absolute; left: 96px; right: 96px; bottom: 320px;
  text-align: center; color: var(--color-fg);
  opacity: 0; transform: translateY(18px);
  transition: opacity 0.35s ease, transform 0.35s ease;
}}
.hf-caption.active {{
  opacity: 1; transform: translateY(0);
}}
.hf-caption p {{
  margin: 0; font-size: 44px; line-height: 1.32;
}}
.style-hyperframes_kinetic {{
  background: rgba(16, 15, 13, 0.74);
  border-radius: 18px; padding: 26px 34px;
  border-bottom: 3px solid #9A8052;
}}
.style-hyperframes_kinetic .hf-accent {{
  display: block; width: 64px; height: 3px; background: #9A8052; margin: 0 auto 14px;
}}
.style-hyperframes_glass {{
  background: rgba(250, 247, 242, 0.16);
  backdrop-filter: blur(14px);
  border: 1.5px solid rgba(250, 247, 242, 0.45);
  border-radius: 22px; padding: 26px 34px;
}}
.style-hyperframes_word_highlight {{
  background: rgba(12, 12, 11, 0.78);
  border-radius: 18px; padding: 24px 30px;
  font-family: var(--font-body);
}}
</style>
</head>
<body>
<div class="stage" data-duration="{total_sec:.3f}" data-fps="{fps}">
{chr(10).join(scene_divs)}
</div>
<script>
window.__hfSeek = function(t) {{
  document.querySelectorAll('.hf-caption').forEach(function(el) {{
    var st = parseFloat(el.dataset.start), en = parseFloat(el.dataset.end);
    if (t >= st && t <= en) el.classList.add('active'); else el.classList.remove('active');
  }});
}};
</script>
</body>
</html>
"""
    index_path = out_dir / "index.html"
    design_path = out_dir / "DESIGN.md"
    index_path.write_text(html_doc, encoding="utf-8")
    design_path.write_text(design_md, encoding="utf-8")
    return {"index_html": str(index_path), "design_md": str(design_path)}


def build_caption_segments_for_scene(
    scene: StoryboardScene,
    caption_style: str,
    sub_dir: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    overrides: Optional[dict[str, Any]] = None,
    word_timings: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Render deterministic PNG overlay segment(s) for a single scene.

    For `hyperframes_word_highlight`, produces time-sliced word-highlight PNGs
    synchronized evenly across the scene's active speech window!
    """
    text = (scene.subtitle_text or "").strip()
    if scene.is_outro or not text:
        return []

    st_global = max(0.0, scene.start_sec + 0.12)
    en_global = max(st_global + 0.25, scene.end_sec - 0.12)

    if caption_style == "hyperframes_word_highlight":
        words = [w for w in re.split(r"\s+", text) if w]
        # 3.6.6 — se la timeline fornisce i tempi delle parole, si usa la voce reale:
        # niente più suddivisione uniforme della finestra della scena.
        timed = [w for w in (word_timings or []) if w.get("start_sec") is not None]
        if timed:
            out_png = sub_dir / f"sub_{scene.scene_number:02d}_0.png"
            segs_t: list[dict[str, Any]] = []
            for idx_w, item in enumerate(timed):
                png = sub_dir / f"sub_{scene.scene_number:02d}_{idx_w}.png"
                render_styled_caption_png(
                    text,
                    png,
                    style=caption_style,
                    active_word_idx=min(len(words) - 1, int(item.get("word_index", idx_w))),
                    width=width,
                    height=height,
                    overrides=overrides,
                )
                segs_t.append(
                    {
                        "png": png,
                        "start_sec": max(0.0, float(item["start_sec"])),
                        "end_sec": max(float(item["start_sec"]) + 0.08, float(item["end_sec"])),
                        "timing_source": item.get("timing_source", "estimated"),
                    }
                )
            return segs_t
        if len(words) <= 1:
            out_png = sub_dir / f"sub_{scene.scene_number:02d}_0.png"
            render_styled_caption_png(text, out_png, style=caption_style, active_word_idx=0, width=width, height=height, overrides=overrides)
            return [{"png": out_png, "start_sec": st_global, "end_sec": en_global}]
        # Group into <= 8 progressive steps for fast deterministic FFmpeg compositing
        step = max(1, (len(words) + 7) // 8)
        indices = list(range(0, len(words), step))
        span = (en_global - st_global) / max(1, len(indices))
        segs: list[dict[str, Any]] = []
        for idx_k, w_idx in enumerate(indices):
            out_png = sub_dir / f"sub_{scene.scene_number:02d}_{idx_k}.png"
            render_styled_caption_png(
                text,
                out_png,
                style=caption_style,
                active_word_idx=min(len(words) - 1, w_idx + step - 1),
                width=width,
                height=height,
                overrides=overrides,
            )
            s_t = st_global + idx_k * span
            e_t = en_global if idx_k == len(indices) - 1 else st_global + (idx_k + 1) * span
            segs.append({"png": out_png, "start_sec": s_t, "end_sec": e_t})
        return segs

    out_png = sub_dir / f"sub_{scene.scene_number:02d}.png"
    render_styled_caption_png(
        text, out_png, style=caption_style, width=width, height=height, overrides=overrides
    )
    return [{"png": out_png, "start_sec": st_global, "end_sec": en_global}]


def render_styled_caption_png(
    text: str,
    out_png: Path,
    style: str = "serif_ivory",
    active_word_idx: Optional[int] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    overrides: Optional[dict[str, Any]] = None,
) -> Path:
    """Render 1080x1920 transparent subtitle overlay in the requested KORA / Hyperframes style.

    `overrides` applica le scelte del Caption Designer (dimensione, colore,
    posizione, interlinea, scrim, ombra) senza cambiare lo stile di base.
    """
    ov: dict[str, Any] = overrides or {}
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not text.strip():
        out_png.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(out_png, "PNG")
        return out_png

    draw = ImageDraw.Draw(overlay)
    is_sans = style in ("clean_sans", "hyperframes_word_highlight")
    if ov.get("font") in ("kora_sans", "kora_serif"):
        is_sans = ov["font"] == "kora_sans"
    try:
        font_size = int(ov.get("font_size") or (44 if not is_sans else 42))
    except (TypeError, ValueError):
        font_size = 44 if not is_sans else 42
    font = load_kora_font(max(20, min(84, font_size)), sans=is_sans)

    try:
        wrap_width = int(ov.get("max_chars") or 26)
    except (TypeError, ValueError):
        wrap_width = 26
    try:
        max_lines = int(ov.get("max_lines") or 3)
    except (TypeError, ValueError):
        max_lines = 3
    wrapped = textwrap.fill(text.strip(), width=max(12, min(40, wrap_width)))
    lines = wrapped.splitlines()[: max(1, min(3, max_lines))]

    line_heights: list[int] = []
    line_widths: list[int] = []
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bb[2] - bb[0])
        line_heights.append((bb[3] - bb[1]) + 14)

    block_h = sum(line_heights)
    block_w = max(line_widths) if line_widths else 0
    box_pad_x = 40
    box_pad_y = 24
    position = str(ov.get("position") or "bottom")
    anchor_y = {"top": 0.18, "center": 0.5}.get(position, 0.775)
    y_start = int(height * anchor_y) - block_h // 2
    box_left = (width - block_w) // 2 - box_pad_x
    box_right = (width + block_w) // 2 + box_pad_x
    box_top = y_start - box_pad_y
    box_bottom = y_start + block_h + box_pad_y - 10

    if ov.get("scrim") is False:
        pass
    elif style == "serif_minimal":
        pass
    elif style == "hyperframes_glass":
        draw.rounded_rectangle(
            [box_left, box_top, box_right, box_bottom],
            radius=24,
            fill=(28, 32, 34, 155),
            outline=(247, 243, 234, 130),
            width=2,
        )
    elif style == "hyperframes_kinetic":
        draw.rounded_rectangle(
            [box_left, box_top - 10, box_right, box_bottom],
            radius=18,
            fill=(16, 15, 13, 192),
        )
        # Gold top accent line + subtle bottom gold border
        cx = width // 2
        draw.rectangle([cx - 38, box_top + 4, cx + 38, box_top + 8], fill=(154, 128, 82, 240))
        draw.rectangle([box_left + 16, box_bottom - 4, box_right - 16, box_bottom], fill=(154, 128, 82, 210))
    elif style == "hyperframes_word_highlight":
        draw.rounded_rectangle(
            [box_left, box_top, box_right, box_bottom],
            radius=20,
            fill=(14, 14, 13, 195),
        )
    else:
        draw.rounded_rectangle(
            [box_left, box_top, box_right, box_bottom],
            radius=18,
            fill=(18, 17, 15, 165),
        )

    cur_y = y_start
    if style == "hyperframes_word_highlight" and active_word_idx is not None:
        global_word_idx = 0
        for idx, line in enumerate(lines):
            words_in_line = line.split(" ")
            lw = line_widths[idx]
            cur_x = (width - lw) // 2
            for w_pos, word in enumerate(words_in_line):
                piece = word + (" " if w_pos < len(words_in_line) - 1 else "")
                pb = draw.textbbox((0, 0), piece, font=font)
                pw = pb[2] - pb[0]
                is_active = global_word_idx <= active_word_idx
                is_current = global_word_idx == active_word_idx
                if is_current:
                    wb = draw.textbbox((0, 0), word, font=font)
                    draw.rounded_rectangle(
                        [cur_x - 6, cur_y - 3, cur_x + (wb[2] - wb[0]) + 6, cur_y + line_heights[idx] - 10],
                        radius=8,
                        fill=(154, 128, 82, 215),
                    )
                    fill_col = (255, 252, 245, 255)
                elif is_active:
                    fill_col = (247, 243, 234, 255)
                else:
                    fill_col = (205, 198, 184, 170)
                draw.text((cur_x, cur_y), piece, fill=fill_col, font=font)
                cur_x += pw
                global_word_idx += 1
            cur_y += line_heights[idx]
    else:
        try:
            hex_color = str(ov.get("color") or "#F7F3EA").lstrip("#")
            text_fill = (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16), 255)
        except Exception:
            text_fill = (247, 243, 234, 255)
        align = str(ov.get("align") or "center")
        for idx, line in enumerate(lines):
            lw = line_widths[idx]
            if align == "left":
                lx = box_left + box_pad_x
            elif align == "right":
                lx = box_right - box_pad_x - lw
            else:
                lx = (width - lw) // 2
            if ov.get("shadow") is not False:
                draw.text((lx + 1, cur_y + 2), line, fill=(0, 0, 0, 170), font=font)
            draw.text((lx, cur_y), line, fill=text_fill, font=font)
            cur_y += line_heights[idx]

    out_png.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(out_png, "PNG")
    return out_png


HYPERFRAMES_OPTIONS_CATALOG: dict[str, list[dict[str, str]]] = {
    "caption_style": [
        {"id": k, "label": v["label"], "description": v["description"]}
        for k, v in CAPTION_STYLES.items()
    ],
    "title_card_intro": [
        {"id": "none", "label": "Nessuna Title Card", "description": "Apertura pura sull'immagine reale."},
        {"id": "editorial_serif", "label": "Editorial Serif Intro", "description": "Titolo in Playfair Display centrato su Scena 1 (0.3s–2.8s)."},
        {"id": "hyperframes_gold_line", "label": "Hyperframes · Gold Line Title", "description": "Title card Hyperframes con filetto oro #9A8052 e sottotitolo Policoro."},
    ],
    "grain_overlay": [
        {"id": "none", "label": "Pulito (Nessuna grana)", "description": "Nitidezza digitale originale."},
        {"id": "subtle_35mm", "label": "Grana 35mm leggera", "description": "Texture pellicola discreta (Hyperframes grain-overlay)."},
        {"id": "warm_kodak", "label": "Pellicola calda Kodak", "description": "Grana organica calda adatta a tramonti e luce radente."},
    ],
    "vignette": [
        {"id": "none", "label": "Nessuna vignettatura", "description": "Luminosità uniforme su tutto il frame."},
        {"id": "soft_luxury", "label": "Vignettatura Luxury morbida", "description": "Guida l'occhio al centro con bordi leggermente più profondi."},
        {"id": "deep_cinematic", "label": "Vignettatura Cinema", "description": "Cornice ottica drammatica per tramonti o sera."},
    ],
    "letterbox": [
        {"id": "none", "label": "Schermo pieno 9:16", "description": "Nessuna cornice ai bordi."},
        {"id": "cinema_frame", "label": "Cornice Cinema leggera", "description": "Bande scure minimali alto/basso per respiro cinematografico."},
        {"id": "editorial_border", "label": "Passepartout Avorio", "description": "Sottile filetto editoriale caldo sui margini."},
    ],
    "motion_pace": [
        {"id": "slow_contemplative", "label": "Lento contemplativo", "description": "Movimenti di camera morbidi e distesi."},
        {"id": "balanced", "label": "Equilibrato KORA", "description": "Ritmo naturale tra dettagli e respiri visivi."},
        {"id": "dynamic_experience", "label": "Dinamico Experience", "description": "Ritmo più vivo per aperitivi, convivialità ed eventi."},
    ],
}

DEFAULT_HYPERFRAMES_CONFIG: dict[str, str] = {
    "caption_style": "serif_ivory",
    "title_card_intro": "none",
    "grain_overlay": "none",
    "vignette": "soft_luxury",
    "letterbox": "none",
    "motion_pace": "balanced",
}


def normalize_hyperframes_config(raw: Optional[dict[str, Any]]) -> dict[str, str]:
    cfg = dict(DEFAULT_HYPERFRAMES_CONFIG)
    if not raw:
        return cfg
    for key, options in HYPERFRAMES_OPTIONS_CATALOG.items():
        val = str(raw.get(key) or "").strip()
        valid_ids = {o["id"] for o in options}
        if val in valid_ids:
            cfg[key] = val
    return cfg


def recommend_hyperframes_for_theme(theme: str) -> dict[str, Any]:
    """Intelligently recommend Preset, Hyperframes options, and theme enrichments from user text."""
    lower = (theme or "").lower()
    if any(k in lower for k in ("aperitivo", "tramonto", "sera", "calice", "spritz", "festa", "insieme", "amici")):
        hf = {
            "caption_style": "hyperframes_kinetic",
            "title_card_intro": "hyperframes_gold_line",
            "grain_overlay": "warm_kodak",
            "vignette": "deep_cinematic",
            "letterbox": "none",
            "motion_pace": "dynamic_experience",
        }
        preset = "KORA Experience"
        rationale = (
            "Per temi conviviali o al tramonto consiglio Hyperframes Kinetic Gold + grana calda Kodak + vignettatura cinema "
            "per valorizzare riflessi dorati, calici e ritmo vivo."
        )
        suggestions = [
            f"{theme.strip()} — con focus sui dettagli dei calici e i sorrisi sotto il pergolato",
            "Aperitivo al tramonto a Policoro: ritmo caldo, poche parole, suono del mare",
            "La luce dorata delle 19:00 al KORA: dalla preparazione dei cocktail al brindisi",
            "Ospitalità vista mare: un invito elegante a vivere la serata senza fretta",
        ]
    elif any(k in lower for k in ("poche parole", "silenzio", "poesia", "minimal", "essenziale", "fuori stagione", "inverno")):
        hf = {
            "caption_style": "serif_minimal",
            "title_card_intro": "editorial_serif",
            "grain_overlay": "subtle_35mm",
            "vignette": "soft_luxury",
            "letterbox": "editorial_border",
            "motion_pace": "slow_contemplative",
        }
        preset = "KORA Editorial"
        rationale = (
            "Per un racconto intimo ed essenziale consiglio Editorial Minimal + cornice avorio + ritmo lento contemplativo "
            "per lasciare spazio al respiro delle immagini."
        )
        suggestions = [
            f"{theme.strip()} — ritmo contemplativo con solo 3 frasi brevi e suono naturale delle onde",
            "Quando la spiaggia si svuota, il mare di Policoro torna protagonista",
            "Pochi gesti, materiali naturali e luce radente: The Art of Being",
            "Un Reel senza voiceover, solo musica soffusa, onde e sottotitoli editoriali",
        ]
    elif any(k in lower for k in ("colazione", "mattino", "caffè", "alba", "luce")):
        hf = {
            "caption_style": "hyperframes_glass",
            "title_card_intro": "editorial_serif",
            "grain_overlay": "subtle_35mm",
            "vignette": "soft_luxury",
            "letterbox": "none",
            "motion_pace": "balanced",
        }
        preset = "KORA Cinematic Identity"
        rationale = (
            "Per la luce del mattino e la colazione consiglio Hyperframes Mediterranean Glass + grana 35mm leggera "
            "per un effetto luminoso, fresco e tattile."
        )
        suggestions = [
            f"{theme.strip()} — dalla prima luce sul pergolato al profumo del caffè",
            "Colazione vista mare a Policoro: ombre lunghe, lino chiaro e calma assoluta",
            "Il risveglio del KORA prima dell'arrivo degli ospiti",
            "Dettagli del mattino: tazzina calda, vento leggero e orizzonte limpido",
        ]
    else:
        hf = {
            "caption_style": "hyperframes_word_highlight",
            "title_card_intro": "hyperframes_gold_line",
            "grain_overlay": "subtle_35mm",
            "vignette": "soft_luxury",
            "letterbox": "cinema_frame",
            "motion_pace": "balanced",
        }
        preset = "KORA Cinematic Identity"
        rationale = (
            "Combinazione equilibrata KORA: Title Card oro Hyperframes + Word Highlight ad alta leggibilità mobile "
            "+ cornice cinema leggera."
        )
        suggestions = [
            "Il piacere di stare insieme davanti al mare, senza guardare l'orologio",
            "Dal mattino al tramonto: una giornata autentica al KORA Beach Club di Policoro",
            "Architettura naturale, pergolato e ospitalità mediterranea",
            "Poche parole essenziali, voce calda e focus sui dettagli reali del lido",
        ]

    return {
        "recommended_preset": preset,
        "recommended_hyperframes": hf,
        "rationale": rationale,
        "theme_suggestions": [s for s in suggestions if s],
    }


def render_title_card_png(
    title: str,
    out_png: Path,
    style: str = "hyperframes_gold_line",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Path:
    """Render an editorial Hyperframes opening title card overlay for Scene 1."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if style == "none" or not title.strip():
        out_png.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(out_png, "PNG")
        return out_png

    draw = ImageDraw.Draw(overlay)
    kicker_font = load_kora_font(22, sans=True)
    title_font = load_kora_font(54, italic=False)
    kicker = "KORA BEACH CLUB · POLICORO"
    kb = draw.textbbox((0, 0), kicker, font=kicker_font)
    kw = kb[2] - kb[0]

    wrapped = textwrap.fill(title.strip(), width=22)
    lines = wrapped.splitlines()[:3]
    l_boxes = [draw.textbbox((0, 0), ln, font=title_font) for ln in lines]
    l_heights = [(b[3] - b[1]) + 16 for b in l_boxes]
    l_widths = [b[2] - b[0] for b in l_boxes]
    total_h = sum(l_heights) + 64
    max_w = max(kw, *(l_widths or [200]))

    center_y = int(height * 0.24)
    box_top = center_y - total_h // 2 - 26
    box_bottom = center_y + total_h // 2 + 20
    box_left = (width - max_w) // 2 - 44
    box_right = (width + max_w) // 2 + 44

    draw.rounded_rectangle(
        [max(40, box_left), box_top, min(width - 40, box_right), box_bottom],
        radius=22,
        fill=(14, 14, 13, 150),
        outline=(154, 128, 82, 160) if style == "hyperframes_gold_line" else (247, 243, 234, 80),
        width=2,
    )

    cur_y = box_top + 26
    draw.text(((width - kw) // 2, cur_y), kicker, fill=(214, 195, 155, 245), font=kicker_font)
    cur_y += 38
    if style == "hyperframes_gold_line":
        cx = width // 2
        draw.rectangle([cx - 55, cur_y, cx + 55, cur_y + 3], fill=(154, 128, 82, 245))
        cur_y += 20
    else:
        cur_y += 8

    for idx, line in enumerate(lines):
        lw = l_widths[idx]
        draw.text(((width - lw) // 2 + 1, cur_y + 2), line, fill=(0, 0, 0, 175), font=title_font)
        draw.text(((width - lw) // 2, cur_y), line, fill=(247, 243, 234, 255), font=title_font)
        cur_y += l_heights[idx]

    out_png.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(out_png, "PNG")
    return out_png


def build_hyperframes_vf_filters(
    hf_cfg: dict[str, str],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> list[str]:
    """Build deterministic FFmpeg post-processing video filters for Hyperframes visual FX."""
    filters: list[str] = []
    grain = hf_cfg.get("grain_overlay", "none")
    if grain == "subtle_35mm":
        filters.append("noise=alls=4:allf=t+u")
    elif grain == "warm_kodak":
        filters.append("noise=alls=7:allf=t+u,eq=saturation=1.04:gamma=1.01")

    vignette = hf_cfg.get("vignette", "none")
    if vignette == "soft_luxury":
        filters.append("vignette=angle=PI/6")
    elif vignette == "deep_cinematic":
        filters.append("vignette=angle=PI/4.2")

    letterbox = hf_cfg.get("letterbox", "none")
    if letterbox == "cinema_frame":
        filters.append(f"drawbox=x=0:y=0:w={width}:h=76:color=0x0B0C0B@0.92:t=fill")
        filters.append(f"drawbox=x=0:y={height - 76}:w={width}:h=76:color=0x0B0C0B@0.92:t=fill")
    elif letterbox == "editorial_border":
        filters.append(f"drawbox=x=24:y=24:w={width - 48}:h={height - 48}:color=0xF7F3EA@0.38:t=3")

    return filters

