"""Media Curator & AI Media Lab — authentic-first visual preparation & detail/motion tools."""

from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import requests
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

    def execute_veo_video(
        self,
        mode: str,
        prompt: str,
        duration_sec: int = 5,
        asset_id: Optional[str] = None,
        camera_motion: str = "movimento lento e naturale",
        explicit_authorization: bool = False,
    ) -> dict[str, Any]:
        """Execute real Google Veo / Flow Image-to-Video (Mode C) or Text-to-Video (Mode D)
        using official Gemini/Veo REST API, with strict BrandGuardian & CostGuard checks.
        """
        if duration_sec not in (3, 4, 5, 6, 8):
            raise ValueError("Durata consentita per Veo: 3, 4, 5, 6 o 8 secondi.")

        is_i2v = mode == "image_to_video"
        if is_i2v and not asset_id:
            raise ValueError("Seleziona una fotografia reale dal Media Vault per Image-to-Video.")

        asset = self.vault.get_asset(asset_id) if asset_id else None
        if is_i2v and (not asset or asset["media_type"] != "image"):
            raise ValueError("Image-to-Video richiede un'immagine valida nel Media Vault.")

        final_prompt = (
            (
                f"Photorealistic slow subtle animation of the exact reference photograph ({asset['filename']}). "
                f"Camera motion: {camera_motion}. "
                "Keep architecture, pergola geometry, fixed furniture placement, signs, and horizon strictly unchanged. "
                "Only gentle breeze on fabric/foliage, soft water ripples, and natural Mediterranean light. "
                "Do not invent pools, buildings, terraces, or extra objects. "
                f"Extra direction: {prompt}".strip()
            )
            if is_i2v and asset
            else (
                "Mediterranean luxury coastal atmosphere, natural sunlight, calm sea breeze, "
                "warm sand and neutral linen tones, photorealistic cinematic 9:16 video. "
                "Non-documentary atmospheric scene (do not depict specific invented club structures). "
                f"{prompt}".strip()
            )
        )

        guard = self.guardian.validate_generation_prompt(final_prompt, is_real_location=is_i2v)
        if not guard["approved"]:
            return {"status": "blocked", "violations": guard["violations"]}

        est_eur = round(0.35 * duration_sec, 2)
        auth = self.providers.cost_guard.check_authorization(
            est_eur, explicit_user_authorization=explicit_authorization
        )
        if not auth["authorized"]:
            return {
                "status": "requires_authorization",
                "reason": auth["reason"],
                "estimated_eur": est_eur,
                "prompt": final_prompt,
            }

        key = self.providers.get_decrypted_key("google_veo_api") or self.providers.get_decrypted_key("gemini_api")
        if not key:
            return {
                "status": "missing_credentials",
                "reason": "Chiave Google Gemini / Veo non configurata in AI Providers.",
            }

        # Build predictLongRunning payload for Google Veo API
        instance: dict[str, Any] = {"prompt": final_prompt}
        if is_i2v and asset:
            img_path = Path(asset["working_path"] or asset["original_path"])
            mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            instance["image"] = {"bytesBase64Encoded": b64, "mimeType": mime}

        payload = {
            "instances": [instance],
            "parameters": {
                "aspectRatio": "9:16",
                "durationSeconds": min(8, max(5, duration_sec)),
            },
        }

        models_to_try = ["veo-2.0-generate-001", "veo-3.0-generate-preview"]
        op_name: Optional[str] = None
        last_err = ""
        used_model = models_to_try[0]
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning"
            r = requests.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if r.status_code == 200:
                op_name = r.json().get("name")
                used_model = model
                break
            last_err = f"{model} HTTP {r.status_code}: {r.text[:240]}"

        if not op_name:
            self.providers.cost_guard.record_usage(
                "google_veo_api", mode, est_eur, 0.0, True, "error", detail=last_err
            )
            raise RuntimeError(f"Google Veo API non ha avviato la generazione: {last_err}")

        # Poll operation up to 180s
        video_uri: Optional[str] = None
        deadline = time.monotonic() + 180
        op_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}"
        while time.monotonic() < deadline:
            time.sleep(5)
            pr = requests.get(op_url, headers={"x-goog-api-key": key}, timeout=20)
            if pr.status_code != 200:
                continue
            pdata = pr.json()
            if pdata.get("done"):
                if pdata.get("error"):
                    raise RuntimeError(f"Veo error: {pdata['error']}")
                resp = pdata.get("response", {})
                samples = (
                    resp.get("generateVideoResponse", {}).get("generatedSamples")
                    or resp.get("predictions")
                    or []
                )
                if samples:
                    v_obj = samples[0].get("video") or samples[0]
                    video_uri = v_obj.get("uri")
                break

        if not video_uri:
            raise RuntimeError("Timeout o nessuna URI video restituita da Google Veo API.")

        dl = requests.get(video_uri, headers={"x-goog-api-key": key}, timeout=90)
        if dl.status_code != 200:
            raise RuntimeError(f"Impossibile scaricare il video Veo generato ({dl.status_code}).")

        out_mp4 = CACHE_DIR / f"veo_{mode}_{uuid.uuid4().hex[:8]}.mp4"
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        out_mp4.write_bytes(dl.content)

        self.providers.cost_guard.record_usage(
            "google_veo_api", mode, est_eur, est_eur, True, "completed", detail=used_model
        )

        auth_status = (
            AuthenticityStatus.GENERATIVO_CONTROLLATO.value
            if is_i2v
            else AuthenticityStatus.ILLUSTRATIVO_SINTETICO.value
        )
        ingested = self.vault.ingest_file(
            out_mp4,
            origin=f"veo_flow:{used_model}" + (f":{asset_id}" if asset_id else ""),
            authenticity_status=auth_status,
            usage_rights="autorizzato_kora",
            category="Mare" if not is_i2v else (asset["category"] if asset else "Ospitalità"),
            tags=["veo", mode, "da_verificare_umano"] + ([asset["category"].lower()] if asset else ["illustrativo"]),
            original_filename=f"veo_{mode}_{uuid.uuid4().hex[:4]}.mp4",
        )
        return {
            "status": "completed",
            "model": used_model,
            "mode": mode,
            "authenticity_status": auth_status,
            "requires_human_review": True,
            "estimated_eur": est_eur,
            "asset": ingested,
        }

    def execute_ai_video(
        self,
        engine: str,
        mode: str,
        prompt: str,
        duration_sec: int = 5,
        asset_id: Optional[str] = None,
        camera_motion: str = "movimento lento e naturale",
        model: Optional[str] = None,
        explicit_authorization: bool = False,
    ) -> dict[str, Any]:
        """Unified Image-to-Video (Mode C) and Text-to-Video (Mode D) executor across
        Google Veo/Flow, fal.ai (Wan 2.1 / Kling / LTX-Video), Alibaba Qwen/Wan, and Hugging Face.
        """
        if engine == "google_veo_api":
            return self.execute_veo_video(
                mode=mode,
                prompt=prompt,
                duration_sec=duration_sec,
                asset_id=asset_id,
                camera_motion=camera_motion,
                explicit_authorization=explicit_authorization,
            )

        is_i2v = mode == "image_to_video"
        if is_i2v and not asset_id:
            raise ValueError("Seleziona una fotografia reale dal Media Vault per Image-to-Video.")
        asset = self.vault.get_asset(asset_id) if asset_id else None
        if is_i2v and (not asset or asset["media_type"] != "image"):
            raise ValueError("Image-to-Video richiede un'immagine valida nel Media Vault.")

        final_prompt = (
            (
                f"Photorealistic slow subtle animation of the exact reference photograph ({asset['filename']}). "
                f"Camera motion: {camera_motion}. "
                "Keep architecture, pergola geometry, fixed furniture placement, signs, and horizon strictly unchanged. "
                "Only gentle breeze on fabric/foliage, soft water ripples, and natural Mediterranean light. "
                "Do not invent pools, buildings, terraces, or extra objects. "
                f"Extra direction: {prompt}".strip()
            )
            if is_i2v and asset
            else (
                "Mediterranean luxury coastal atmosphere, natural sunlight, calm sea breeze, "
                "warm sand and neutral linen tones, photorealistic cinematic 9:16 video. "
                "Non-documentary atmospheric scene (do not depict specific invented club structures). "
                f"{prompt}".strip()
            )
        )

        guard = self.guardian.validate_generation_prompt(final_prompt, is_real_location=is_i2v)
        if not guard["approved"]:
            return {"status": "blocked", "violations": guard["violations"]}

        est_by_engine = {
            "fal_ai": 0.05 if (model and "ltx" in model) else (0.30 if (model and "kling" in model) else 0.20),
            "qwen_wan_api": 0.22,
            "huggingface_api": 0.0,
        }
        if engine not in est_by_engine:
            raise ValueError(f"Motore video non supportato: {engine}")

        est_eur = est_by_engine[engine]
        auth = self.providers.cost_guard.check_authorization(
            est_eur, explicit_user_authorization=explicit_authorization
        )
        if not auth["authorized"]:
            return {
                "status": "requires_authorization",
                "reason": auth["reason"],
                "estimated_eur": est_eur,
                "prompt": final_prompt,
            }

        key = self.providers.get_decrypted_key(engine)
        if not key:
            return {
                "status": "missing_credentials",
                "reason": f"Chiave API per '{engine}' non configurata in Provider AI.",
            }

        img_data_uri: Optional[str] = None
        img_bytes: Optional[bytes] = None
        if is_i2v and asset:
            img_path = Path(asset["working_path"] or asset["original_path"])
            mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            img_bytes = img_path.read_bytes()
            img_data_uri = f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}"

        video_bytes: Optional[bytes] = None
        used_model = model or ""

        if engine == "fal_ai":
            if not used_model:
                used_model = "fal-ai/wan-i2v" if is_i2v else "fal-ai/wan-t2v"
            payload: dict[str, Any] = {
                "prompt": final_prompt,
                "aspect_ratio": "9:16",
            }
            if is_i2v and img_data_uri:
                payload["image_url"] = img_data_uri
            if "kling" in used_model:
                payload["duration"] = "5" if duration_sec <= 5 else "10"
            submit = requests.post(
                f"https://queue.fal.run/{used_model}",
                headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if submit.status_code not in (200, 201, 202):
                raise RuntimeError(f"fal.ai errore avvio ({submit.status_code}): {submit.text[:220]}")
            sdata = submit.json()
            status_url = sdata.get("status_url")
            response_url = sdata.get("response_url")
            video_url = (sdata.get("video") or {}).get("url")
            deadline = time.monotonic() + 240
            while not video_url and status_url and time.monotonic() < deadline:
                time.sleep(4)
                st = requests.get(status_url, headers={"Authorization": f"Key {key}"}, timeout=20)
                if st.status_code == 200 and st.json().get("status") == "COMPLETED":
                    rr = requests.get(response_url, headers={"Authorization": f"Key {key}"}, timeout=20)
                    if rr.status_code == 200:
                        video_url = (rr.json().get("video") or {}).get("url")
                    break
                if st.status_code == 200 and st.json().get("status") == "FAILED":
                    raise RuntimeError(f"fal.ai generazione fallita: {st.text[:200]}")
            if not video_url:
                raise RuntimeError("Timeout o nessun URL video restituito da fal.ai.")
            dl = requests.get(video_url, timeout=90)
            if dl.status_code != 200:
                raise RuntimeError(f"Download video fal.ai fallito ({dl.status_code}).")
            video_bytes = dl.content

        elif engine == "qwen_wan_api":
            used_model = model or ("wan2.1-i2v-turbo" if is_i2v else "wan2.1-t2v-turbo")
            inp: dict[str, Any] = {"prompt": final_prompt}
            if is_i2v and img_data_uri:
                inp["img_url"] = img_data_uri
            submit = requests.post(
                "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                },
                json={
                    "model": used_model,
                    "input": inp,
                    "parameters": {"size": "720*1280" if "t2v" in used_model else "720P", "duration": 5},
                },
                timeout=30,
            )
            if submit.status_code != 200:
                raise RuntimeError(f"Alibaba Qwen/Wan errore ({submit.status_code}): {submit.text[:220]}")
            task_id = (submit.json().get("output") or {}).get("task_id")
            if not task_id:
                raise RuntimeError(f"Task ID mancante da DashScope: {submit.text[:200]}")
            video_url = None
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                time.sleep(5)
                tr = requests.get(
                    f"https://dashscope-intl.aliyuncs.com/api/v1/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=20,
                )
                if tr.status_code != 200:
                    continue
                out = tr.json().get("output") or {}
                if out.get("task_status") == "SUCCEEDED":
                    video_url = out.get("video_url")
                    break
                if out.get("task_status") in ("FAILED", "CANCELED"):
                    raise RuntimeError(f"DashScope task fallito: {out}")
            if not video_url:
                raise RuntimeError("Timeout Alibaba Qwen/Wan Video.")
            dl = requests.get(video_url, timeout=90)
            if dl.status_code != 200:
                raise RuntimeError(f"Download video Alibaba Wan fallito ({dl.status_code}).")
            video_bytes = dl.content

        elif engine == "huggingface_api":
            used_model = model or ("Wan-AI/Wan2.1-I2V-14B-480P" if is_i2v else "Wan-AI/Wan2.1-T2V-14B")
            hf_payload: dict[str, Any] = {"inputs": final_prompt}
            if is_i2v and img_bytes:
                hf_payload["inputs"] = base64.b64encode(img_bytes).decode("ascii")
                hf_payload["parameters"] = {"prompt": final_prompt}
            r = requests.post(
                f"https://router.huggingface.co/hf-inference/models/{used_model}",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=hf_payload,
                timeout=180,
            )
            if r.status_code != 200:
                raise RuntimeError(f"Hugging Face errore ({r.status_code}): {r.text[:220]}")
            if not r.headers.get("content-type", "").startswith(("video/", "application/octet-stream")):
                raise RuntimeError(f"Hugging Face ha risposto senza file video: {r.text[:200]}")
            video_bytes = r.content

        if not video_bytes:
            raise RuntimeError("Nessun contenuto video restituito dal provider selezionato.")

        out_mp4 = CACHE_DIR / f"{engine}_{mode}_{uuid.uuid4().hex[:8]}.mp4"
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        out_mp4.write_bytes(video_bytes)

        self.providers.cost_guard.record_usage(
            engine, mode, est_eur, est_eur, True, "completed", detail=used_model
        )
        auth_status = (
            AuthenticityStatus.GENERATIVO_CONTROLLATO.value
            if is_i2v
            else AuthenticityStatus.ILLUSTRATIVO_SINTETICO.value
        )
        ingested = self.vault.ingest_file(
            out_mp4,
            origin=f"{engine}:{used_model}" + (f":{asset_id}" if asset_id else ""),
            authenticity_status=auth_status,
            usage_rights="autorizzato_kora",
            category="Mare" if not is_i2v else (asset["category"] if asset else "Ospitalità"),
            tags=[engine, mode, "da_verificare_umano"] + ([asset["category"].lower()] if asset else ["illustrativo"]),
            original_filename=f"{engine}_{mode}_{uuid.uuid4().hex[:4]}.mp4",
        )
        return {
            "status": "completed",
            "engine": engine,
            "model": used_model,
            "mode": mode,
            "authenticity_status": auth_status,
            "requires_human_review": True,
            "estimated_eur": est_eur,
            "asset": ingested,
        }
