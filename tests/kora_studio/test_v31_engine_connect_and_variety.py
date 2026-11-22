"""v3.1 — collegamento del motore in-app e varietà reale della direzione creativa.

Copre due requisiti dell'utente:
1) Gemini AI Studio si collega dalla UI (nessun curl): endpoint unico che valida,
   salva, sceglie il modello, dichiara la quota gratuita e attiva il motore.
2) Hook e voice-over non sono più gli stessi 4 template: la direzione locale
   ruota su angoli diversi e ricorda quelli già usati.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from kora_studio.agents.creative_director import STORY_ANGLES
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload


def _build_director(isolated_env):
    from kora_studio.agents.director import KoraDirector

    db = Database(pathlib.Path(isolated_env["db_path"]))
    director = KoraDirector(db=db)
    director.vault.seed_kora_authentic_assets()
    isolated_env["director"] = director
    return director


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KORA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KORA_ENV_FILE", str(tmp_path / ".env"))
    return {"db_path": tmp_path / "kora.db"}


def test_angle_library_is_not_the_same_four():
    assert len(STORY_ANGLES) >= 12
    hooks = [angle["hook_tpl"] for angle in STORY_ANGLES]
    assert len(set(hooks)) == len(hooks), "ogni angolo deve avere un hook diverso"
    for angle in STORY_ANGLES:
        assert len(angle["arc_tpl"]) >= 3, "il voice-over non può essere di una riga"


def test_deterministic_concepts_rotate_and_never_repeat_a_hook(isolated_env):
    director = _build_director(isolated_env)
    seen: list[str] = []
    for _ in range(3):
        env = director.creative.suggest_four_themes_lite(CreativeRequestPayload(duration_sec=20))
        hooks = [p.hook for p in env.proposals]
        assert len(hooks) == 4
        assert len(set(hooks)) == 4, "quattro proposte devono avere quattro hook distinti"
        assert len({p.title for p in env.proposals}) == 4
        seen.extend(hooks)
    # tre richieste consecutive non ripropongono gli stessi quattro copioni
    assert len(set(seen)) >= 8


def test_voiceover_is_substantial(isolated_env):
    director = _build_director(isolated_env)
    env = director.creative.suggest_four_themes_lite(CreativeRequestPayload(duration_sec=20))
    for proposal in env.proposals:
        script = f"{proposal.hook} {proposal.central_message} {proposal.narrative_direction}"
        words = len(script.split())
        assert words >= 25, f"voice-over troppo scarno ({words} parole)"


def test_reel_script_variants_rotate_for_same_theme(isolated_env):
    director = _build_director(isolated_env)
    hooks = set()
    for _ in range(3):
        design = director.design_reel(
            CreativeRequestPayload(theme="Experience & Hospitality", duration_sec=25)
        )
        hooks.add(design.concept.hook)
    assert len(hooks) >= 2, "lo stesso tema non deve produrre lo stesso copione"


def test_connect_options_lists_real_engines(isolated_env):
    _build_director(isolated_env)
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    res = client.get("/api/engines/connect-options")
    assert res.status_code == 200, res.text
    ids = {option["id"] for option in res.json()["options"]}
    assert {"gemini_api", "codex", "opencode"} <= ids


def test_connect_gemini_rejects_empty_key(isolated_env):
    _build_director(isolated_env)
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    res = client.post("/api/engines/gemini/connect", json={"api_key": "  "})
    assert res.status_code == 400
    assert "API key" in res.json()["detail"]


def test_connect_gemini_reports_invalid_key_without_activating(isolated_env, monkeypatch):
    director = _build_director(isolated_env)
    from kora_studio.ai_engine import adapters_http

    monkeypatch.setattr(
        adapters_http.GeminiAdapter,
        "authenticated",
        lambda self: {"authenticated": False, "detail": "API key non valida"},
    )
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    res = client.post("/api/engines/gemini/connect", json={"api_key": "AIza-finta"})
    assert res.status_code == 400
    assert "non valida" in res.json()["detail"]
    assert director.ai_engine.essential().get("active_provider") != "gemini_api"


def test_connect_gemini_does_not_switch_the_creative_engine(isolated_env, monkeypatch):
    """3.6.1 — collegare una chiave non cambia il motore creativo senza consenso."""
    director = _build_director(isolated_env)
    from kora_studio.ai_engine import adapters_http
    from kora_studio.ai_engine.base import BILLING_UNKNOWN, ModelInfo

    monkeypatch.setattr(
        adapters_http.GeminiAdapter,
        "authenticated",
        lambda self: {"authenticated": True, "detail": "ok"},
    )
    monkeypatch.setattr(
        adapters_http.GeminiAdapter,
        "list_models",
        lambda self: [
            ModelInfo(
                id="gemini-2.0-flash-lite",
                label="Gemini Flash-Lite",
                provider="gemini_api",
                billing=BILLING_UNKNOWN,
                execution="remote",
                auth_type="api_key",
            )
        ],
    )
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    res = client.post("/api/engines/gemini/connect", json={"api_key": "AIza-test"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["model"] == "gemini-2.0-flash-lite"
    # la chiave è salvata e verificata, ma OpenCode resta il motore attivo
    assert data["activated"] is False
    assert director.ai_engine.essential()["active_provider"] == "opencode"
    assert director.ai_engine.provider_enabled("gemini_api") is False

    # v3.3: la quota gratuita non è più dichiarata d'ufficio
    assert data["billing"] == "non_verificabile"
    assert data["cost_verified"] is False
    assert not (director.ai_engine.settings().get("declared_free_quota") or {}).get("gemini_api")
    routing = client.get("/api/ai-engine/routing?agent_id=creative_director")
    assert routing.status_code == 200
