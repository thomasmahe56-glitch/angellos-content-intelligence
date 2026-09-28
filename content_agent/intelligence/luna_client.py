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
        self.input_tokens = 0
        self.output_tokens = 0

    def _validate_config(self) -> None:
        if not is_configured(self.settings.openai_api_key) or not is_configured(self.settings.openai_content_model):
            raise LunaError("OPENAI_API_KEY and OPENAI_CONTENT_MODEL are required for Luna")

    async def json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self._validate_config()
        self.calls += 1
        payload = {
            "model": self.settings.openai_content_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "content_agent", "strict": True, "schema": schema}},
        }
        # Some frontier models only accept their provider default temperature.
        # Omit it unless an explicitly compatible override is configured.
        if self.settings.luna_temperature is not None:
            payload["temperature"] = self.settings.luna_temperature
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
                usage = data.get("usage", {})
                self.input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.output_tokens += int(usage.get("completion_tokens", 0) or 0)
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

    def estimated_cost_usd(self):
        if not self.calls:
            return 0.0
        input_rate = self.settings.luna_input_usd_per_million_tokens
        output_rate = self.settings.luna_output_usd_per_million_tokens
        if input_rate is None or output_rate is None:
            return None
        return (self.input_tokens * input_rate + self.output_tokens * output_rate) / 1_000_000


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
