"""Model Manager leggero per OpenCode (v3.6).

Tre responsabilità, nessun demone:
1. elenco modelli con cache (mai interrogare il CLI a ogni apertura);
2. configurazione dei modelli per ruolo (everyday / premium / alternativo) e dei timeout;
3. test reali di inferenza, con latenza misurata e classificazione dell'errore.

Regola dura: la presenza nell'elenco non dimostra che il modello funzioni.
Un modello è "operativo" solo dopo un test riuscito, con data e latenza.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineError, EngineResult

CONFIG_KEY = "ai_model_config"
MODELS_CACHE_KEY = "ai_opencode_models_cache"
TESTS_KEY = "ai_model_tests"
LEGACY_ARCHIVE_KEY = "ai_model_legacy_archive"
LAST_INFERENCE_KEY = "ai_last_inference"
MODELS_TTL_SEC = 30 * 60

#: modelli misurati sul Mac dell'utente (3.6): 20,7 s / 78,4 s / 28,4 s
VERIFIED_ROLE_MODELS = (
    "opencode/hy4-flash-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/nemotron-3.5-lightning-free",
)

#: modelli che il provider ha dichiarato dismessi: mai riattivati in automatico
RETIRED_MARKERS = ("deepseek-v4", "deepseek-ai/deepseek-v4")

DEFAULT_CONFIG: dict[str, Any] = {
    "roles": {
        "everyday": "opencode/hy4-flash-free",
        "premium": "opencode/mimo-v2.6-flash-free",
        "fallback": "opencode/nemotron-3.5-lightning-free",
    },
    "timeouts": {"ideation": 45, "design": 90, "revision": 45, "premium": 150},
    "policy": "ask",  # ask | fallback | local | stop
    "mode": "everyday",  # everyday | premium
}

POLICIES = {
    "ask": "Chiedi conferma all'utente",
    "fallback": "Usa automaticamente il modello alternativo autorizzato",
    "local": "Usa il fallback locale",
    "stop": "Interrompi",
}

QUICK_PROMPT = "Rispondi soltanto con OK"

CREATIVE_PROMPT = """Sei il direttore creativo di KORA Beach Club (Policoro, Italy).
Restituisci SOLO JSON valido, nessun testo fuori dal JSON, con questa forma:
{"themes":[{"title":"...","hook":"...","message":"...","visual":"...","voiceover":"...","payoff":"The Art of Being"}]}
Esattamente 4 temi, differenti tra loro, in italiano, massimo 18 parole di voiceover ciascuno.
Filosofia: eleganza discreta, autenticità, ospitalità mediterranea. Nessun prezzo, nessuna promozione."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def provider_of_model(model: str) -> str:
    return model.split("/", 1)[0] if model and "/" in model else "opencode"


def is_retired_model(model: str, available: Optional[list[str]] = None) -> bool:
    """Un modello è «dismesso» se il nome lo dichiara o se il provider non lo elenca più."""
    low = (model or "").lower()
    if any(marker in low for marker in RETIRED_MARKERS):
        return True
    if available is not None:
        known = {m.lower() for m in available}
        if known and low not in known:
            return True
    return False


