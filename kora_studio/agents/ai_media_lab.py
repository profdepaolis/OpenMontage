"""Media Curator & AI Media Lab — authentic-first visual preparation & detail/motion tools."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import CACHE_DIR, DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from kora_studio.db.models import AuthenticityStatus, CameraMotion, StoryboardScene
from kora_studio.media_tools import run_ffmpeg
from kora_studio.providers.registry import ProviderManager
from kora_studio.vault.media_vault import MediaVault


class MediaCurator:
    """Matches scenes to authentic vault assets using strict authenticity priority."""

    def __init__(self, vault: MediaVault) -> None:
        self.vault = vault

    def curate_for_scene(self, scene: StoryboardScene, theme: str = "") -> dict[str, Any]:
        if scene.is_outro:
            logo = self.vault.get_logo_asset()
            return {
                "asset": logo,
                "priority_tier": "Originale reale (Logo)",
                "authenticity_status": AuthenticityStatus.AUTENTICO.value,
            }
        if scene.selected_asset_id:
            asset = self.vault.get_asset(scene.selected_asset_id)
            if asset:
                return {
                    "asset": asset,
                    "priority_tier": "Originale reale",
                    "authenticity_status": asset["authenticity_status"],
                }
        query = f"{scene.narrative_goal} {scene.subtitle_text} {theme}".strip()
        matches = self.vault.semantic_search(query, limit=3)
        if matches:
            return {
                "asset": matches[0],
                "priority_tier": "Originale reale (ricerca semantica)",
                "authenticity_status": matches[0]["authenticity_status"],
            }
        return {"asset": None, "priority_tier": "Mancante", "authenticity_status": "mancante"}


class AIMediaLab:
    """Implements Modes A–D respecting KORA authenticity hierarchy:
    Originale reale -> Ritaglio originale -> Animazione conservativa -> Generazione controllata -> Illustrativo.
    """

    def __init__(
        self,
        vault: MediaVault,
        brand_brain: BrandBrain,
        providers: ProviderManager,
    ) -> None:
        self.vault = vault
        self.brand_brain = brand_brain
        self.guardian = BrandGuardian(brand_brain)
        self.providers = providers

    def build_faithful_detail_prompt(self, detail_description: str) -> str:
        """Exact KORA authenticity prompt for faithful detail generation/crop (Section 7 Mode A)."""
        return (
            "Utilizza la fotografia fornita come riferimento vincolante. "
            f"Realizza un'inquadratura cinematografica ravvicinata del dettaglio selezionato ({detail_description}), "
            "conservando materiali, colori, geometria, posizione degli oggetti e illuminazione coerenti con l'originale. "
            "Non introdurre nuovi elementi architettonici, arredi o prodotti. Non alterare la struttura del luogo. "
            "Mantieni un risultato fotorealistico, naturale, coerente con l'identità luxury mediterranea KORA. "
            "Se il dettaglio non è sufficientemente documentato, evita di inventarlo e segnala il limite."
        )

    def create_detail_crop(
        self,
        asset_id: str,
        x_norm: float = 0.5,
        y_norm: float = 0.5,
        zoom: float = 1.8,
        detail_label: str = "dettaglio reale",
        ingest_to_vault: bool = True,
    ) -> dict[str, Any]:
        """Mode A — Real deterministic high-res 9:16 detail crop from an authentic photograph."""
        asset = self.vault.get_asset(asset_id)
        if not asset or asset["media_type"] != "image":
            raise ValueError("Modalità A richiede una fotografia esistente nel Media Vault.")
        src = Path(asset["working_path"] or asset["original_path"])
        zoom = max(1.1, min(3.5, float(zoom)))
        x_norm = max(0.15, min(0.85, float(x_norm)))
        y_norm = max(0.15, min(0.85, float(y_norm)))

        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            target_ratio = DEFAULT_WIDTH / DEFAULT_HEIGHT  # 9:16
            if w / h > target_ratio:
                crop_h = int(h / zoom)
                crop_w = int(crop_h * target_ratio)
            else:
                crop_w = int(w / zoom)
                crop_h = int(crop_w / target_ratio)
            crop_w = max(200, min(w, crop_w))
            crop_h = max(350, min(h, crop_h))
            cx = int(w * x_norm)
            cy = int(h * y_norm)
            left = max(0, min(w - crop_w, cx - crop_w // 2))
            top = max(0, min(h - crop_h, cy - crop_h // 2))
            cropped = im.crop((left, top, left + crop_w, top + crop_h))
            upscaled = cropped.resize((DEFAULT_WIDTH, DEFAULT_HEIGHT), Image.Resampling.LANCZOS)

        out_path = CACHE_DIR / f"detail_{asset_id}_{uuid.uuid4().hex[:6]}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        upscaled.save(out_path, "PNG")

        result: dict[str, Any] = {
            "mode": "Mode A — Ritaglio originale fedele",
            "source_asset_id": asset_id,
            "output_path": str(out_path),
            "authenticity_status": AuthenticityStatus.DERIVATO_CONSERVATIVO.value,
            "requires_human_review": False,
            "prompt_reference": self.build_faithful_detail_prompt(detail_label),
        }
        if ingest_to_vault:
            ingested = self.vault.ingest_file(
                out_path,
                origin=f"ritaglio_autentico:{asset_id}",
                authenticity_status=AuthenticityStatus.DERIVATO_CONSERVATIVO.value,
                usage_rights=asset["usage_rights"],
                category="Dettagli",
                tags=["ritaglio", "dettaglio reale", detail_label, *asset["tags"][:4]],
                original_filename=f"dettaglio_{Path(asset['filename']).stem}.png",
            )
            result["asset"] = ingested
        return result

    def animate_photo_deterministic(
        self,
        asset_path: Path,
        output_path: Path,
        duration_frames: int,
        motion: str = CameraMotion.SLOW_PUSH_IN.value,
        fps: int = DEFAULT_FPS,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> Path:
        """Mode B — Conservative deterministic camera motion on an authentic photograph."""
        duration_sec = duration_frames / fps
        # Render at 25fps using smooth subpixel zoompan on high-res pre-scaled canvas
        # Pre-scale to cover 1080x1920 with generous headroom (1440x2560) for smooth pan/zoom
        if motion == CameraMotion.SLOW_PULL_OUT.value:
            zoom_expr = f"max(1.0,1.10-0.10*(on/{max(1, duration_frames)}))"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == CameraMotion.PAN_RIGHT.value:
            zoom_expr = "1.08"
            x_expr = f"(iw-iw/zoom)*min(1,on/{max(1, duration_frames)})"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == CameraMotion.PAN_LEFT.value:
            zoom_expr = "1.08"
            x_expr = f"(iw-iw/zoom)*(1-min(1,on/{max(1, duration_frames)}))"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == CameraMotion.TILT_UP.value:
            zoom_expr = "1.08"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = f"(ih-ih/zoom)*(1-min(1,on/{max(1, duration_frames)}))"
        elif motion == CameraMotion.TILT_DOWN.value:
            zoom_expr = "1.08"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = f"(ih-ih/zoom)*min(1,on/{max(1, duration_frames)})"
        elif motion == CameraMotion.MICRO_PARALLAX.value:
            zoom_expr = f"1.04+0.05*(on/{max(1, duration_frames)})"
            x_expr = f"(iw-iw/zoom)*(0.45+0.10*(on/{max(1, duration_frames)}))"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == CameraMotion.STATIC_BREATHE.value:
            zoom_expr = f"1.0+0.025*(on/{max(1, duration_frames)})"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        else:  # SLOW_PUSH_IN
            zoom_expr = f"min(1.12,1.0+0.10*(on/{max(1, duration_frames)}))"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"

        vf = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={duration_frames}:s={width}x{height}:fps={fps},"
            f"setsar=1,format=yuv420p"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(asset_path),
                "-vf",
                vf,
                "-t",
                f"{duration_sec:.4f}",
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            timeout=180,
        )
        return output_path

    def request_image_to_video(
        self,
        asset_id: str,
        camera_motion: str = "lento avanti",
        duration_sec: int = 5,
        explicit_authorization: bool = False,
    ) -> dict[str, Any]:
        """Mode C — Image-to-Video with strict authenticity constraints and budget guard."""
        if duration_sec not in (3, 5, 8):
            raise ValueError("Durata Image-to-Video consentita: 3, 5 o 8 secondi.")
        asset = self.vault.get_asset(asset_id)
        if not asset:
            raise KeyError(f"Asset non trovato: {asset_id}")

        prompt = (
            f"Fotografia originale di riferimento: {asset['filename']}. "
            f"Movimento di camera: {camera_motion}, intensità delicata. "
            "Elementi statici: architettura, pergolato, posizione arredi fissi, insegne e linea dell'orizzonte. "
            "Elementi che possono muoversi: lieve increspatura dell'acqua, leggera brezza su tessuti/frange, luce naturale. "
            "Vincoli di autenticità: non deformare geometrie, non inventare elementi fuori campo."
        )
        guard = self.guardian.validate_generation_prompt(prompt, is_real_location=True)
        if not guard["approved"]:
            return {"status": "blocked", "violations": guard["violations"]}

        selection = self.providers.select_provider_for(
            "image_to_video",
            estimated_eur=round(0.35 * duration_sec, 2),
            explicit_user_authorization=explicit_authorization,
        )
        if not selection["authorized"] or not selection["selected"]:
            return {
                "status": "requires_authorization_or_fallback",
                "reason": selection.get("reason"),
                "prompt": prompt,
                "fallback": "Utilizzare Modalità B (Animazione deterministica gratuita su fotografia autentica).",
            }
        return {
            "status": "ready_for_provider_dispatch",
            "provider": selection["selected"]["id"],
            "estimated_eur": selection["cost_eur"],
            "prompt": prompt,
            "requires_human_review": True,
        }

    def generate_illustrative_atmosphere(
        self,
        description: str,
        explicit_authorization: bool = False,
    ) -> dict[str, Any]:
        """Mode D — Non-documentary Mediterranean atmosphere, explicitly flagged as synthetic."""
        guard = self.guardian.validate_generation_prompt(description, is_real_location=False)
        if not guard["approved"]:
            return {"status": "blocked", "violations": guard["violations"]}

        selection = self.providers.select_provider_for(
            "image_generation",
            estimated_eur=0.04,
            explicit_user_authorization=explicit_authorization,
        )
        return {
            "status": "requires_authorization" if not selection["authorized"] else "ready",
            "authenticity_status": AuthenticityStatus.ILLUSTRATIVO_SINTETICO.value,
            "disclosure": "Scena illustrativa non documentaria: non presentare come ripresa reale del locale KORA.",
            "reason": selection.get("reason", ""),
        }
