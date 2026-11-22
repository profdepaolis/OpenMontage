"""Tests for KORA Brand Brain, Brand Guardian, and Media Vault."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import ASSETS_KORA_DIR
from kora_studio.db.database import Database
from kora_studio.vault.media_vault import MediaVault


def test_brand_brain_versioning_rollback_and_palette(isolated_env):
    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    v1 = brain.get_current()
    assert v1["payoff"] == "The Art of Being"
    assert v1["version"] == 1

    # Update & rollback
    updated = dict(v1)
    updated["luxury_philosophy"] = "Silenzio, sabbia calda e luce del mare Ionio."
    v2 = brain.save_version(updated, note="Test edit")
    assert v2["version"] == 2
    assert "sabbia calda" in v2["luxury_philosophy"]

    rolled = brain.rollback(1)
    assert rolled["version"] == 3
    assert rolled["luxury_philosophy"] == v1["luxury_philosophy"]
    assert len(brain.get_history()) == 3

    # Palette extraction from real KORA photos
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    palette_data = brain.refresh_palette_from_vault()
    assert palette_data["palette"]["status"] == "verificata_da_materiali_autentici"
    assert len(palette_data["palette"]["derived_from"]) >= 1
    assert palette_data["palette"]["colors"]["blu_mediterraneo"]["hex"].startswith("#")


def test_brand_guardian_rejects_aggressive_copy_and_unverified_logo(isolated_env):
    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    guardian = BrandGuardian(brain)

    bad = guardian.validate_copy("OFFERTA IMPERDIBILE -50% PRENOTA ORA!")
    assert not bad["approved"]
    assert len(bad["violations"]) >= 1

    good = guardian.validate_copy(
        "La luce del mattino sfiora il legno caldo. KORA — The Art of Being."
    )
    assert good["approved"]

    logo_missing = guardian.validate_logo_asset(None)
    assert not logo_missing["approved"]
    assert "LOGO KORA.PNG" in logo_missing["error"]

    logo_path = str(ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG")
    logo_ok = guardian.validate_logo_asset(logo_path)
    assert logo_ok["approved"]


def test_media_vault_immutability_dedup_and_semantic_search(isolated_env):
    db = Database(isolated_env["db_path"])
    vault = MediaVault(db)

    # Create sample image outside vault
    src = isolated_env["tmp_path"] / "sample_sunset.png"
    Image.new("RGB", (900, 1600), (218, 175, 118)).save(src)
    orig_hash = hashlib.sha256(src.read_bytes()).hexdigest()

    asset1 = vault.ingest_file(src, origin="test_upload", tags=["tramonto", "aperitivo"])
    assert asset1["sha256"] == orig_hash
    assert asset1["orientation"] == "vertical"
    assert Path(asset1["original_path"]).is_file()
    assert Path(asset1["working_path"]).is_file()
    assert Path(asset1["thumbnail_path"]).is_file()
    # Original file bytes preserved exactly
    assert hashlib.sha256(Path(asset1["original_path"]).read_bytes()).hexdigest() == orig_hash

    # Deduplication by SHA-256
    asset2 = vault.ingest_file(src, origin="duplicate_upload")
    assert asset2["id"] == asset1["id"]

    # Seed real KORA pack and test semantic search in Italian for concepts not in filename
    seeded = vault.seed_kora_authentic_assets()
    assert seeded["imported"] >= 12
    results_mare = vault.semantic_search("onda spiaggia mare")
    assert any("mare" in r["filename"].lower() or "mare" in r["tags"] for r in results_mare)
    results_logo = vault.semantic_search("brand logo")
    assert any(r["category"].lower() == "logo" for r in results_logo)
    results_pergolato = vault.semantic_search("luce filtrata cannicciato")
    assert len(results_pergolato) >= 1