def _clean_times(values: dict[str, Any], fallback: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in values.items():
        try:
            out[key] = max(10, min(600, int(value)))
        except (TypeError, ValueError):
            out[key] = int(fallback.get(key, 90))
    return out


class ModelManager:
    """Configurazione e collaudo dei modelli del motore creativo."""

    def __init__(self, db: Any, adapter_provider: Any) -> None:
        self.db = db
        #: callable(provider_id) -> adapter, fornito dall'AIEngineManager
        self._adapter_for = adapter_provider

    # ------------------------------------------------------------ configurazione
    def _raw(self) -> dict[str, Any]:
        try:
            return dict(self.db.get_preference(CONFIG_KEY) or {})
        except Exception:
            return {}

    def config(self) -> dict[str, Any]:
        stored = self._raw()
        merged = {
            "roles": {**DEFAULT_CONFIG["roles"], **(stored.get("roles") or {})},
            "timeouts": {**DEFAULT_CONFIG["timeouts"], **(stored.get("timeouts") or {})},
            "policy": stored.get("policy") or DEFAULT_CONFIG["policy"],
            "mode": stored.get("mode") or DEFAULT_CONFIG["mode"],
            #: True solo quando l'utente ha salvato una scelta dalla schermata Modelli
            "user_choice": bool(stored.get("user_choice")),
            "migration": stored.get("migration") or None,
        }
        return {**merged, "policies": POLICIES, "defaults": DEFAULT_CONFIG}

    def update(self, patch: dict[str, Any], *, internal: bool = False) -> dict[str, Any]:
        current = self.config()
        roles = {**current["roles"], **(patch.get("roles") or {})}
        timeouts = {**current["timeouts"], **(patch.get("timeouts") or {})}
        clean_timeouts = _clean_times(timeouts, DEFAULT_CONFIG["timeouts"])
        policy = str(patch.get("policy") or current["policy"])
        if policy not in POLICIES:
            policy = "ask"
        mode = str(patch.get("mode") or current["mode"])
        if mode not in ("everyday", "premium"):
            mode = "everyday"
        payload: dict[str, Any] = {
            "roles": roles,
            "timeouts": clean_timeouts,
            "policy": policy,
            "mode": mode,
            # una patch esplicita dell'utente blocca ogni futura migrazione automatica
            "user_choice": True if not internal else bool(current.get("user_choice")),
            "updated_at": _utcnow(),
        }
        migration = patch.get("migration", current.get("migration"))
        if migration:
            payload["migration"] = migration
        self.db.set_preference(CONFIG_KEY, payload)
        return self.config()

    # ------------------------------------------------------------- migrazione
    def migrate_legacy(self, engine: Any = None) -> dict[str, Any]:
        """Porta la vecchia configurazione (provider/agente) dentro il Model Manager.

        Regole:
        * una scelta già salvata dall'utente non viene mai sovrascritta;
        * un modello valido fra quelli misurati viene conservato;
        * un modello dismesso (DeepSeek v4.1) viene sostituito con HY4 Flash e
          la sostituzione resta scritta nella configurazione e nella Home;
        * le chiavi legacy vengono svuotate, così non possono più scegliere il
          modello al posto del Model Manager.
        """
        stored = self._raw()
        if stored.get("user_choice"):
            return {
                "migrated": False,
                "reason": "scelta utente già salvata: nessuna sovrascrittura",
                "config": self.config(),
            }

        settings: dict[str, Any] = {}
        if engine is not None and hasattr(engine, "settings"):
            try:
                settings = engine.settings() or {}
            except Exception:
                settings = {}
        providers = settings.get("providers") or {}
        agents = settings.get("agents") or {}

        legacy: list[tuple[str, str]] = []
        provider_model = str((providers.get("opencode") or {}).get("model") or "").strip()
        if provider_model:
            legacy.append(("providers.opencode.model", provider_model))
        agent_conf = agents.get("creative_director") or {}
        agent_model = str(agent_conf.get("model") or "").strip()
        if agent_model and (
            agent_conf.get("mode") == "provider" and str(agent_conf.get("provider") or "opencode") == "opencode"
        ):
            legacy.append(("agents.creative_director.model", agent_model))

        if not legacy:
            return {"migrated": False, "reason": "nessuna configurazione legacy", "config": self.config()}

        available: list[str] = []
        candidate = next(
            (m for _source, m in legacy if m in VERIFIED_ROLE_MODELS and not is_retired_model(m)),
            None,
        )
        # l'elenco del CLI serve solo quando il vecchio modello non è già provato
        if candidate is None:
            adapter = self._adapter()
            if adapter is not None:
                try:
                    if adapter.detect().get("installed"):
                        available = list(adapter.raw_model_ids())
                except Exception:
                    available = []
        valid = candidate if candidate and (not available or candidate in available) else None
        retired = [model for _source, model in legacy if is_retired_model(model)]
        chosen = valid or DEFAULT_CONFIG["roles"]["everyday"]
        migration = {
            "at": _utcnow(),
            "from": legacy[0][1],
            "from_all": [model for _source, model in legacy],
            "to": chosen,
            "retired": bool(retired) and valid is None,
            "sources": [source for source, _model in legacy],
            "detail": (
                f"Modello precedente «{retired[0]}» dismesso o non disponibile: "
                f"uso {chosen}."
                if retired and valid is None
                else f"Preferenza precedente «{valid}» mantenuta nel Model Manager."
                if valid
                else f"Configurazione precedente «{legacy[0][1]}» migrata su {chosen}."
            ),
        }
        roles = {**DEFAULT_CONFIG["roles"], "everyday": chosen}
        self.update(
            {
                "roles": roles,
                "timeouts": dict(DEFAULT_CONFIG["timeouts"]),
                "policy": stored.get("policy") or DEFAULT_CONFIG["policy"],
                "mode": stored.get("mode") or DEFAULT_CONFIG["mode"],
                "migration": migration,
            },
            internal=True,
        )
        try:
            self.db.set_preference(
                LEGACY_ARCHIVE_KEY,
                {"at": migration["at"], "entries": [{"source": s, "model": m} for s, m in legacy]},
            )
        except Exception:
            pass

        # le chiavi legacy non devono più decidere il modello
        if engine is not None and hasattr(engine, "update_settings"):
            patch: dict[str, Any] = {}
            if provider_model:
                patch.setdefault("providers", {})["opencode"] = {"model": ""}
            if agent_model:
                patch.setdefault("agents", {})["creative_director"] = {"mode": "inherit"}
            if patch:
                try:
                    engine.update_settings(patch)
                except Exception:
                    pass
        return {"migrated": True, "migration": migration, "config": self.config()}

    # ------------------------------------------------------------ modello attivo
    def effective_choice(self) -> dict[str, Any]:
        """Provider e modello che KORA deve usare adesso (unica fonte: questa config)."""
        cfg = self.config()
        mode = "premium" if cfg["mode"] == "premium" else "everyday"
        model = str(cfg["roles"].get(mode) or DEFAULT_CONFIG["roles"][mode])
        return {
            "provider": provider_of_model(model),
            "model": model,
            "role": mode,
            "mode": cfg["mode"],
            "fallback_model": str(cfg["roles"].get("fallback") or DEFAULT_CONFIG["roles"]["fallback"]),
            "policy": cfg["policy"],
            "timeouts": dict(cfg["timeouts"]),
            "user_choice": cfg["user_choice"],
            "migration": cfg["migration"],
        }

    def record_inference(
        self,
        *,
        provider: str,
        model: str,
        ok: bool,
        latency_ms: Optional[float] = None,
        error: str = "",
        fallback: bool = False,
        purpose: str = "",
    ) -> None:
        """Ultima inferenza realmente eseguita: la Home mostra questo, non un'etichetta."""
        record = {
            "at": _utcnow(),
            "provider": provider,
            "model": model,
            "ok": bool(ok),
            "latency_ms": latency_ms,
            "error": (error or "")[:300],
            "fallback": bool(fallback),
            "purpose": purpose,
        }
        try:
            self.db.set_preference(LAST_INFERENCE_KEY, record)
        except Exception:
            pass

    def last_inference(self) -> Optional[dict[str, Any]]:
        try:
            return self.db.get_preference(LAST_INFERENCE_KEY) or None
        except Exception:
            return None

    def status(self, engine: Any = None) -> dict[str, Any]:
        """Diagnostica visibile nella Home: provider/modello reali, modalità, esiti."""
        choice = self.effective_choice()
        adapter = self._adapter()
        installed = False
        version = None
        detail = ""
        try:
            if adapter is not None:
                detect = adapter.detect()
                installed = bool(detect.get("installed"))
                version = detect.get("version")
                detail = str(detect.get("detail") or "")
        except Exception:
            installed = False
        tests = [row for row in self._tests(limit=50) if row.get("ok")]
        last_test = tests[-1] if tests else None
        active_provider = choice["provider"]
        enabled = True
        if engine is not None and hasattr(engine, "provider_enabled"):
            try:
                enabled = bool(engine.provider_enabled(active_provider))
            except Exception:
                enabled = True
        return {
            "provider": active_provider,
            "model": choice["model"],
            "model_label": choice["model"].split("/", 1)[-1],
            "mode": choice["mode"],
            "role": choice["role"],
            "policy": choice["policy"],
            "policy_label": POLICIES.get(choice["policy"], choice["policy"]),
            "timeouts": choice["timeouts"],
            "fallback_model": choice["fallback_model"],
            "installed": installed,
            "cli_version": version,
            "detail": detail,
            "provider_enabled": enabled,
            "last_test": last_test,
            "last_inference": self.last_inference(),
            "migration": choice["migration"],
            "user_choice": choice["user_choice"],
        }

    def timeout_for(self, purpose: str) -> float:
        cfg = self.config()
        if purpose == "premium":
            return float(cfg["timeouts"].get("premium", 150))
        return float(cfg["timeouts"].get(purpose, 90))

    def model_for(self, role: str) -> Optional[str]:
        return self.config()["roles"].get(role)

    # ------------------------------------------------------------------ elenco
    def _adapter(self):
        return self._adapter_for("opencode")

    def refresh(self, force: bool = False) -> dict[str, Any]:
        cached = self.db.get_preference(MODELS_CACHE_KEY) or {}
        age = time.time() - float(cached.get("fetched_at") or 0)
        if not force and cached.get("models") and age < MODELS_TTL_SEC:
            return {**cached, "cached": True, "age_sec": int(age)}

        adapter = self._adapter()
        if adapter is None:
            raise EngineError("Adapter OpenCode non disponibile")
        detect = adapter.detect()
        if not detect.get("installed"):
            payload = {"models": [], "fetched_at": time.time(), "fetched_at_iso": _utcnow(), "error": detect.get("detail")}
            self.db.set_preference(MODELS_CACHE_KEY, payload)
            return {**payload, "cached": False, "age_sec": 0}
        try:
            ids = adapter.raw_model_ids()
            error = ""
        except EngineError as exc:
            ids, error = [], str(exc)
        payload = {
            "models": ids,
            "fetched_at": time.time(),
            "fetched_at_iso": _utcnow(),
            "cli_version": detect.get("version"),
            "error": error,
        }
        self.db.set_preference(MODELS_CACHE_KEY, payload)
        return {**payload, "cached": False, "age_sec": 0}

    def search(self, query: str = "", provider: str = "") -> dict[str, Any]:
        cache = self.refresh()
        models = list(cache.get("models") or [])
        q = (query or "").strip().lower()
        p = (provider or "").strip().lower()
        providers = sorted({m.split("/", 1)[0] for m in models if "/" in m})
        filtered = [
            m for m in models
            if (not q or q in m.lower()) and (not p or m.lower().startswith(f"{p}/") or f"/{p}/" in m.lower())
        ]
        tested = {item["model"]: item for item in self._tests(limit=200)}
        return {
            "models": [
                {
                    "id": m,
                    "provider": m.split("/", 1)[0] if "/" in m else "",
                    "last_test": tested.get(m),
                }
                for m in filtered
            ],
            "total": len(models),
            "providers": providers,
            "cached": cache.get("cached"),
            "fetched_at_iso": cache.get("fetched_at_iso"),
            "error": cache.get("error") or "",
            "cli_version": cache.get("cli_version"),
        }

    # ------------------------------------------------------------------ test
    def _tests(self, limit: int = 50) -> list[dict[str, Any]]:
        stored = self.db.get_preference(TESTS_KEY) or []
        return list(stored)[-limit:]

    def _save_test(self, record: dict[str, Any]) -> None:
        stored = list(self.db.get_preference(TESTS_KEY) or [])
        stored.append(record)
        self.db.set_preference(TESTS_KEY, stored[-120:])

    def test_model(self, model: str, kind: str = "quick", timeout: Optional[float] = None) -> dict[str, Any]:
        """Un test reale. Nessuna scorciatoia: se il modello non risponde, lo dice."""
        if not model:
            raise ValueError("Indica il modello da testare")
        adapter = self._adapter()
        if adapter is None:
            raise EngineError("Adapter OpenCode non disponibile")
        if kind not in ("quick", "creative"):
            raise ValueError("Tipo di test non riconosciuto: quick o creative")
        limit = timeout or (45.0 if kind == "quick" else 150.0)
        prompt = QUICK_PROMPT if kind == "quick" else CREATIVE_PROMPT
        started = time.perf_counter()
        record: dict[str, Any] = {
            "model": model,
            "kind": kind,
            "at": _utcnow(),
            "ok": False,
            "latency_ms": None,
            "error_kind": "",
            "error": "",
            "notes": "",
        }
        try:
            result: EngineResult = adapter.generate(
                [{"role": "user", "content": prompt}],
                model=model,
                timeout=limit,
                json_mode=(kind == "creative"),
            )
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            text = (result.text or "").strip()
            record["latency_ms"] = elapsed
            record["raw_chars"] = len(text)
            if kind == "quick":
                ok = "ok" in text.lower()
                record["ok"] = ok
                record["notes"] = "" if ok else "la risposta non contiene OK"
            else:
                from kora_studio.ai_engine.json_extract import JsonExtractionError, parse_json_object

                try:
                    data = parse_json_object(text, required_keys={"themes"})
                    themes = data.get("themes")
                    record["themes"] = len(themes) if isinstance(themes, list) else 0
                    record["ok"] = record["themes"] == 4
                    record["notes"] = (
                        "" if record["ok"] else f"attesi 4 temi, ricevuti {record['themes']}"
                    )
                except JsonExtractionError as exc:
                    record["ok"] = False
                    record["error_kind"] = "json_non_valido"
                    record["notes"] = str(exc)[:200]
        except EngineError as exc:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            record["error_kind"] = exc.kind
            record["error"] = str(exc)[:400]
        except Exception as exc:  # pragma: no cover - difensivo
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            record["error_kind"] = "errore_interno"
            record["error"] = f"{type(exc).__name__}: {exc}"[:400]

        self._save_test(record)
        return record

    def benchmark(self, kind: str = "quick", models: Optional[list[str]] = None) -> dict[str, Any]:
        """Esegue i test in sequenza sui modelli indicati (mai tutti, mai in automatico)."""
        cfg = self.config()
        if models is None:
            models = [cfg["roles"].get("everyday"), cfg["roles"].get("premium"), cfg["roles"].get("fallback")]
        ordered = [m for m in dict.fromkeys(models) if m]
        if not ordered:
            raise ValueError("Nessun modello configurato da testare")
        results = []
        for model in ordered:
            results.append(self.test_model(model, kind=kind, timeout=cfg["timeouts"].get("premium", 150)))
        return {
            "kind": kind,
            "at": _utcnow(),
            "models": ordered,
            "results": results,
            "passed": sum(1 for r in results if r.get("ok")),
        }

    def history(self, model: str = "", limit: int = 30) -> list[dict[str, Any]]:
        rows = self._tests(limit=200)
        if model:
            rows = [r for r in rows if r.get("model") == model]
        return rows[-limit:]

    # --------------------------------------------------------- diagnostica
    def diagnostics(self) -> dict[str, Any]:
        adapter = self._adapter()
        cfg = self.config()
        if adapter is None:
            return {"installed": False, "detail": "adapter non disponibile", "config": cfg}
        detect = adapter.detect()
        cache = self.db.get_preference(MODELS_CACHE_KEY) or {}
        return {
            "installed": bool(detect.get("installed")),
            "binary": detect.get("binary"),
            "version": detect.get("version"),
            "detail": detect.get("detail"),
            "models_cached": len(cache.get("models") or []),
            "models_fetched_at": cache.get("fetched_at_iso"),
            "models_error": cache.get("error") or "",
            "config": cfg,
            "selected": cfg["roles"],
            "last_tests": self._tests(limit=6),
        }
