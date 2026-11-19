"""KORA Media Vault — immutable originals, working copies, visual analysis, and semantic search."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageOps

from kora_studio.config import (
    ASSETS_KORA_DIR,
    VAULT_ORIGINALS_DIR,
    VAULT_THUMBS_DIR,
    VAULT_WORKING_DIR,
    ensure_directories,
)
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import AuthenticityStatus
from kora_studio.media_tools import parse_fps, probe_media, run_ffmpeg

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
SUPPORTED_AUDIO_EXTS = {".wav", ".mp3", ".aac"}
SUPPORTED_GRAPHIC_EXTS = {".svg"}
ALL_SUPPORTED_EXTS = (
    SUPPORTED_IMAGE_EXTS
    | SUPPORTED_VIDEO_EXTS
    | SUPPORTED_AUDIO_EXTS
    | SUPPORTED_GRAPHIC_EXTS
)

CATEGORIES = [
    "Mare",
    "Spiaggia",
    "Pergolato",
    "Ombrelloni",
    "Arredi",
    "Colazione",
    "Aperitivo",
    "Ospitalità",
    "Persone",
    "Dettagli",
    "Luce",
    "Tramonto",
    "Logo",
    "Materiali audio",
]

# Semantic concept lexicon for Italian/English natural-language retrieval
SEMANTIC_CONCEPTS: dict[str, set[str]] = {
    "mare": {"mare", "sea", "onde", "waves", "orizzonte", "horizon", "acqua", "ionio", "blu", "policoro", "costa"},
    "spiaggia": {"spiaggia", "beach", "sabbia", "sand", "lido", "riva", "battigia", "ombrelloni"},
    "pergolato": {"pergolato", "pergola", "cannicciato", "tettoia", "legno", "ombre", "pomeriggio", "struttura", "travi", "lanterna"},
    "ombrelloni": {"ombrellone", "ombrelloni", "parasole", "frange", "macrame", "paglia", "ombra"},
    "arredi": {"arredi", "tavolo", "seduta", "sedia", "sedie", "tavoli", "ulivo", "lanterna", "legno", "mise"},
    "colazione": {"colazione", "breakfast", "mattino", "caffe", "caffè", "tazzina", "cornetto", "croissant", "espresso"},
    "aperitivo": {"aperitivo", "spritz", "bicchiere", "calice", "tramonto", "olive", "drink", "cocktail", "brindisi"},
    "ospitalita": {"ospitalità", "ospitalita", "accoglienza", "servizio", "cura", "tavolo", "preparato", "posto"},
    "persone": {"persone", "ospiti", "amici", "coppia", "convivialità", "insieme", "conversazione", "condivisione", "stare"},
    "dettagli": {"dettaglio", "dettagli", "vicino", "primo", "piano", "texture", "legno", "bicchiere", "tazzina", "intreccio"},
    "luce": {"luce", "sole", "raggi", "dorata", "morbida", "ombre", "ombra", "filtrata", "attraversa", "pomeriggio", "mattino", "chiaroscuro"},
    "tramonto": {"tramonto", "sunset", "sera", "oro", "dorato", "caldo", "crepuscolo", "parasole"},
    "logo": {"logo", "marchio", "kora", "palma", "brand", "payoff"},
    "audio": {"audio", "voce", "musica", "suono", "parlato", "brando", "tiziana", "mp3", "wav"},
}

STOPWORDS_IT = {
    "trova", "una", "un", "uno", "il", "lo", "la", "i", "gli", "le", "con", "che",
    "del", "della", "dello", "dei", "delle", "al", "alla", "nel", "nella", "sul",
    "sulla", "per", "tra", "fra", "di", "a", "da", "in", "e", "o", "foto", "immagine",
    "clip", "video", "mostra", "cerca", "voglio", "vorrei", "mi", "serve", "dove",
}


def _tokenize(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\sàèéìòù]", " ", (text or "").lower())
    return [t for t in cleaned.split() if len(t) > 1 and t not in STOPWORDS_IT]


def _expand_concepts(tokens: list[str]) -> set[str]:
    expanded = set(tokens)
    for concept, synonyms in SEMANTIC_CONCEPTS.items():
        if concept in expanded or expanded & synonyms:
            expanded |= synonyms | {concept}
    return expanded


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class MediaVault:
    """Intelligent, non-destructive media repository for KORA STUDIO."""

    def __init__(self, db: Database) -> None:
        self.db = db
        ensure_directories()

    def _analyze_image(self, path: Path) -> dict[str, Any]:
        """Extract resolution, sharpness, exposure, color cues, and visual tags."""
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                width, height = im.size
                has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
                rgb = im.convert("RGB")
                small = rgb.resize((160, max(1, int(160 * height / max(1, width)))))
                arr = np.asarray(small, dtype=np.float32)
        except Exception as exc:
            raise ValueError(f"Immagine corrotta o non decodificabile ({path.name}): {exc}") from exc

        if width < 32 or height < 32:
            raise ValueError(f"Immagine troppo piccola o non valida: {width}x{height}")

        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        mean_luma = float(np.mean(luma))
        std_luma = float(np.std(luma))

        # Laplacian variance on luminance as sharpness indicator
        if luma.shape[0] > 3 and luma.shape[1] > 3:
            lap = (
                -4 * luma[1:-1, 1:-1]
                + luma[:-2, 1:-1]
                + luma[2:, 1:-1]
                + luma[1:-1, :-2]
                + luma[1:-1, 2:]
            )
            sharpness = float(np.var(lap))
        else:
            sharpness = 0.0

        # Shadow stripe / filtered light metric (high local mid-frequency contrast)
        warmth = float(np.mean(r - b))
        blue_ratio = float(np.mean((b > r + 14) & (b >= g)))
        gold_ratio = float(np.mean((r > 170) & (g > 110) & (b < g - 20)))
        shadow_ratio = float(np.mean(luma < 75))
        bright_ratio = float(np.mean(luma > 185))
        light_shadow_interplay = float(min(1.0, shadow_ratio * bright_ratio * 12.0))

        # Quality score 0..100
        res_score = min(35.0, (min(width, height) / 1080.0) * 35.0)
        sharp_score = min(35.0, math.log1p(sharpness) / math.log1p(1200.0) * 35.0)
        exposure_score = max(0.0, 30.0 - abs(mean_luma - 132.0) * 0.25)
        quality = round(min(100.0, max(15.0, res_score + sharp_score + exposure_score)), 1)

        if width > height * 1.05:
            orientation = "horizontal"
        elif height > width * 1.05:
            orientation = "vertical"
        else:
            orientation = "square"

        return {
            "width": width,
            "height": height,
            "orientation": orientation,
            "has_alpha": has_alpha,
            "technical_quality": quality,
            "metrics": {
                "mean_luma": round(mean_luma, 1),
                "contrast_std": round(std_luma, 1),
                "sharpness": round(sharpness, 1),
                "warmth": round(warmth, 1),
                "blue_ratio": round(blue_ratio, 3),
                "gold_ratio": round(gold_ratio, 3),
                "light_shadow_interplay": round(light_shadow_interplay, 3),
            },
        }

    def _infer_category_and_tags(
        self,
        filename: str,
        media_type: str,
        analysis: dict[str, Any],
        explicit_category: Optional[str] = None,
        extra_tags: Optional[list[str]] = None,
    ) -> tuple[str, list[str], str]:
        name_l = filename.lower()
        tags: set[str] = set(extra_tags or [])
        tokens = _tokenize(Path(filename).stem.replace("_", " ").replace("-", " "))
        tags.update(tokens)

        metrics = analysis.get("metrics", {})
        blue_ratio = metrics.get("blue_ratio", 0.0)
        gold_ratio = metrics.get("gold_ratio", 0.0)
        interplay = metrics.get("light_shadow_interplay", 0.0)
        warmth = metrics.get("warmth", 0.0)

        category = explicit_category if explicit_category in CATEGORIES else None

        if media_type == "audio":
            category = category or "Materiali audio"
            tags.update(["audio", "voce" if "voce" in name_l else "suono"])
        elif "logo" in name_l:
            category = category or "Logo"
            tags.update(["logo", "kora", "brand"])
        elif any(k in name_l for k in ("pergolato", "ombre")):
            category = category or "Pergolato"
            tags.update(["pergolato", "luce", "ombre", "legno", "luce filtrata", "cannicciato"])
        elif any(k in name_l for k in ("tramonto", "aperitivo", "bicchiere")):
            category = category or ("Tramonto" if "tramonto" in name_l else "Aperitivo")
            tags.update(["aperitivo", "tramonto", "luce dorata", "calice", "ospitalità"])
        elif any(k in name_l for k in ("colazione", "mattino", "caffe", "caffè")):
            category = category or "Colazione"
            tags.update(["colazione", "mattino", "caffè", "tazzina", "luce", "mare"])
        elif any(k in name_l for k in ("mare", "policoro", "onde")):
            category = category or "Mare"
            tags.update(["mare", "spiaggia", "orizzonte", "policoro", "acqua"])
        elif any(k in name_l for k in ("tavolo", "seduta", "persone", "ospiti")):
            category = category or ("Persone" if "persone" in name_l else "Arredi")
            tags.update(["ospitalità", "convivialità", "tavolo", "arredi", "stare insieme"])
        elif "dettaglio" in name_l:
            category = category or "Dettagli"
            tags.update(["dettagli", "texture", "luce"])
        elif "ombrellon" in name_l or "parasole" in name_l:
            category = category or "Ombrelloni"
            tags.update(["ombrelloni", "spiaggia", "ombra"])

        # Add visual-feature derived semantic descriptors even when filename is generic (e.g. IMG_1234.jpg)
        if blue_ratio > 0.16:
            tags.update(["mare", "cielo", "blu mediterraneo", "orizzonte"])
            category = category or "Mare"
        if gold_ratio > 0.12 or warmth > 28:
            tags.update(["luce dorata", "tramonto", "luce calda"])
            category = category or "Tramonto"
        if interplay > 0.22:
            tags.update(["luce", "ombre morbide", "luce che attraversa il pergolato", "chiaroscuro"])
            category = category or "Luce"

        if "persone" in name_l:
            tags.update(["persone", "ospiti", "condivisione", "stare insieme"])

        category = category or "Ospitalità"
        semantic_parts = [category, Path(filename).stem, " ".join(sorted(tags))]
        semantic_text = " ".join(semantic_parts).lower()
        return category, sorted(tags), semantic_text

    def _create_working_copy_and_thumb(
        self, asset_id: str, media_type: str, ext: str, orig_path: Path
    ) -> tuple[Optional[Path], Optional[Path]]:
        working_path: Optional[Path] = None
        thumb_path: Optional[Path] = None

        if media_type == "image":
            working_path = VAULT_WORKING_DIR / f"{asset_id}_work.png"
            thumb_path = VAULT_THUMBS_DIR / f"{asset_id}_thumb.jpg"
            with Image.open(orig_path) as im:
                im = ImageOps.exif_transpose(im)
                # Preserve alpha for logos/overlays, RGB for photos
                save_im = im.convert("RGBA") if im.mode in ("RGBA", "LA") else im.convert("RGB")
                save_im.save(working_path, "PNG")
                thumb = im.convert("RGB")
                thumb.thumbnail((640, 960))
                thumb.save(thumb_path, "JPEG", quality=84)
        elif media_type == "video":
            working_path = VAULT_WORKING_DIR / f"{asset_id}_work.mp4"
            shutil.copy2(orig_path, working_path)
            thumb_path = VAULT_THUMBS_DIR / f"{asset_id}_thumb.jpg"
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        "0.5",
                        "-i",
                        str(orig_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=640:-2",
                        str(thumb_path),
                    ],
                    timeout=30,
                )
            except Exception:
                thumb_path = None
        elif media_type == "graphic":
            working_path = VAULT_WORKING_DIR / f"{asset_id}_work{ext}"
            shutil.copy2(orig_path, working_path)
        elif media_type == "audio":
            working_path = VAULT_WORKING_DIR / f"{asset_id}_work.wav"
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(orig_path),
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(working_path),
                    ],
                    timeout=60,
                )
            except Exception:
                shutil.copy2(orig_path, orig_path)
                working_path = orig_path

        return working_path, thumb_path

    def ingest_file(
        self,
        source_path: Path,
        origin: str = "upload_utente",
        authenticity_status: str = AuthenticityStatus.AUTENTICO.value,
        usage_rights: str = "autorizzato_kora",
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        original_filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ingest a media file into KORA Media Vault without modifying the source."""
        source_path = Path(source_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"File non trovato: {source_path}")
        if source_path.stat().st_size == 0:
            raise ValueError(f"File vuoto o corrotto: {source_path.name}")

        display_name = original_filename or source_path.name
        ext = Path(display_name).suffix.lower() or source_path.suffix.lower()
        if ext not in ALL_SUPPORTED_EXTS:
            raise ValueError(f"Formato non supportato: '{ext}'. Formati consentiti: {sorted(ALL_SUPPORTED_EXTS)}")

        sha256 = _compute_sha256(source_path)
        with self.db.session() as conn:
            existing = conn.execute(
                "SELECT * FROM media_assets WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if existing:
                item = self._row_to_dict(existing)
                item["duplicate"] = True
                return item

        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", display_name)
        orig_dest = VAULT_ORIGINALS_DIR / f"{asset_id}_{safe_name}"

        # Validate & analyze before keeping in vault
        media_type: str
        width: Optional[int] = None
        height: Optional[int] = None
        fps: Optional[float] = None
        duration_sec: Optional[float] = None
        orientation = "unknown"
        quality = 80.0
        analysis: dict[str, Any] = {}

        if ext in SUPPORTED_IMAGE_EXTS:
            media_type = "image"
            decode_target = source_path
            converted_heic: Optional[Path] = None
            if ext == ".heic":
                try:
                    import pillow_heif  # type: ignore

                    pillow_heif.register_heif_opener()
                except Exception:
                    converted_heic = VAULT_WORKING_DIR / f"{asset_id}_heic_decode.png"
                    run_ffmpeg(
                        ["-y", "-loglevel", "error", "-i", str(source_path), str(converted_heic)],
                        timeout=30,
                    )
                    decode_target = converted_heic
            analysis = self._analyze_image(decode_target)
            width = analysis["width"]
            height = analysis["height"]
            orientation = analysis["orientation"]
            quality = analysis["technical_quality"]
            shutil.copy2(source_path, orig_dest)
            if converted_heic and converted_heic.is_file():
                working_path, thumb_path = self._create_working_copy_and_thumb(
                    asset_id, "image", ".png", converted_heic
                )
            else:
                working_path, thumb_path = self._create_working_copy_and_thumb(
                    asset_id, "image", ext, orig_dest
                )

        elif ext in SUPPORTED_VIDEO_EXTS:
            media_type = "video"
            probe = probe_media(source_path)
            v_streams = [s for s in probe["streams"] if s.get("codec_type") == "video"]
            if not v_streams:
                raise ValueError(f"Nessuna traccia video valida trovata in {display_name}")
            vs = v_streams[0]
            width = int(vs.get("width", 0) or 0)
            height = int(vs.get("height", 0) or 0)
            fps = round(parse_fps(str(vs.get("r_frame_rate", "25/1"))), 3)
            duration_sec = round(float(probe["format"].get("duration", 0) or 0), 3)
            if width <= 0 or height <= 0 or duration_sec <= 0:
                raise ValueError(f"Metadati video non validi o corrotti in {display_name}")
            orientation = (
                "horizontal" if width > height * 1.05 else ("vertical" if height > width * 1.05 else "square")
            )
            quality = round(min(100.0, 45.0 + min(35.0, (min(width, height) / 1080.0) * 35.0) + min(20.0, fps / 25.0 * 20.0)), 1)
            analysis = {
                "width": width,
                "height": height,
                "fps": fps,
                "duration_sec": duration_sec,
                "has_audio": any(s.get("codec_type") == "audio" for s in probe["streams"]),
                "metrics": {"blue_ratio": 0.2 if "mare" in display_name.lower() else 0.05},
            }
            shutil.copy2(source_path, orig_dest)
            working_path, thumb_path = self._create_working_copy_and_thumb(
                asset_id, "video", ext, orig_dest
            )

        elif ext in SUPPORTED_AUDIO_EXTS:
            media_type = "audio"
            probe = probe_media(source_path)
            a_streams = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
            if not a_streams:
                raise ValueError(f"Nessuna traccia audio valida in {display_name}")
            duration_sec = round(float(probe["format"].get("duration", 0) or 0), 3)
            if duration_sec <= 0:
                raise ValueError(f"Durata audio non valida in {display_name}")
            orientation = "audio"
            quality = 90.0
            analysis = {
                "duration_sec": duration_sec,
                "sample_rate": int(a_streams[0].get("sample_rate", 48000) or 48000),
                "channels": int(a_streams[0].get("channels", 2) or 2),
                "metrics": {},
            }
            shutil.copy2(source_path, orig_dest)
            working_path, thumb_path = self._create_working_copy_and_thumb(
                asset_id, "audio", ext, orig_dest
            )

        else:  # SVG
            media_type = "graphic"
            try:
                tree = ET.parse(source_path)
                root = tree.getroot()
                if "svg" not in root.tag.lower():
                    raise ValueError("Root tag non è SVG")
            except Exception as exc:
                raise ValueError(f"SVG non valido o corrotto ({display_name}): {exc}") from exc
            orientation = "vector"
            quality = 95.0
            analysis = {"vector": True, "metrics": {}}
            shutil.copy2(source_path, orig_dest)
            working_path, thumb_path = self._create_working_copy_and_thumb(
                asset_id, "graphic", ext, orig_dest
            )

        inferred_cat, inferred_tags, semantic_text = self._infer_category_and_tags(
            display_name, media_type, analysis, explicit_category=category, extra_tags=tags
        )
        now = utcnow_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO media_assets (
                    id, filename, sha256, media_type, extension, origin, acquired_at,
                    file_size, duration_sec, orientation, width, height, fps,
                    category, tags_json, technical_quality, authenticity_status,
                    usage_rights, original_path, working_path, thumbnail_path,
                    analysis_json, semantic_text, projects_used_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                """,
                (
                    asset_id,
                    display_name,
                    sha256,
                    media_type,
                    ext,
                    origin,
                    now,
                    orig_dest.stat().st_size,
                    duration_sec,
                    orientation,
                    width,
                    height,
                    fps,
                    inferred_cat,
                    json.dumps(inferred_tags, ensure_ascii=False),
                    quality,
                    authenticity_status,
                    usage_rights,
                    str(orig_dest),
                    str(working_path) if working_path else None,
                    str(thumb_path) if thumb_path else None,
                    json.dumps(analysis, ensure_ascii=False),
                    semantic_text,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
            item = self._row_to_dict(row)
            item["duplicate"] = False
            return item

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "filename": row["filename"],
            "sha256": row["sha256"],
            "media_type": row["media_type"],
            "extension": row["extension"],
            "origin": row["origin"],
            "acquired_at": row["acquired_at"],
            "file_size": row["file_size"],
            "duration_sec": row["duration_sec"],
            "orientation": row["orientation"],
            "width": row["width"],
            "height": row["height"],
            "fps": row["fps"],
            "category": row["category"],
            "tags": json.loads(row["tags_json"]),
            "technical_quality": row["technical_quality"],
            "authenticity_status": row["authenticity_status"],
            "usage_rights": row["usage_rights"],
            "original_path": row["original_path"],
            "working_path": row["working_path"],
            "thumbnail_path": row["thumbnail_path"],
            "analysis": json.loads(row["analysis_json"]),
            "projects_used": json.loads(row["projects_used_json"]),
            "created_at": row["created_at"],
        }

    def list_assets(
        self,
        media_type: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM media_assets WHERE 1=1"
        params: list[Any] = []
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC"
        with self.db.session() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_asset(self, asset_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def get_logo_asset(self) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE category = 'Logo' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def mark_project_usage(self, asset_ids: list[str], project_id: str) -> None:
        with self.db.session() as conn:
            for aid in set(asset_ids):
                row = conn.execute(
                    "SELECT projects_used_json FROM media_assets WHERE id = ?", (aid,)
                ).fetchone()
                if not row:
                    continue
                used = set(json.loads(row["projects_used_json"]))
                used.add(project_id)
                conn.execute(
                    "UPDATE media_assets SET projects_used_json = ? WHERE id = ?",
                    (json.dumps(sorted(used)), aid),
                )

    def semantic_search(
        self,
        query: str,
        media_type: Optional[str] = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Semantic search across vault assets combining concept expansion & visual features."""
        assets = self.list_assets(media_type=media_type)
        if not query or not query.strip():
            return assets[:limit]

        q_tokens = _tokenize(query)
        q_expanded = _expand_concepts(q_tokens)
        q_lower = query.lower()

        scored: list[tuple[float, dict[str, Any]]] = []
        for asset in assets:
            # Never return Logo unless explicitly asked for logo
            if asset["category"] == "Logo" and "logo" not in q_lower and "brand" not in q_lower:
                continue

            doc_tokens = set(_tokenize(asset["filename"].replace("_", " "))) | set(
                t.lower() for t in asset["tags"]
            )
            doc_tokens.add(asset["category"].lower())
            doc_expanded = _expand_concepts(list(doc_tokens))

            direct_hits = len(set(q_tokens) & doc_tokens)
            concept_hits = len(q_expanded & doc_expanded)
            union_size = max(1, len(q_expanded | doc_expanded))
            jaccard = concept_hits / union_size

            score = direct_hits * 3.0 + jaccard * 8.0 + (asset["technical_quality"] / 100.0) * 0.6

            # Visual feature boosts matching natural-language queries
            metrics = asset.get("analysis", {}).get("metrics", {})
            if any(k in q_expanded for k in ("luce", "pergolato", "ombre", "attraversa", "filtrata")):
                score += metrics.get("light_shadow_interplay", 0.0) * 4.5
            if any(k in q_expanded for k in ("mare", "blu", "orizzonte", "spiaggia")):
                score += metrics.get("blue_ratio", 0.0) * 4.0
            if any(k in q_expanded for k in ("tramonto", "dorata", "caldo", "sera")):
                score += metrics.get("gold_ratio", 0.0) * 4.0
            if asset["media_type"] == "video" and ("video" in q_lower or "clip" in q_lower or "mare" in q_lower):
                score += 1.2

            if score > 0.7:
                item = dict(asset)
                item["relevance_score"] = round(score, 3)
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def seed_kora_authentic_assets(self) -> dict[str, int]:
        """Import authentic KORA Beach Club assets from assets/kora/ into Media Vault."""
        imported = 0
        skipped = 0

        logo_path = ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG"
        if logo_path.is_file():
            res = self.ingest_file(
                logo_path,
                origin="kora_brand_originale",
                authenticity_status=AuthenticityStatus.AUTENTICO.value,
                usage_rights="proprietario_kora",
                category="Logo",
                tags=["logo", "kora", "palma", "the art of being"],
            )
            imported += 0 if res.get("duplicate") else 1
            skipped += 1 if res.get("duplicate") else 0

        photos_dir = ASSETS_KORA_DIR / "photos"
        if photos_dir.is_dir():
            for p in sorted(photos_dir.iterdir()):
                if p.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                    res = self.ingest_file(
                        p,
                        origin="archivio_kora_policoro",
                        authenticity_status=AuthenticityStatus.AUTENTICO.value,
                        usage_rights="autorizzato_kora",
                    )
                    imported += 0 if res.get("duplicate") else 1
                    skipped += 1 if res.get("duplicate") else 0

        videos_dir = ASSETS_KORA_DIR / "videos"
        if videos_dir.is_dir():
            for v in sorted(videos_dir.iterdir()):
                if v.suffix.lower() in SUPPORTED_VIDEO_EXTS:
                    res = self.ingest_file(
                        v,
                        origin="riprese_kora_policoro",
                        authenticity_status=AuthenticityStatus.AUTENTICO.value,
                        usage_rights="autorizzato_kora",
                    )
                    imported += 0 if res.get("duplicate") else 1
                    skipped += 1 if res.get("duplicate") else 0

        voices_dir = ASSETS_KORA_DIR / "voices"
        if voices_dir.is_dir():
            for a in sorted(voices_dir.iterdir()):
                if a.suffix.lower() in SUPPORTED_AUDIO_EXTS:
                    res = self.ingest_file(
                        a,
                        origin="voce_kora_autorizzata",
                        authenticity_status=AuthenticityStatus.AUTENTICO.value,
                        usage_rights="autorizzato_kora",
                        category="Materiali audio",
                        tags=["voce", "narratore", a.stem],
                    )
                    imported += 0 if res.get("duplicate") else 1
                    skipped += 1 if res.get("duplicate") else 0

        return {"imported": imported, "skipped": skipped}
