"""v3.3 — stabilità della produzione: brand, outro, durata, cache, proposte.

Verifiche reali (non simulate) su ciò che si può collaudare senza FFmpeg:
niente rendering, ma tutta la logica che decide *cosa* verrà renderizzato.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.brand_guardian import normalize_text
from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, ThemeProposalLite


def _director(isolated_env) -> KoraDirector:
    db = Database(pathlib.Path(isolated_env["db_path"]))
    director = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png", "colazione.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (30 + index * 25, 70, 90)).save(path)
        director.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
            tags=["mare"],
        )
    isolated_env["director"] = director
    isolated_env["assets"] = [a["id"] for a in director.vault.list_assets()]
    return director


def _project(director: KoraDirector, *, seconds: float = 20.0, voice: str = "brando") -> str:
    design = director.design_reel(
        CreativeRequestPayload(theme="La bellezza non fa rumore", duration_sec=seconds, voice_id=voice)
    )
    project = director.create_project_from_design(design, theme="La bellezza non fa rumore")
    return project["id"]


@pytest.fixture()
def env(isolated_env):
    _director(isolated_env)
    return isolated_env


# --------------------------------------------------------------- brand guardian
def test_spa_false_positives_are_not_violations(env):
    guardian = env["director"].guardian
    for text in [
        "Lo spazio tra le sedute resta vuoto.",
        "Spazi ampi, luce bassa, niente rumore.",
        "Un tavolo spazioso ma raccolto.",
        "Il spasmo della fretta non esiste qui.",
    ]:
        result = guardian.validate_copy(text)
        assert result["approved"], f"falso positivo su: {text} -> {result['violations']}"


def test_real_undocumented_services_are_detected(env):
    guardian = env["director"].guardian
    for text, term in [
        ("Una spa con sauna privata.", "spa"),
        ("La piscina esterna al tramonto.", "piscina"),
        ("Due piscine e un rooftop.", "piscina"),
    ]:
        result = guardian.validate_copy(text)
        assert not result["approved"], text
        assert any(term in v for v in result["violations"])
        # il messaggio deve citare il contesto, non solo la parola
        assert any("«" in v for v in result["violations"])


def test_normalization_handles_apostrophes_case_and_accents():
    assert normalize_text("L’Ombra  del   Mare") == "l'ombra del mare"
    assert normalize_text("Perché") == "perche"


# ------------------------------------------------------------------- outro KORA
def test_added_scene_stays_before_the_outro(env):
    director = env["director"]
    pid = _project(director)
    added = director.scene_editor.add_scene(
        pid, {"asset_id": env["assets"][0], "duration_sec": 3.0, "spoken_text": "Una scena in mezzo"}
    )
    rows = added["scenes"]
    assert rows[-1]["is_outro"] is True, "l'outro deve restare l'ultima scena"
    assert [r["is_outro"] for r in rows].count(True) == 1, "una sola chiusura istituzionale"
    assert rows[-2]["spoken_text"] == "Una scena in mezzo"


def test_outro_cannot_be_deleted_by_accident(env):
    director = env["director"]
    pid = _project(director)
    rows = director.scene_editor.list_scenes(pid)["scenes"]
    outro_number = rows[-1]["scene_number"]
    result = director.scene_editor.remove_scene(pid, outro_number)
    if result.get("blocked"):
        assert result["removed"] is None
    assert director.scene_editor.list_scenes(pid)["scenes"][-1]["is_outro"] is True


def test_removing_a_middle_scene_keeps_the_logo_last(env):
    director = env["director"]
    pid = _project(director)
    director.scene_editor.remove_scene(pid, 2)
    rows = director.scene_editor.list_scenes(pid)["scenes"]
    assert rows[-1]["is_outro"] is True
    assert [r["scene_number"] for r in rows] == list(range(1, len(rows) + 1))


# --------------------------------------------------------------- durata vincolante
def test_user_duration_wins_over_ai_recommendation(env):
    director = env["director"]
    proposal = ThemeProposalLite(
        id="p1",
        title="La bellezza non fa rumore",
        hook="x",
        central_message="y",
        narrative_direction="z",
        recommended_duration_sec=30.0,
        recommended_voice_id="tiziana",
    )
    design = director.design_reel(
        CreativeRequestPayload(theme="La bellezza non fa rumore", duration_sec=20.0, voice_id="brando"),
        proposal=proposal,
    )
    assert design.concept.duration_sec == pytest.approx(20.0)
    assert design.concept.recommended_voice_id == "brando", "la voce scelta non viene sostituita"
    total = sum(s.duration_frames for s in design.scenes)
    assert total == 500, f"20 s a 25 fps devono fare 500 frame, trovati {total}"


def test_duration_free_mode_is_explicit(env):
    director = env["director"]
    proposal = ThemeProposalLite(
        id="p2",
        title="Quello che resta",
        hook="x",
        central_message="y",
        narrative_direction="z",
        recommended_duration_sec=30.0,
        recommended_voice_id="brando",
    )
    design = director.design_reel(
        CreativeRequestPayload(
            theme="Quello che resta", duration_sec=20.0, voice_id="brando", duration_locked=False
        ),
        proposal=proposal,
    )
    assert design.concept.duration_sec == pytest.approx(30.0)


def test_frame_math_is_exact_for_required_durations(env):
    director = env["director"]
    for seconds, frames in ((15, 375), (20, 500), (30, 750)):
        design = director.design_reel(
            CreativeRequestPayload(theme="Prova durata", duration_sec=float(seconds))
        )
        assert sum(s.duration_frames for s in design.scenes) == frames


# ------------------------------------------------------------ testo e sottotitoli
def test_subtitles_come_from_the_same_text_as_the_voice(env):
    director = env["director"]
    pid = _project(director)
    for scene in director.scene_editor.list_scenes(pid)["scenes"]:
        if scene["is_outro"] or not scene["spoken_text"]:
            continue
        assert scene["subtitle_text"] == scene["spoken_text"]


def test_manual_text_is_validated(env):
    director = env["director"]
    pid = _project(director)
    with pytest.raises(ValueError):
        director.scene_editor.update_scene(pid, 1, {"spoken_text": "Una spa con idromassaggio."})
    ok = director.scene_editor.update_scene(pid, 1, {"spoken_text": "Lo spazio tra le sedute resta vuoto."})
    assert ok["scenes"][0]["spoken_text"].startswith("Lo spazio")


# ------------------------------------------------------------------ cache audio
def test_audio_is_invalidated_when_text_changes(env):
    director = env["director"]
    pid = _project(director)
    project_dir = pathlib.Path(director.get_project(pid)["id"])  # solo per type check
    from kora_studio.config import PROJECTS_DIR

    mix = PROJECTS_DIR / pid / "audio_mix.wav"
    mix.parent.mkdir(parents=True, exist_ok=True)
    mix.write_bytes(b"RIFF....WAVEfake")

    director.mark_audio_built(pid, note="test")
    assert director.audio_state(pid)["state"] == "aggiornato"

    director.scene_editor.update_scene(pid, 1, {"spoken_text": "Testo nuovo per la prima scena."})
    state = director.audio_state(pid)
    assert state["state"] == "obsoleto"
    assert state["valid"] is False


def test_audio_without_signature_is_not_considered_good(env):
    """Un file che esiste non basta: se è più vecchio dello storyboard è inaffidabile."""
    director = env["director"]
    pid = _project(director)
    import os
    from kora_studio.config import PROJECTS_DIR

    mix = PROJECTS_DIR / pid / "audio_mix.wav"
    mix.parent.mkdir(parents=True, exist_ok=True)
    mix.write_bytes(b"RIFF....WAVEfake")
    old = 1_600_000_000  # ben prima della creazione del progetto
    os.utime(mix, (old, old))
    state = director.audio_state(pid)
    assert state["valid"] is False
    assert state["state"] in {"sconosciuto", "obsoleto"}


# ------------------------------------------------------- proposta vs applicazione
def test_text_suggestion_is_only_a_proposal(env):
    director = env["director"]
    pid = _project(director)
    before = director.scene_editor.list_scenes(pid)["scenes"][1]["spoken_text"]
    proposal = director.scene_editor.suggest_text(pid, 2)
    assert proposal["applied"] is False
    after = director.scene_editor.list_scenes(pid)["scenes"][1]["spoken_text"]
    assert after == before, "la proposta non deve sovrascrivere il testo"
    assert proposal["source"]


def test_apply_text_saves_and_declares_source(env):
    director = env["director"]
    pid = _project(director)
    applied = director.scene_editor.apply_text(pid, 2, "Il vento muove solo le foglie.", source="utente")
    assert applied["applied"] is True
    saved = director.scene_editor.list_scenes(pid)["scenes"][1]
    assert saved["spoken_text"] == "Il vento muove solo le foglie."
    assert saved["subtitle_text"] == saved["spoken_text"]


def test_api_suggestion_then_apply(env):
    director = env["director"]
    pid = _project(director)
    client = TestClient(create_app(db_path=env["db_path"], auto_seed=False))
    suggestion = client.post(f"/api/projects/{pid}/scenes/2/text", json={})
    assert suggestion.status_code == 200
    body = suggestion.json()
    assert body["applied"] is False
    applied = client.post(
        f"/api/projects/{pid}/scenes/2/text/apply", json={"instruction": body["text"]}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True


# --------------------------------------------------------------- prontezza/render
def test_readiness_separates_preview_and_render(env):
    director = env["director"]
    pid = _project(director)
    ready = director.preview_readiness(pid)
    ids = {c["id"] for c in ready["checks"]}
    assert {"ffmpeg", "outro", "duration", "subtitle_source", "audio"} <= ids
    assert "render_ready" in ready
    assert ready["total_frames"] in (500, ready["total_frames"])  # 20 s -> 500 frame
    assert ready["total_frames"] == 500


# --------------------------------------------------------------- motori e budget
def test_gemini_invalid_key_does_not_overwrite_previous(env, monkeypatch):
    director = env["director"]
    from kora_studio.ai_engine import adapters_http

    director.ai_engine.providers.set_api_key("gemini_api", "AIza-vecchia")
    monkeypatch.setattr(
        adapters_http.GeminiAdapter, "authenticated", lambda self: {"authenticated": False, "detail": "401"}
    )
    with pytest.raises(ValueError):
        director.ai_engine.connect_gemini_api("AIza-sbagliata")
    assert director.ai_engine.providers.get_decrypted_key("gemini_api") == "AIza-vecchia"


def test_gemini_connect_does_not_declare_free_quota(env, monkeypatch):
    director = env["director"]
    from kora_studio.ai_engine import adapters_http
    from kora_studio.ai_engine.base import BILLING_UNKNOWN, ModelInfo

    monkeypatch.setattr(
        adapters_http.GeminiAdapter, "authenticated", lambda self: {"authenticated": True, "detail": "ok"}
    )
    monkeypatch.setattr(
        adapters_http.GeminiAdapter,
        "list_models",
        lambda self: [
            ModelInfo(id="gemini-2.0-flash-lite", label="Flash-Lite", provider="gemini_api", billing=BILLING_UNKNOWN)
        ],
    )
    result = director.ai_engine.connect_gemini_api("AIza-buona")
    assert result["ok"] is True
    assert result["billing"] == "non_verificabile"
    assert result["cost_verified"] is False
    assert not (director.ai_engine.settings().get("declared_free_quota") or {}).get("gemini_api")


def test_design_works_without_any_engine(env):
    director = env["director"]
    design = director.design_reel(CreativeRequestPayload(theme="Nessun motore", duration_sec=20))
    assert design.generation_engine
    assert "deterministic" in design.generation_engine
    assert sum(s.duration_frames for s in design.scenes) == 500
