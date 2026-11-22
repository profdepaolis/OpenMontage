"""KORA Brand Brain — versioned brand identity, derived palette, and logo verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from kora_studio.config import ASSETS_KORA_DIR
from kora_studio.db.database import Database, utcnow_iso

FORBIDDEN_COMMERCIAL_PHRASES = [
    "offerta imperdibile",
    "sconto",
    "prenota ora!!!",
    "esclusivo vip",
    "il migliore del mondo",
    "numero uno",
    "lusso sfrenato",
    "incredibile occasione",
    "affrettati",
    "non perdere",
    "prezzo shock",
    "super offerta",
    "piscina infinity",
    "piscina panoramica",
    "terrazza panoramica",
]

DEFAULT_BRAND_VALUES = [
    "Luxury Mediterranean Lifestyle",
    "Eleganza sobria",
    "Ospitalità autentica",
    "Bellezza naturale",
    "Libertà e benessere",
    "Piacere di condividere",
    "Tempo ritrovato",
    "Esperienze semplici ma curate",
    "Connessione con il mare",
    "Atmosfera rilassante e raffinata",
]

DEFAULT_NARRATIVE_RULES = {
    "style": "Frasi brevi, evocative, naturali e misurate.",
    "avoid": [
        "Linguaggio commerciale aggressivo",
        "Slogan generici",
        "Superlativi continui",
        "Narrazione eccessivamente enfatica",
        "Promesse non documentate",
        "Ripetizione sistematica degli stessi concetti",
    ],
    "max_words_per_scene": 14,
    "preferred_languages": ["it"],
}

DEFAULT_AUTHENTICITY_RULES = {
    "preserve": [
        "Architettura originale",
        "Posizione degli arredi fissi",
        "Morfologia della spiaggia",
        "Linea dell'orizzonte",
        "Geometria del pergolato",
        "Materiali e finiture",
        "Insegne",
        "Elementi strutturali",
        "Prospettive realistiche",
        "Identità riconoscibile del locale",
    ],
    "never_invent": [
        "piscine",
        "edifici",
        "terrazze",
        "arredi",
        "installazioni",
        "prodotti",
        "servizi",
    ],
    "require_original_logo": True,
}


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2]))),
    )


def extract_palette_from_authentic_media(
    photo_paths: list[Path], logo_path: Optional[Path] = None
) -> dict[str, Any]:
    """Derive palette hex values directly from authentic approved KORA assets.

    Classifies sampled pixels into KORA semantic color families:
    sabbia_naturale, avorio, legno_caldo, blu_mediterraneo, toni_neutri, luce_dorata, oro_logo.
    """
    if not photo_paths and not (logo_path and logo_path.is_file()):
        return {
            "status": "da_ricavare_da_materiali_autentici",
            "derived_from": [],
            "colors": {},
        }

    buckets: dict[str, list[np.ndarray]] = {
        "sabbia_naturale": [],
        "avorio": [],
        "legno_caldo": [],
        "blu_mediterraneo": [],
        "toni_neutri": [],
        "luce_dorata": [],
    }
    used_files: list[str] = []

    for p in photo_paths[:12]:
        if not p.is_file():
            continue
        try:
            with Image.open(p) as im:
                im_rgb = im.convert("RGB").resize((96, 96))
                arr = np.asarray(im_rgb, dtype=np.float32).reshape(-1, 3)
            used_files.append(p.name)
            r, g, b = arr[:, 0], arr[:, 1], arr[:, 2]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            maxc = np.max(arr, axis=1)
            minc = np.min(arr, axis=1)
            sat = np.where(maxc > 0, (maxc - minc) / (maxc + 1e-5), 0.0)

            # Ivory: bright low-saturation warm highlights
            ivory_mask = (luma > 205) & (sat < 0.18) & (r >= b - 8)
            if np.any(ivory_mask):
                buckets["avorio"].append(arr[ivory_mask])

            # Mediterranean blue: blue-dominant sea/sky tones (not oversaturated neon)
            blue_mask = (b > r + 18) & (b >= g) & (luma > 55) & (luma < 210)
            if np.any(blue_mask):
                buckets["blu_mediterraneo"].append(arr[blue_mask])

            # Golden light: warm sunlit highlights
            gold_mask = (r > 175) & (g > 125) & (g < r) & (b < g - 25) & (sat > 0.25)
            if np.any(gold_mask):
                buckets["luce_dorata"].append(arr[gold_mask])

            # Warm wood: medium-dark warm browns
            wood_mask = (r > g + 12) & (g > b + 10) & (luma >= 65) & (luma <= 155)
            if np.any(wood_mask):
                buckets["legno_caldo"].append(arr[wood_mask])

            # Natural sand: warm mid-bright muted beige
            sand_mask = (r >= g) & (g >= b) & (luma >= 145) & (luma <= 215) & (sat >= 0.08) & (sat <= 0.35)
            if np.any(sand_mask):
                buckets["sabbia_naturale"].append(arr[sand_mask])

            # Neutral stone/shadow tones
            neutral_mask = (sat < 0.14) & (luma >= 45) & (luma <= 180)
            if np.any(neutral_mask):
                buckets["toni_neutri"].append(arr[neutral_mask])
        except Exception:
            continue

    colors: dict[str, dict[str, str]] = {}
    labels = {
        "sabbia_naturale": "Sabbia naturale",
        "avorio": "Avorio",
        "legno_caldo": "Legno caldo",
        "blu_mediterraneo": "Blu mediterraneo",
        "toni_neutri": "Toni neutri",
        "luce_dorata": "Luce dorata naturale",
    }
    for key, samples in buckets.items():
        if samples:
            merged = np.vstack(samples)
            med = np.median(merged, axis=0)
            colors[key] = {
                "label": labels[key],
                "hex": _rgb_to_hex((med[0], med[1], med[2])),
                "source": "ricavato da materiali autentici",
            }

    if logo_path and logo_path.is_file():
        try:
            with Image.open(logo_path) as im:
                rgba = im.convert("RGBA")
                arr = np.asarray(rgba, dtype=np.float32).reshape(-1, 4)
                visible = arr[arr[:, 3] > 128][:, :3]
                if len(visible) > 0:
                    # Exclude near-white background pixels if present
                    non_white = visible[np.mean(visible, axis=1) < 235]
                    sample = non_white if len(non_white) > 0 else visible
                    med = np.median(sample, axis=0)
                    colors["oro_logo"] = {
                        "label": "Oro logo autentico",
                        "hex": _rgb_to_hex((med[0], med[1], med[2])),
                        "source": f"ricavato da {logo_path.name}",
                    }
                    used_files.append(logo_path.name)
        except Exception:
            pass

    return {
        "status": "verificata_da_materiali_autentici" if colors else "da_ricavare",
        "derived_from": sorted(set(used_files)),
        "colors": colors,
    }


class BrandBrain:
    """Persistent, versioned KORA Brand Brain consulted by all agents."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.ensure_initialized()

    def ensure_initialized(self) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM brand_profiles ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row:
                return self._row_to_dict(row)

        photos_dir = ASSETS_KORA_DIR / "photos"
        photo_paths = sorted(photos_dir.glob("*.png")) if photos_dir.is_dir() else []
        logo_file = ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG"
        palette = extract_palette_from_authentic_media(
            photo_paths, logo_file if logo_file.is_file() else None
        )
        profile = {
            "brand_name": "KORA Beach Club",
            "location": "Lungomare destro · Policoro (MT), Basilicata",
            "payoff": "The Art of Being",
            "values": DEFAULT_BRAND_VALUES,
            "luxury_philosophy": (
                "Il lusso non è ostentazione né esclusività artificiale: "
                "emerge dalla qualità autentica dell'esperienza, dalla luce, dal mare e dalla cura."
            ),
            "narrative_rules": DEFAULT_NARRATIVE_RULES,
            "authenticity_rules": DEFAULT_AUTHENTICITY_RULES,
            "palette": palette,
            "logo": {
                "require_original": True,
                "original_filename": logo_file.name if logo_file.is_file() else None,
                "original_path": str(logo_file) if logo_file.is_file() else None,
                "payoff_exact": "The Art of Being",
                "forbid_reconstruction": True,
            },
            "contact": {
                "address": "Lungomare destro · Policoro (MT)",
                "whatsapp": "352 0379963",
            },
        }
        return self.save_version(
            profile=profile,
            updated_by="system_bootstrap",
            note="Inizializzazione KORA Brand Brain con palette ricavata da materiali autentici",
        )

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        data = json.loads(row["profile_json"])
        data["version"] = row["version"]
        data["updated_by"] = row["updated_by"]
        data["note"] = row["note"]
        data["created_at"] = row["created_at"]
        return data

    def get_current(self) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM brand_profiles ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if not row:
                return self.ensure_initialized()
            return self._row_to_dict(row)

    def get_history(self) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM brand_profiles ORDER BY version DESC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def save_version(
        self, profile: dict[str, Any], updated_by: str = "utente", note: str = ""
    ) -> dict[str, Any]:
        # Enforce non-negotiable KORA constants
        profile["payoff"] = "The Art of Being"
        profile.setdefault("brand_name", "KORA Beach Club")
        profile.setdefault("location", "Policoro (MT)")
        logo = profile.setdefault("logo", {})
        logo["payoff_exact"] = "The Art of Being"
        logo["require_original"] = True
        logo["forbid_reconstruction"] = True

        with self.db.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO brand_profiles
                (brand_name, location, payoff, profile_json, updated_by, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["brand_name"],
                    profile["location"],
                    profile["payoff"],
                    json.dumps(profile, ensure_ascii=False),
                    updated_by,
                    note,
                    utcnow_iso(),
                ),
            )
            ver = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM brand_profiles WHERE version = ?", (ver,)
            ).fetchone()
            return self._row_to_dict(row)

    def refresh_palette_from_vault(self) -> dict[str, Any]:
        """Re-derive palette directly from current authentic photos and logo in vault."""
        with self.db.session() as conn:
            photos = conn.execute(
                "SELECT original_path FROM media_assets WHERE media_type='image' AND authenticity_status='autentico' ORDER BY created_at DESC LIMIT 12"
            ).fetchall()
            logo = conn.execute(
                "SELECT original_path FROM media_assets WHERE category='Logo' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        photo_paths = [Path(r["original_path"]) for r in photos]
        logo_path = Path(logo["original_path"]) if logo else None
        if not photo_paths and (ASSETS_KORA_DIR / "photos").is_dir():
            photo_paths = sorted((ASSETS_KORA_DIR / "photos").glob("*.png"))
        if not logo_path and (ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG").is_file():
            logo_path = ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG"

        palette = extract_palette_from_authentic_media(photo_paths, logo_path)
        current = self.get_current()
        current["palette"] = palette
        if logo_path and logo_path.is_file():
            current["logo"]["original_filename"] = logo_path.name
            current["logo"]["original_path"] = str(logo_path)
        return self.save_version(
            current,
            updated_by="brand_brain_sampler",
            note="Aggiornamento palette cromatica da materiali autentici nel Media Vault",
        )

    def rollback(self, target_version: int) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM brand_profiles WHERE version = ?", (target_version,)
            ).fetchone()
            if not row:
                raise ValueError(f"Versione Brand Brain {target_version} non trovata")
            old = self._row_to_dict(row)
        return self.save_version(
            old,
            updated_by="rollback",
            note=f"Ripristino alla versione {target_version}",
        )
