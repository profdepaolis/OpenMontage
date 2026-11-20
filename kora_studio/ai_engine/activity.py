"""AI Activity Monitor — one row per engine call, no credentials, no prompts.

Recorded for every call: production, agent, provider and model actually used,
start time, duration, outcome, attempts, whether a fallback happened, and the
cost (actual, estimated or unknown). Prompts, responses, keys and tokens are
never stored here.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

MAX_ROWS_RETURNED = 200


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIActivityLog:
    """Thin persistence layer on top of the existing SQLite database."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------ write
    def record(
        self,
        *,
        production_id: Optional[str],
        agent: str,
        provider: str,
        model: str,
        started_at: str,
        duration_ms: int,
        outcome: str,
        attempts: int = 1,
        fallback_used: bool = False,
        fallback_from: Optional[str] = None,
        cost_eur: Optional[float] = None,
        billing: str = "unknown",
        execution: str = "unknown",
        error_kind: Optional[str] = None,
        error_message: str = "",
        cancelled: bool = False,
    ) -> str:
        call_id = f"ai_{uuid.uuid4().hex[:12]}"
        if self.db is None:
            return call_id
        payload = {
            "id": call_id,
            "production_id": production_id or "",
            "agent": agent,
            "provider": provider or "",
            "model": model or "",
            "started_at": started_at,
            "duration_ms": int(duration_ms),
            "outcome": outcome,
            "attempts": int(attempts),
            "fallback_used": 1 if fallback_used else 0,
            "fallback_from": fallback_from or "",
            "cost_eur": cost_eur,
            "cost_known": 1 if cost_eur is not None else 0,
            "billing": billing or "unknown",
            "execution": execution or "unknown",
            "error_kind": error_kind or "",
            "error_message": (error_message or "")[:500],
            "cancelled": 1 if cancelled else 0,
            "created_at": _utcnow(),
        }
        try:
            with self.db.session() as conn:
                conn.execute(
                    """
                    INSERT INTO ai_activity (
                        id, production_id, agent, provider, model, started_at, duration_ms,
                        outcome, attempts, fallback_used, fallback_from, cost_eur, cost_known,
                        billing, execution, error_kind, error_message, cancelled, created_at
                    ) VALUES (
                        :id, :production_id, :agent, :provider, :model, :started_at, :duration_ms,
                        :outcome, :attempts, :fallback_used, :fallback_from, :cost_eur, :cost_known,
                        :billing, :execution, :error_kind, :error_message, :cancelled, :created_at
                    )
                    """,
                    payload,
                )
        except Exception:
            # the monitor must never break a production
            return call_id
        return call_id

    # ------------------------------------------------------------------- read
    def list(
        self,
        limit: int = 50,
        production_id: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        limit = max(1, min(int(limit or 50), MAX_ROWS_RETURNED))
        clauses = []
        params: list[Any] = []
        if production_id:
            clauses.append("production_id = ?")
            params.append(production_id)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self.db.session() as conn:
                rows = conn.execute(
                    f"SELECT * FROM ai_activity {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["fallback_used"] = bool(item.get("fallback_used"))
            item["cost_known"] = bool(item.get("cost_known"))
            item["cancelled"] = bool(item.get("cancelled"))
            out.append(item)
        return out

    def summary(self, limit: int = 500) -> dict[str, Any]:
        rows = self.list(limit=limit)
        total = len(rows)
        ok = sum(1 for r in rows if r.get("outcome") == "ok")
        costs = [r["cost_eur"] for r in rows if r.get("cost_known") and r.get("cost_eur") is not None]
        unknown = sum(1 for r in rows if not r.get("cost_known"))
        fallbacks = sum(1 for r in rows if r.get("fallback_used"))
        return {
            "calls": total,
            "ok": ok,
            "errors": total - ok,
            "fallbacks": fallbacks,
            "known_cost_eur": round(sum(costs), 5),
            "unknown_cost_calls": unknown,
            "note": "Costo reale solo dove il prezzo è pubblicato; altrove «sconosciuto» (non inventato).",
        }

    def export_json(self, limit: int = MAX_ROWS_RETURNED) -> str:
        return json.dumps(self.list(limit=limit), ensure_ascii=False, indent=2)
