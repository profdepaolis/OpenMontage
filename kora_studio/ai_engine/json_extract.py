"""Estrazione robusta del JSON dalle risposte dei modelli (v3.4).

I modelli reali (Gemini, Codex, OpenCode) restituiscono spesso:
- testo prima o dopo l'oggetto,
- blocchi ```json … ```,
- virgole finali, virgolette tipografiche, a capo non escapati,
- risposte troncate per limite di token.

Qui si prova a recuperare il JSON in modo deterministico e verificabile;
se non è recuperabile si solleva `JsonExtractionError` con un estratto breve,
mai con l'intera risposta (che può contenere dati sensibili).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional


class JsonExtractionError(ValueError):
    """La risposta del modello non contiene un oggetto JSON interpretabile."""

    def __init__(self, message: str, excerpt: str = "") -> None:
        super().__init__(message)
        self.excerpt = excerpt[:280]


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    # fence aperto e mai chiuso (risposta troncata)
    if "```" in text:
        after = text.split("```", 1)[1]
        if after.strip().startswith("json"):
            after = after.strip()[4:]
        if "{" in after:
            return after.strip()
    return text.strip()


def _balanced_slice(text: str, start: int) -> tuple[str, bool]:
    """Ritorna (sottostringa, completa). Se il JSON è troncato chiude i delimitatori."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], True
    # troncato: chiudi ciò che è aperto
    closers = []
    depth = 0
    in_string = False
    escaped = False
    for ch in text[start:]:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            closers.append("}" if ch == "{" else "]")
        elif ch in "}]" and closers:
            depth -= 1
            closers.pop()
    tail = text[start:]
    if in_string:
        tail += '"'
    return tail + "".join(reversed(closers)), False


def _repair(text: str) -> str:
    """Correzioni sicure e verificabili dei difetti più comuni."""
    cleaned = text.strip()
    # virgolette tipografiche usate come delimitatore
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"')
    cleaned = cleaned.replace("\u2018", "'").replace("\u2019", "'")
    # virgole finali prima di } o ]
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    # caratteri di controllo non escapati dentro le stringhe
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)
    # newline dentro una stringa JSON → spazio
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in cleaned:
        if in_string:
            if escaped:
                escaped = False
                out.append(ch)
                continue
            if ch == "\\":
                escaped = True
                out.append(ch)
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            out.append(" " if ch in "\r\n\t" else ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def parse_json_object(
    raw: str,
    *,
    required_keys: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Estrae il primo oggetto JSON valido dalla risposta del modello."""
    text = _strip_fences(raw or "")
    if not text:
        raise JsonExtractionError("risposta vuota dal modello")

    starts = [index for index, ch in enumerate(text) if ch == "{"][:6]
    if not starts:
        raise JsonExtractionError("nessun oggetto JSON nella risposta", text)

    attempts: list[str] = []
    for start in starts:
        candidate, complete = _balanced_slice(text, start)
        attempts.append(candidate)
        if not complete:
            attempts.append(_repair(candidate))
        else:
            attempts.append(_repair(candidate))

    last_error: Optional[Exception] = None
    for candidate in attempts:
        try:
            data = json.loads(candidate)
        except Exception as exc:  # noqa: BLE001 - si prova il candidato successivo
            last_error = exc
            continue
        if not isinstance(data, dict):
            continue
        if required_keys and not required_keys.issubset(data.keys()):
            last_error = JsonExtractionError(
                "JSON valido ma incompleto: mancano " + ", ".join(sorted(required_keys - set(data)))
            )
            continue
        return data

    raise JsonExtractionError(
        f"JSON non interpretabile ({type(last_error).__name__ if last_error else 'formato sconosciuto'})",
        text,
    )


def looks_cut_off(raw: str) -> bool:
    """True se la risposta sembra troncata (delimitatori non chiusi)."""
    text = _strip_fences(raw or "")
    start = text.find("{")
    if start < 0:
        return False
    _slice, complete = _balanced_slice(text, start)
    return not complete
