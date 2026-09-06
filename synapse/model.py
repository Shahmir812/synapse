from __future__ import annotations

import os

from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ModelConfigError(Exception):
    """Required model settings are missing."""


def create_model_client() -> tuple[OpenAI, str]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get("OPENROUTER_DEFAULT_MODEL", "").strip()
    for name, value in (
        ("OPENROUTER_API_KEY", api_key),
        ("OPENROUTER_DEFAULT_MODEL", model),
    ):
        if not value:
            raise ModelConfigError(f"Set {name} in your environment or .env file before using Ask.")

    return OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=30.0,
        max_retries=0,
    ), model


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def gemini_is_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip())


def create_gemini_client() -> tuple[OpenAI, str]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "").strip()
    if not api_key:
        raise ModelConfigError("Set GEMINI_API_KEY to your Google AI Studio API key.")
    if not model:
        raise ModelConfigError("Set GEMINI_MODEL to a Google AI Studio model ID (without the google/ prefix).")
    return OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL, timeout=30.0, max_retries=0), model
