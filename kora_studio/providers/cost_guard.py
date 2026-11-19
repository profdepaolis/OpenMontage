"""Cost Guard — enforces 0 EUR default paid budget and <=20 EUR monthly cap."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from kora_studio.config import DEFAULT_PAID_BUDGET_EUR, MAX_MONTHLY_CAP_EUR
from kora_studio.db.database import Database, utcnow_iso


class CostGuard:
    """Manages spending limits, estimates, authorizations, and reconciliation."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_budget_settings(self) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM budget_settings WHERE id = 1").fetchone()
            paid_budget = float(row["paid_budget_eur"]) if row else DEFAULT_PAID_BUDGET_EUR
            monthly_cap = float(row["monthly_cap_eur"]) if row else MAX_MONTHLY_CAP_EUR
            month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
            usage_row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(estimated_eur), 0.0) AS est_total,
                    COALESCE(SUM(actual_eur), 0.0) AS act_total
                FROM provider_usage
                WHERE created_at LIKE ? AND status != 'rejected'
                """,
                (f"{month_prefix}%",),
            ).fetchone()
            est_month = round(float(usage_row["est_total"] or 0.0), 4)
            act_month = round(float(usage_row["act_total"] or 0.0), 4)
        return {
            "paid_budget_eur": paid_budget,
            "monthly_cap_eur": monthly_cap,
            "max_allowed_cap_eur": MAX_MONTHLY_CAP_EUR,
            "estimated_month_eur": est_month,
            "actual_month_eur": act_month,
            "remaining_paid_budget_eur": round(max(0.0, paid_budget - act_month), 4),
            "remaining_monthly_cap_eur": round(max(0.0, monthly_cap - act_month), 4),
        }

    def update_budget_settings(
        self, paid_budget_eur: Optional[float] = None, monthly_cap_eur: Optional[float] = None
    ) -> dict[str, Any]:
        current = self.get_budget_settings()
        new_paid = current["paid_budget_eur"] if paid_budget_eur is None else float(paid_budget_eur)
        new_cap = current["monthly_cap_eur"] if monthly_cap_eur is None else float(monthly_cap_eur)
        if new_paid < 0:
            raise ValueError("Il budget a pagamento non può essere negativo.")
        if new_cap < 0 or new_cap > MAX_MONTHLY_CAP_EUR:
            raise ValueError(
                f"Il limite mensile configurabile deve essere compreso tra 0 € e {MAX_MONTHLY_CAP_EUR:.0f} €."
            )
        if new_paid > new_cap:
            raise ValueError("Il budget autorizzato non può superare il limite mensile.")
        with self.db.session() as conn:
            conn.execute(
                "UPDATE budget_settings SET paid_budget_eur = ?, monthly_cap_eur = ?, updated_at = ? WHERE id = 1",
                (new_paid, new_cap, utcnow_iso()),
            )
        return self.get_budget_settings()

    def check_authorization(
        self,
        estimated_eur: float,
        explicit_user_authorization: bool = False,
    ) -> dict[str, Any]:
        """Never allow paid execution (> 0 EUR) without explicit user budget & authorization."""
        settings = self.get_budget_settings()
        if estimated_eur <= 0.0:
            return {"authorized": True, "reason": "Operazione gratuita o locale (0 €)."}

        if settings["paid_budget_eur"] <= 0.0 and not explicit_user_authorization:
            return {
                "authorized": False,
                "reason": (
                    "Budget predefinito per generazioni a pagamento impostato a 0 €. "
                    "Autorizzazione esplicita dell'utente richiesta."
                ),
            }
        projected = settings["actual_month_eur"] + estimated_eur
        if projected > settings["monthly_cap_eur"]:
            return {
                "authorized": False,
                "reason": (
                    f"Limite mensile superato ({projected:.2f} € > {settings['monthly_cap_eur']:.2f} €)."
                ),
            }
        if projected > settings["paid_budget_eur"] and not explicit_user_authorization:
            return {
                "authorized": False,
                "reason": (
                    f"Costo stimato ({estimated_eur:.2f} €) oltre il budget autorizzato "
                    f"({settings['paid_budget_eur']:.2f} €)."
                ),
            }
        return {"authorized": True, "reason": "Autorizzato nei limiti di budget."}

    def record_usage(
        self,
        provider_id: str,
        capability: str,
        estimated_eur: float,
        actual_eur: float,
        authorized: bool,
        status: str,
        project_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO provider_usage
                (provider_id, project_id, capability, estimated_eur, actual_eur, authorized, status, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    project_id,
                    capability,
                    round(estimated_eur, 4),
                    round(actual_eur, 4),
                    1 if authorized else 0,
                    status,
                    detail,
                    utcnow_iso(),
                ),
            )

    def get_usage_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_usage ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
