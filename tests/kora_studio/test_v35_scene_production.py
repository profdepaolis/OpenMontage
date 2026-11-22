"""v3.5 — editor di scena, blocchi, sottotitoli, copione, regia conversazionale.

Collaudo reale sulla logica che governa il render: identità stabile delle scene,
protezione dell'outro, invalidazione corretta di audio e anteprima, proposte
mai applicate senza conferma.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload


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


def _project(director: KoraDirector, seconds: float = 20.0) -> str:
    design = director.design_reel(
        CreativeRequestPayload(theme="La bellezza non fa rumore", duration_sec=seconds, voice_id="brando")
    )
    project = director.create_project_from_design(design, theme="La bellezza non fa rumore")
    return project["id"]


@pytest.fixture()
def env(isolated_env):
    _director(isolated_env)
    return isolated_env


@pytest.fixture()
def setup(env):
    director = env["director"]
    pid = _project(director)
    return director, pid


# ------------------------------------------------------------- identità e outro
def test_scene_uids_survive_reorder_and_duplication(setup):
    director, pid = setup
    before = director.scene_editor.list_scenes(pid)["scenes"]
    uids = [s["scene_uid"] for s in before]
    assert all(uids), "ogni scena deve avere un identificativo stabile"

    moved = director.scene_editor.move_scene(pid, 1, "down")
    assert moved["ok"]
    after = moved["scenes"]
    assert [s["scene_uid"] for s in after][:2] == [uids[1], uids[0]]

    duplicated = director.scene_editor.duplicate_scene(pid, 1)
    assert duplicated["ok"]
    new_uid = duplicated["added"]["scene_uid"]
    assert new_uid and new_uid not in uids, "la copia deve avere un'identità propria"


def test_outro_cannot_become_an_ordinary_scene(setup):
    director, pid = setup
    scenes = director.scene_editor.list_scenes(pid)["scenes"]
    outro = next(s for s in scenes if s["is_outro"])

    swap = director.scene_editor.update_scene(
        pid, outro["scene_number"], {"asset_id": director.vault.list_assets()[0]["id"]}
    )
    assert swap["ok"] is False and swap["blocked"] is True

    dupe = director.scene_editor.duplicate_scene(pid, outro["scene_number"])
    assert dupe["ok"] is False and dupe["blocked"] is True

    moved = director.scene_editor.move_scene(pid, outro["scene_number"], "down")
    assert moved["ok"] is False

    remaining = director.scene_editor.list_scenes(pid)["scenes"]
    assert sum(1 for s in remaining if s["is_outro"]) == 1
    assert remaining[-1]["is_outro"] is True


def test_locks_block_only_the_locked_component(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    number = scene["scene_number"]

    locked = director.scene_editor.set_locks(pid, number, {"duration": True})
    assert locked["ok"] and locked["locks"] == {"duration": True}

    blocked = director.scene_editor.update_scene(pid, number, {"duration_sec": 5.0})
    assert blocked["ok"] is False and "durata" in blocked["reason"]

    allowed = director.scene_editor.update_scene(pid, number, {"motion_intensity": 1.4})
    assert allowed["ok"] is True

    director.scene_editor.set_locks(pid, number, {})
    assert director.scene_editor.list_scenes(pid)["scenes"][0]["locks"] == {}


def test_duration_parameter_changes_reach_the_scene(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    res = director.scene_editor.update_scene(
        pid,
        scene["scene_number"],
        {"motion_intensity": 1.8, "motion_easing": "ease_out", "composition": "simmetria"},
    )
    updated = res["scenes"][0]
    assert updated["motion_intensity"] == 1.8
    assert updated["motion_easing"] == "ease_out"
    assert updated["composition"] == "simmetria"

    with pytest.raises(ValueError):
        director.scene_editor.update_scene(pid, scene["scene_number"], {"motion_easing": "boing"})
    with pytest.raises(ValueError):
        director.scene_editor.update_scene(pid, scene["scene_number"], {"composition": "a caso"})


# --------------------------------------------------------------- sottotitoli
def test_caption_change_does_not_touch_voice(setup):
    director, pid = setup
    director.invalidate_audio(pid, reason="stato iniziale")
    before = director.audio_state(pid)

    res = director.caption_designer.apply(
        pid, "cinematic_luxury", {"font_size": 50, "color": "#FFFFFF", "position": "center"}
    )
    assert res["ok"] and res["audio_untouched"] is True
    assert res["overrides"]["font_size"] == 50
    assert res["overrides"]["color"] == "#FFFFFF"

    after = director.audio_state(pid)
    assert after.get("invalidated_at") == before.get("invalidated_at"), "la voce non deve essere invalidata"


def test_caption_presets_are_seven_and_engine_styles_resolve(setup):
    director, pid = setup
    panel = director.caption_designer.presets()
    assert len(panel["presets"]) == 7
    current = director.caption_designer.current(pid)
    assert current["engine_style"] in {
        "serif_ivory",
        "serif_minimal",
        "clean_sans",
        "hyperframes_kinetic",
        "hyperframes_glass",
        "hyperframes_word_highlight",
        "none",
    }

    with pytest.raises(ValueError):
        director.caption_designer.apply(pid, "preset_inventato")

    cleaned, warnings = director.caption_designer.normalize_overrides({"color": "rosso"})
    assert cleaned["color"] == "#F7F3EA" and warnings

    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    per_scene = director.caption_designer.apply(pid, "minimal", None, scene_number=scene["scene_number"])
    assert per_scene["ok"] and per_scene["scope"] == "scene"
    refreshed = director.scene_editor.list_scenes(pid)["scenes"][0]
    assert refreshed["caption_style"] == "minimal"


def test_title_is_separate_from_voiceover(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    res = director.caption_designer.set_title_text(pid, scene["scene_number"], "Mattino sul mare")
    assert res["ok"] and res["audio_untouched"] is True
    refreshed = director.scene_editor.list_scenes(pid)["scenes"][0]
    assert refreshed["title_text"] == "Mattino sul mare"
    assert refreshed["spoken_text"] == scene["spoken_text"], "il titolo grafico non è la voce"

    outro = next(s for s in director.scene_editor.list_scenes(pid)["scenes"] if s["is_outro"])
    blocked = director.caption_designer.set_title_text(pid, outro["scene_number"], "Altro")
    assert blocked["ok"] is False


# ------------------------------------------------------------------- copione
def test_script_proposal_is_not_applied_until_confirmed(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    before_text = scene["spoken_text"]

    proposal = director.script_director.propose(pid, "scene", [scene["scene_number"]], "più concreto")
    assert proposal["applied"] is False
    assert proposal["proposals"], proposal
    still = director.scene_editor.list_scenes(pid)["scenes"][0]
    assert still["spoken_text"] == before_text, "una proposta non deve modificare il copione"

    applied = director.script_director.apply(pid, proposal["proposals"], scope="scene")
    assert applied["ok"], applied
    assert applied["audio_invalidated"] is True
    changed = director.scene_editor.list_scenes(pid)["scenes"][0]
    assert changed["spoken_text"] == proposal["proposals"][0]["after"]


def test_script_multi_scene_is_one_call_and_respects_locks(setup):
    director, pid = setup
    scenes = director.scene_editor.list_scenes(pid)["scenes"]
    numbers = [s["scene_number"] for s in scenes[:3] if not s["is_outro"]]
    director.scene_editor.set_locks(pid, numbers[0], {"voice": True})

    proposal = director.script_director.propose(pid, "group", numbers, "tono più quieto")
    assert numbers[0] in proposal["locked_scenes"]
    assert all(p["scene_number"] != numbers[0] for p in proposal["proposals"])
    assert proposal["visuals_untouched"] is True
    assert proposal["direction_untouched"] is True
    assert proposal["duration_frames"] == sum(s["duration_frames"] for s in scenes), "la durata non cambia"


def test_instagram_caption_needs_no_render(setup):
    director, pid = setup
    proposal = director.script_director.propose(pid, "caption", [], "più asciutta")
    assert proposal["ok"] and proposal["requires_render"] is False
    applied = director.script_director.apply(pid, proposal["proposals"], scope="caption")
    assert applied["ok"] and applied["audio_invalidated"] is False


# ------------------------------------------------- regia conversazionale (v3.5)
def test_conversational_direction_parses_italian(setup):
    director, pid = setup
    scenes = director.scene_editor.list_scenes(pid)["scenes"]
    target = scenes[2]

    res = director.art_director.propose(pid, "la scena 3 più lenta")
    assert res["ok"] and res["starts_production"] is False
    change = res["changes"][0]
    assert change["scene_number"] == target["scene_number"]
    assert "motion_speed" in change["patch"]

    before = director.scene_editor.list_scenes(pid)["scenes"][2]["motion_speed"]
    applied = director.art_director.apply(pid, res["changes"])
    assert applied["ok"]
    after = director.scene_editor.list_scenes(pid)["scenes"][2]["motion_speed"]
    assert after != before


def test_direction_never_touches_outro(setup):
    director, pid = setup
    outro = next(s for s in director.scene_editor.list_scenes(pid)["scenes"] if s["is_outro"])
    res = director.art_director.propose(pid, f"scena {outro['scene_number']} più lenta")
    assert res["changes"] == []
    assert res["ok"] is False


def test_direction_invalid_preset_is_reported(setup):
    director, pid = setup
    res = director.art_director.propose(pid, "sottotitoli minimal")
    assert res["ok"] and res["changes"][0]["kind"] == "caption_preset"
    assert res["changes"][0]["preset"] == "minimal"


# ------------------------------------------------------------- generatore scene
def test_scene_generator_declares_modes_and_costs(setup):
    director, pid = setup
    panel = director.scene_generator.modes()
    assert [m["id"] for m in panel["modes"]] == ["A", "B", "C", "D", "E"]
    assert panel["budget_zero"] is True

    free = director.scene_generator.preflight("A")
    assert free["available"] is True
    assert free["requires_authorization"] is False
    assert free["tested"] is True

    paid = director.scene_generator.preflight("C")
    assert paid["requires_authorization"] is True
    assert paid["tested"] is False
    assert paid["declared_limit"], "un provider non collaudato va dichiarato"

    blocked = director.scene_generator.generate(pid, 1, "C", prompt="scena", duration_sec=5)
    assert blocked["ok"] is False
    assert blocked.get("requires_authorization") or blocked.get("blocked"), blocked


def test_scene_generator_discards_without_touching_originals(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    res = director.scene_generator.generate(pid, scene["scene_number"], "A", prompt="dettaglio del legno")
    assert res["ok"], res
    assert res["generation"]["id"]
    history = director.scene_generator.history(pid)["generations"]
    assert len(history) == 1
    assert history[0]["reference_asset_id"] == scene["asset_id"]
    assert history[0]["requires_human_review"] is True

    discarded = director.scene_generator.discard(pid, res["generation"]["id"])
    assert discarded["ok"] and discarded["originals_untouched"] is True
    assert director.scene_generator.history(pid)["generations"] == []


# ----------------------------------------------------------------------- API
def test_v35_api_endpoints(env):
    director = env["director"]
    pid = _project(director)
    client = TestClient(create_app(db_path=env["db_path"], auto_seed=False), raise_server_exceptions=False)

    panel = client.get(f"/api/projects/{pid}/caption")
    assert panel.status_code == 200 and len(panel.json()["presets"]) == 7

    bad = client.post(f"/api/projects/{pid}/caption/apply", json={"preset": "inesistente"})
    assert bad.status_code == 400

    missing = client.post(f"/api/projects/{pid}/scenes/99/duplicate")
    assert missing.status_code == 404

    direction = client.post(f"/api/projects/{pid}/direction/propose", json={"instruction": "scena 2 più lenta"})
    assert direction.status_code == 200 and direction.json()["changes"]

    modes = client.get("/api/scene-generator/modes")
    assert modes.status_code == 200 and len(modes.json()["modes"]) == 5

    locks = client.post(f"/api/projects/{pid}/scenes/1/locks", json={"locks": {"scene": True}})
    assert locks.status_code == 200 and locks.json()["locks"] == {"scene": True}
    director.scene_editor.set_locks(pid, 1, {})


def test_single_scene_preview_is_low_res_and_labelled(setup):
    director, pid = setup
    scene = director.scene_editor.list_scenes(pid)["scenes"][0]
    info = director.render_scene_preview(pid, scene["scene_number"])
    assert info["resolution"] == "540x960"
    assert info["fps"] == 25
    assert info["revision"], "l'anteprima va etichettata con la revisione corrente"
    from pathlib import Path as _P

    assert _P(info["clip"]).is_file()

    again = director.render_scene_preview(pid, scene["scene_number"])
    assert again["cache_hit"] is True, "la seconda richiesta deve usare la cache"


def test_direction_understands_intensity_words(setup):
    director, pid = setup
    res = director.art_director.propose(pid, "scena 2 più intensa")
    assert res["ok"], res
    patches = [c.get("patch") or {} for c in res["changes"]]
    assert any("motion_intensity" in p and float(p["motion_intensity"]) > 1.0 for p in patches), patches
