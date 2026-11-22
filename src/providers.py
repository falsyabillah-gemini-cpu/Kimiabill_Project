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
    "gemini_api": "gemini-2.5-flash",
    "anthropic_api": "claude-sonnet-4-5",
    "openrouter": "google/gemini-2.5-flash",
    "claude_code_cli": "claude-sonnet-4-5",
}


def resolve_provider_and_model(provider: str, model: str) -> tuple[str, str]:
    """Resolve 'auto' provider based on available environment API keys."""
    if provider != "auto":
        return provider, model or DEFAULT_MODELS.get(provider, "")

    forced = os.environ.get("LLM_PROVIDER", "").strip()
    if forced and forced != "auto":
        return forced, DEFAULT_MODELS.get(forced, model)

    if os.environ.get("GEMINI_API_KEY"):
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
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
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
        resp = requests.post(url, json=body, timeout=120)
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
