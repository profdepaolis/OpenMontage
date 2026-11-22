"""v3.4 — recupero del JSON dei modelli (Gemini/Codex/OpenCode).

Il messaggio «JSON non valido (JSONDecodeError)» nasceva da un parsing ingenuo:
primo `{` … ultimo `}` con `json.loads`. Qui si verifica il recupero reale
(recinti di codice, prosa, virgole finali, risposte troncate) e il
comportamento del progetto quando il modello sbaglia davvero.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.json_extract import JsonExtractionError, looks_cut_off, parse_json_object
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload


# ------------------------------------------------------------------ parser unit
def test_parses_json_inside_code_fence():
    raw = '```json\n{"a": 1, "b": [1, 2]}\n```'
    assert parse_json_object(raw) == {"a": 1, "b": [1, 2]}


def test_parses_json_surrounded_by_prose():
    raw = 'Ecco il progetto richiesto:\n{"concept": {"title": "X"}}\nSpero vada bene.'
    assert parse_json_object(raw)["concept"]["title"] == "X"


def test_repairs_trailing_commas():
    assert parse_json_object('{"a": 3, "b": [1, 2,],}') == {"a": 3, "b": [1, 2]}


def test_repairs_truncated_response():
    raw = '{"concept": {"title": "X", "hook": "y"}, "scenes": [{"visual_goal": "a"'
    parsed = parse_json_object(raw)
    assert parsed["concept"]["title"] == "X"
    assert parsed["scenes"][0]["visual_goal"] == "a"
    assert looks_cut_off(raw) is True


def test_repairs_typographic_quotes_and_newlines():
    assert parse_json_object('{\u201ca\u201d: 5}') == {"a": 5}
    assert parse_json_object('{"a": "riga1\nriga2"}')["a"] == "riga1 riga2"


def test_raises_clear_error_without_json():
    with pytest.raises(JsonExtractionError) as info:
        parse_json_object("nessun json qui")
    assert info.value.excerpt


def test_required_keys_is_enforced():
    with pytest.raises(JsonExtractionError):
        parse_json_object('{"scenes": []}', required_keys={"concept"})


# ------------------------------------------------------------- design con engine
class FakeEngine:
    """Motore finto: restituisce risposte prefissate, conta le chiamate."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    def fast_entry(self, agent_id: str):
        return {"provider": "gemini_api", "model": "gemini-2.5-flash", "zero_cost": True}

    def call(self, agent_id: str, messages, **kwargs):  # noqa: ANN001 - firma del manager
        index = min(self.calls, len(self.replies) - 1)
        self.calls += 1
        return SimpleNamespace(
            text=self.replies[index],
            timings={"inference": 12.0},
            latency_ms=12,
            provider="gemini_api",
            model="gemini-2.5-flash",
            call_id=f"call-{self.calls}",
        )


def _director(isolated_env) -> KoraDirector:
    db = Database(pathlib.Path(isolated_env["db_path"]))
    director = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (35 + index * 20, 65, 95)).save(path)
        director.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
            tags=["mare"],
        )
    isolated_env["director"] = director
    return director


def _payload(director: KoraDirector, scenes: int = 5) -> dict:
    ids = [a["id"] for a in director.vault.list_assets()]
    return {
        "concept": {
            "title": "La bellezza non fa rumore",
            "hook": "C'è un'ora in cui il mare non chiede nulla.",
            "central_message": "L'ospitalità si misura nei dettagli che non si vedono.",
            "narrative_arc": "Apertura → dettaglio → chiusura",
            "voiceover_script": (
                "C'è un'ora in cui il mare non chiede nulla. "
                "La luce arriva prima delle parole. "
                "Il resto lo fa il tempo. "
                "The Art of Being."
            ),
            "visual_mood": "Toni caldi, ombre lunghe",
            "objective": "Brand Identity",
            "primary_emotion": "Calma",
            "ending": "KORA. The Art of Being.",
        },
        "scenes": [
            {
                "visual_goal": "Dettaglio reale",
                "asset_id": ids[i % len(ids)],
                "framing": "piano_medio",
                "camera_motion": "slow_push_in",
                "transition": "cross_dissolve",
                "subtitle_text": "La luce arriva prima delle parole.",
            }
            for i in range(scenes)
        ],
        "sound_design": {"music_mood": "cinematic", "texture": "mare", "mix_note": "leggero"},
        "cover": {"title": "La bellezza non fa rumore", "subtitle": "KORA"},
        "caption": "C'è un'ora in cui il mare non chiede nulla. #KORA",
    }


def test_design_uses_fenced_json_from_engine(isolated_env):
    director = _director(isolated_env)
    payload = _payload(director)
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    director.ai_engine = FakeEngine([fenced])

    design = director.design_reel(CreativeRequestPayload(theme="La bellezza non fa rumore", duration_sec=20))
    assert design.generation_engine.startswith("ai_engine:"), design.fallback_reason
    assert design.scenes, "il progetto deve avere scene"
    assert sum(s.duration_frames for s in design.scenes) == 500


def test_truncated_json_is_repaired_without_second_call(isolated_env):
    director = _director(isolated_env)
    full = json.dumps(_payload(director), ensure_ascii=False)
    truncated = full[: int(len(full) * 0.82)]  # tagliato a metà
    engine = FakeEngine([truncated, "{}"])
    director.ai_engine = engine

    design = director.design_reel(CreativeRequestPayload(theme="Prova troncata", duration_sec=20))
    assert engine.calls <= 2, "non si devono fare chiamate inutili"
    assert design.scenes


def test_garbage_triggers_one_repair_then_declared_fallback(isolated_env):
    director = _director(isolated_env)
    engine = FakeEngine(["Mi dispiace, non posso rispondere in JSON.", "Ancora testo libero."])
    director.ai_engine = engine

    design = director.design_reel(CreativeRequestPayload(theme="Prova rotta", duration_sec=20))
    assert engine.calls == 2, "esattamente un ritentativo di riparazione"
    assert "deterministic" in design.generation_engine
    assert "formato non leggibile" in (design.fallback_reason or "")
    assert "JSONDecodeError" not in (design.fallback_reason or "")
    assert design.timings_ms.get("json_repair_error")


def test_repair_success_keeps_engine_design(isolated_env):
    director = _director(isolated_env)
    good = json.dumps(_payload(director), ensure_ascii=False)
    engine = FakeEngine(["risposta in prosa senza JSON", good])
    director.ai_engine = engine

    design = director.design_reel(CreativeRequestPayload(theme="Riparazione riuscita", duration_sec=20))
    assert engine.calls == 2
    assert design.generation_engine.startswith("ai_engine:")
    assert design.timings_ms.get("json_repaired") is True
