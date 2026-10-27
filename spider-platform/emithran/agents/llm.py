"""Provider-agnostic LLM gateway (OpenAI-compatible chat completions) with fallback and a privacy gate."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..db import session_scope
from ..models import AppSetting

PROVIDERS_FILE = Path(__file__).with_name("providers.yaml")
ASSIGN_KEY = "agent_models"                 # {"<agent id>" | "*": [{"provider": ..., "model": ...}, ...]}
ALLOW_HOSTED_KEY = "agents_allow_hosted_internal"


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    local: bool = False
    key_env: str | None = None
    rpm: int | None = None

    @property
    def key(self) -> str | None:
        return os.environ.get(self.key_env) if self.key_env else None

    @property
    def configured(self) -> bool:
        return self.local or bool(self.key)


def load_providers() -> dict[str, Provider]:
    raw = yaml.safe_load(PROVIDERS_FILE.read_text())["providers"]
    return {n: Provider(name=n, label=c.get("label", n), base_url=c["base_url"].rstrip("/"), local=bool(c.get("local")),
                        key_env=c.get("key_env"), rpm=c.get("rpm")) for n, c in raw.items()}


def _setting(key: str, default: Any) -> Any:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return row.value if row is not None else default


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (tolerates ```json fences and surrounding prose)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    dec, start = json.JSONDecoder(), text.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(text[start:])      # real JSON parsing: braces inside strings are handled
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None


class LLMGateway:
    def __init__(self, http: httpx.Client | None = None, providers: dict[str, Provider] | None = None):
        self.http = http or httpx.Client(timeout=httpx.Timeout(90.0, connect=8.0))
        self.providers = providers or load_providers()
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ configuration
    def assignments(self) -> dict[str, list[dict[str, str]]]:
        return _setting(ASSIGN_KEY, {})

    def allow_hosted_internal(self) -> bool:
        return bool(_setting(ALLOW_HOSTED_KEY, False))

    def candidates(self, agent_id: str, *, internal: bool) -> tuple[list[tuple[Provider, str]], list[str]]:
        """Usable (provider, model) pairs for an agent, plus the reasons others were skipped."""
        cfg = self.assignments()
        wanted = cfg.get(agent_id) or cfg.get("*") or []
        out, skipped = [], []
        allow = self.allow_hosted_internal()
        for a in wanted:
            p = self.providers.get(a.get("provider", ""))
            if p is None or not a.get("model"):
                skipped.append(f"{a.get('provider')}: unknown provider or no model")
            elif not p.configured:
                skipped.append(f"{p.name}: no API key ({p.key_env})")
            elif internal and not p.local and not allow:
                skipped.append(f"{p.name}: hosted provider blocked for internal data")
            else:
                out.append((p, a["model"]))
        return out, skipped

    def available(self, agent_id: str, *, internal: bool = False) -> bool:
        return bool(self.candidates(agent_id, internal=internal)[0])

    # ------------------------------------------------------------------ calls
    def _pace(self, p: Provider) -> None:
        if not p.rpm:
            return
        gap = 60.0 / p.rpm
        with self._lock:
            wait = self._last_call.get(p.name, 0) + gap - time.monotonic()
            self._last_call[p.name] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def chat_json(self, agent_id: str, system: str, user: str, *, internal: bool, max_tokens: int = 1400,
                  temperature: float = 0.2) -> tuple[dict | None, dict]:
        """Ask for a JSON object. Never raises: returns (None, meta) when no model produced usable JSON."""
        cands, skipped = self.candidates(agent_id, internal=internal)
        meta: dict[str, Any] = {"attempts": [], "skipped": skipped}
        for p, model in cands:
            self._pace(p)
            headers = {"Content-Type": "application/json"}
            if p.key:
                headers["Authorization"] = f"Bearer {p.key}"
            body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system + "\nReply with ONE JSON object and nothing else."},
                                 {"role": "user", "content": user}]}
            try:
                r = self.http.post(f"{p.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as exc:
                meta["attempts"].append({"provider": p.name, "model": model, "error": type(exc).__name__})
                continue
            if r.status_code != 200:
                meta["attempts"].append({"provider": p.name, "model": model, "error": f"HTTP {r.status_code}"})
                continue
            try:
                msg = r.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                meta["attempts"].append({"provider": p.name, "model": model, "error": "malformed response"})
                continue
            obj = extract_json(msg)
            if obj is None:
                meta["attempts"].append({"provider": p.name, "model": model, "error": "reply was not JSON"})
                continue
            meta.update(provider=p.name, model=model, usage=r.json().get("usage"), local=p.local)
            return obj, meta
        return None, meta

    # ------------------------------------------------------------------ status (for the UI)
    def status(self, probe: bool = False) -> dict[str, Any]:
        rows = []
        for p in self.providers.values():
            row: dict[str, Any] = {"name": p.name, "label": p.label, "local": p.local, "key_env": p.key_env,
                                   "key_present": bool(p.key) if p.key_env else None, "configured": p.configured,
                                   "base_url": p.base_url}
            if probe and p.configured:
                try:
                    r = self.http.get(f"{p.base_url}/models", headers={"Authorization": f"Bearer {p.key}"} if p.key else {},
                                      timeout=6.0)
                    r.raise_for_status()
                    data = r.json().get("data") or r.json().get("models") or []
                    row["reachable"] = True
                    row["models"] = sorted({m.get("id") or m.get("name") for m in data if isinstance(m, dict)})[:300]
                except (httpx.HTTPError, ValueError) as exc:
                    row["reachable"], row["error"] = False, type(exc).__name__
            rows.append(row)
        return {"providers": rows, "assignments": self.assignments(), "allow_hosted_internal": self.allow_hosted_internal()}
