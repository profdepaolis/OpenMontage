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
MODELS_TTL_SEC = 30 * 60

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


class ModelManager:
    """Configurazione e collaudo dei modelli del motore creativo."""

    def __init__(self, db: Any, adapter_provider: Any) -> None:
        self.db = db
        #: callable(provider_id) -> adapter, fornito dall'AIEngineManager
        self._adapter_for = adapter_provider

    # ------------------------------------------------------------ configurazione
    def config(self) -> dict[str, Any]:
        stored = self.db.get_preference(CONFIG_KEY) or {}
        merged = {
            "roles": {**DEFAULT_CONFIG["roles"], **(stored.get("roles") or {})},
            "timeouts": {**DEFAULT_CONFIG["timeouts"], **(stored.get("timeouts") or {})},
            "policy": stored.get("policy") or DEFAULT_CONFIG["policy"],
            "mode": stored.get("mode") or DEFAULT_CONFIG["mode"],
        }
        return {**merged, "policies": POLICIES, "defaults": DEFAULT_CONFIG}

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.config()
        roles = {**current["roles"], **(patch.get("roles") or {})}
        timeouts = {**current["timeouts"], **(patch.get("timeouts") or {})}
        clean_timeouts: dict[str, int] = {}
        for key, value in timeouts.items():
            try:
                clean_timeouts[key] = max(10, min(600, int(value)))
            except (TypeError, ValueError):
                clean_timeouts[key] = int(DEFAULT_CONFIG["timeouts"].get(key, 90))
        policy = str(patch.get("policy") or current["policy"])
        if policy not in POLICIES:
            policy = "ask"
        mode = str(patch.get("mode") or current["mode"])
        if mode not in ("everyday", "premium"):
            mode = "everyday"
        payload = {"roles": roles, "timeouts": clean_timeouts, "policy": policy, "mode": mode}
        self.db.set_preference(CONFIG_KEY, payload)
        return self.config()

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
