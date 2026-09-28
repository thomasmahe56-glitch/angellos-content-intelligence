from __future__ import annotations

import json
import re
import asyncio
from typing import Any, Optional, Union

import httpx

from content_agent.config import Settings
from content_agent.config import is_configured


class LunaError(RuntimeError):
    pass


class LunaClient:
    """OpenAI-compatible structured-output client. Model identity remains environment-configured."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls = 0

    def _validate_config(self) -> None:
        if not is_configured(self.settings.openai_api_key) or not is_configured(self.settings.openai_content_model):
            raise LunaError("OPENAI_API_KEY and OPENAI_CONTENT_MODEL are required for Luna")

    async def json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self._validate_config()
        self.calls += 1
        payload = {
            "model": self.settings.openai_content_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_schema", "json_schema": {"name": "content_agent", "strict": True, "schema": schema}},
        }
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {self.settings.openai_api_key}"}, json=payload)
                if response.status_code >= 500 or response.status_code == 429:
                    raise LunaError(f"Luna temporary API error {response.status_code}")
                if response.status_code >= 400:
                    raise LunaError(f"Luna API error {response.status_code}: {response.text[:300]}")
                data = response.json()
                break
            except (httpx.HTTPError, LunaError) as exc:
                last_error = exc
                if attempt == 2 or (isinstance(exc, LunaError) and "temporary" not in str(exc)):
                    raise
                await asyncio.sleep(2 ** attempt)
        else:
            raise LunaError(str(last_error))
        content = data["choices"][0]["message"]["content"]
        return parse_json(content)


def parse_json(raw: Union[str, dict]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LunaError("Luna returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LunaError("Luna JSON response must be an object")
    return data
