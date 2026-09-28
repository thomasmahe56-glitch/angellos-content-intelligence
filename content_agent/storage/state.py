from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_agent.config import Settings, is_configured
from content_agent.models.schemas import Candidate


class StateStore:
    """Small state store with a production Notion-page option and safe local fallback.

    Railway's filesystem is never treated as production persistence: production health
    requires NOTION_CONTENT_AGENT_STATE_PAGE_ID.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.local_state_path
        self.notion = None
        if is_configured(settings.notion_api_key) and is_configured(settings.notion_state_page_id):
            from notion_client import Client
            self.notion = Client(auth=settings.notion_api_key)

    @property
    def durable(self) -> bool:
        return self.notion is not None

    def load(self, *, strict: bool = False) -> dict[str, Any]:
        if self.notion:
            try:
                blocks = self._notion_blocks()
                payload = "".join(
                    "".join(x.get("plain_text", "") for x in b.get("code", {}).get("rich_text", []))
                    for b in blocks if b.get("type") == "code" and b.get("code", {}).get("caption", [])
                    and b["code"]["caption"][0].get("plain_text") == "content-agent-state"
                )
                if payload:
                    return json.loads(payload)
            except Exception as exc:
                # A transient Notion failure must not erase local recovery state.
                # Production callers use strict mode: stale Railway disk state
                # is not a safe substitute for the durable idempotency ledger.
                if strict:
                    raise RuntimeError("durable state read failed") from exc
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"reels": {}, "followers": {}}

    def save(self, state: dict[str, Any]) -> None:
        serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if self.notion:
            # Touch only code blocks created by this agent; the state page may
            # legitimately contain human notes or setup instructions.
            for block in self._notion_blocks():
                code = block.get("code", {})
                caption = code.get("caption", [])
                owned = caption and caption[0].get("plain_text") == "content-agent-state"
                if not owned:
                    continue
                self.notion.blocks.delete(block_id=block["id"])
            # Notion rich_text content is capped. Chunks carry an ownership
            # caption so later runs never delete unrelated page content.
            for offset in range(0, len(serialized), 1800):
                self.notion.blocks.children.append(
                    block_id=self.settings.notion_state_page_id,
                    children=[{"object": "block", "type": "code", "code": {"language": "json", "caption": [{"type": "text", "text": {"content": "content-agent-state"}}], "rich_text": [{"type": "text", "text": {"content": serialized[offset:offset + 1800]}}]}}],
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(serialized)

    @staticmethod
    def mark(state: dict[str, Any], candidate: Candidate, status: str, **extra: Any) -> None:
        record = state.setdefault("reels", {}).setdefault(candidate.key, candidate.to_dict())
        record.update(candidate.to_dict())
        # Never persist a full video analysis or generated script here: Notion
        # owns the editorial artifact; this store is an idempotency ledger.
        for key, value in extra.items():
            if key in {"gemini_analysis", "adaptation", "local_video_path"}:
                continue
            record[key] = str(value)[:500] if key == "error" else value
        record["status"] = status

    def _notion_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor = None
        while True:
            kwargs: dict[str, Any] = {"block_id": self.settings.notion_state_page_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self.notion.blocks.children.list(**kwargs)
            blocks.extend(response.get("results", []))
            if not response.get("has_more"):
                return blocks
            cursor = response.get("next_cursor")
