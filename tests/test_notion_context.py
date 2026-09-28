from phase2_analysis.notion_context import _property_text


def test_product_context_reads_human_authored_rich_text_properties():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Positioning"}]},
            "Content": {"type": "rich_text", "rich_text": [{"plain_text": "Automate qualified conversations."}]},
            "Active": {"type": "checkbox", "checkbox": True},
        }
    }
    assert _property_text(page) == "Content: Automate qualified conversations."
