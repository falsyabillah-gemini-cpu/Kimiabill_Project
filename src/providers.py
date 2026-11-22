"""Pluggable multi-model provider router.

Supports:
- antigravity_local: Writes prompt handoffs & reads structured JSON produced by Antigravity.
- claude_code_cli:   Invokes local `claude -p` headless CLI for future Antigravity <-> Claude Code handoffs.
- gemini_api:        Calls Google Gemini REST API directly (with GEMINI_API_KEY).
- anthropic_api:     Calls Anthropic Messages API directly (with ANTHROPIC_API_KEY).
- openrouter:        Calls OpenRouter chat completions (with OPENROUTER_API_KEY).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Type, TypeVar

import requests
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError(f"No JSON object found in model response: {text[:300]}")
    return json.loads(text[start : end + 1])


DEFAULT_MODELS: dict[str, str] = {
    "gemini_api": "gemini-3.5-flash-lite",
    "anthropic_api": "claude-sonnet-4-5",
    "openrouter": "google/gemini-3.5-flash-lite",
    "claude_code_cli": "claude-sonnet-4-5",
}

GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
)

TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _post_with_retry(
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
    max_attempts: int = 4,
    timeout: int = 120,
) -> requests.Response:
    """POST with exponential backoff on transient 429/5xx errors or network hiccups."""
    last_exc: Exception | None = None
    resp: requests.Response | None = None
    for attempt in range(max_attempts):
        try:
            resp = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if resp.status_code not in TRANSIENT_STATUS_CODES:
                return resp
            wait_sec = min(30, 3 * (2 ** attempt))
            print(f"[providers] Transient HTTP {resp.status_code}, retrying in {wait_sec}s (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(wait_sec)
        except requests.RequestException as exc:
            last_exc = exc
            wait_sec = min(30, 3 * (2 ** attempt))
            print(f"[providers] Network error ({exc}), retrying in {wait_sec}s (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(wait_sec)
    if resp is not None:
        return resp
    assert last_exc is not None
    raise last_exc


def _discover_gemini_flash_model(api_key: str) -> str | None:
    """Query Gemini /v1beta/models to find an available Flash model supporting generateContent."""
    try:
        resp = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        models = resp.json().get("models", [])
        candidates: list[str] = []
        for m in models:
            methods = m.get("supportedGenerationMethods", [])
            name = m.get("name", "").removeprefix("models/")
            if "generateContent" in methods and name.startswith("gemini-") and "flash" in name:
                if not any(skip in name for skip in ("tts", "image", "audio", "live", "embedding")):
                    candidates.append(name)
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0]
    except Exception:
        return None
    return None


def resolve_provider_and_model(provider: str, model: str) -> tuple[str, str]:
    """Resolve 'auto' provider based on available environment API keys."""
    if provider != "auto":
        return provider, model or DEFAULT_MODELS.get(provider, "")

    forced = os.environ.get("LLM_PROVIDER", "").strip()
    if forced and forced != "auto":
        return forced, DEFAULT_MODELS.get(forced, model)

    if os.environ.get("GEMINI_API_KEY"):
        env_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_model:
            return "gemini_api", env_model
        return "gemini_api", model if model.startswith("gemini") else DEFAULT_MODELS["gemini_api"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic_api", model if model.startswith("claude") else DEFAULT_MODELS["anthropic_api"]
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter", model if "/" in model else DEFAULT_MODELS["openrouter"]

    raise ProviderError(
        "No LLM API key found for provider='auto'. Set GEMINI_API_KEY, ANTHROPIC_API_KEY, "
        "or OPENROUTER_API_KEY in .env or GitHub Repository Secrets."
    )


def _save_handoff(result: BaseModel, handoff_path: Path | None) -> None:
    if handoff_path is not None:
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def call_structured_agent(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    response_model: Type[T],
    handoff_path: Path | None = None,
) -> T:
    """Route an agent step to the configured provider and validate against a Pydantic schema."""
    provider, model = resolve_provider_and_model(provider, model)
    schema_hint = json.dumps(response_model.model_json_schema(), indent=2)
    prompt_body = (
        f"{system_prompt}\n\n"
        f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}\n\n"
        f"OUTPUT MUST BE VALID JSON MATCHING THIS SCHEMA (no extra markdown):\n{schema_hint}"
    )

    if provider == "antigravity_local":
        if handoff_path is None:
            raise ProviderError("antigravity_local mode requires handoff_path")
        if not handoff_path.exists():
            req_path = handoff_path.with_suffix(".prompt.txt")
            req_path.parent.mkdir(parents=True, exist_ok=True)
            req_path.write_text(prompt_body, encoding="utf-8")
            raise ProviderError(
                f"Handoff required for Antigravity agent. Prompt saved at {req_path}; "
                f"write validated JSON to {handoff_path}."
            )
        raw = json.loads(handoff_path.read_text(encoding="utf-8"))
        return response_model.model_validate(raw)

    if provider == "claude_code_cli":
        cmd = [
            "claude",
            "-p",
            prompt_body,
            "--output-format",
            "json",
        ]
        if model:
            cmd.extend(["--model", model])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ProviderError(f"Claude Code CLI failed: {proc.stderr}")
        outer = json.loads(proc.stdout)
        text = outer.get("result", proc.stdout) if isinstance(outer, dict) else proc.stdout
        parsed = response_model.model_validate(_extract_json(text))
        _save_handoff(parsed, handoff_path)
        return parsed

    if provider == "gemini_api":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not set")
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                                f"Return JSON matching schema:\n{schema_hint}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        tried: list[str] = []
        candidates = [model, *[m for m in GEMINI_FALLBACK_MODELS if m != model]]
        resp = None
        for cand_model in candidates:
            tried.append(cand_model)
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{cand_model}:generateContent?key={api_key}"
            )
            resp = _post_with_retry(url, json_body=body, max_attempts=3)
            if resp.status_code == 200:
                break
            if resp.status_code not in (404, *TRANSIENT_STATUS_CODES):
                break
            print(f"[providers] Gemini model '{cand_model}' returned HTTP {resp.status_code}, trying next fallback...")
        if resp is not None and resp.status_code in (404, *TRANSIENT_STATUS_CODES):
            discovered = _discover_gemini_flash_model(api_key)
            if discovered and discovered not in tried:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{discovered}:generateContent?key={api_key}"
                )
                resp = _post_with_retry(url, json_body=body, max_attempts=3)
        assert resp is not None
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = response_model.model_validate(_extract_json(text))
        _save_handoff(parsed, handoff_path)
        return parsed

    if provider == "anthropic_api":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 2048,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                            f"Return ONLY valid JSON matching schema:\n{schema_hint}"
                        ),
                    }
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        parsed = response_model.model_validate(_extract_json(text))
        _save_handoff(parsed, handoff_path)
        return parsed

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                            f"Return ONLY valid JSON matching schema:\n{schema_hint}"
                        ),
                    },
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        parsed = response_model.model_validate(_extract_json(text))
        _save_handoff(parsed, handoff_path)
        return parsed

    raise ProviderError(f"Unsupported provider: {provider}")
