from content_agent.integrations.hermes import event_from_report


def test_hermes_event_is_compact_and_requests_human_auth_action():
    event = event_from_report({
        "status": "human_action_required",
        "human_action_required": True,
        "reason": "instagram_authentication_challenge",
        "scanned": 8,
        "created_items": [],
    })
    assert event["event_type"] == "angellos.content_intelligence.run"
    assert event["requires_attention"] is True
    assert event["next_action"]["action"] == "refresh_instagram_session"
    assert event["summary"]["scanned"] == 8


def test_hermes_event_does_not_require_attention_after_normal_run():
    event = event_from_report({"status": "completed", "content_ideas_created": 1, "costs": {"total_usd": 0.12, "complete": True}})
    assert event["requires_attention"] is False
    assert event["next_action"] is None
    assert event["summary"]["content_ideas_created"] == 1
    assert event["costs"]["total_usd"] == 0.12
