def test_gemini_bad_json_is_safe():
    # Parser is deliberately lazy-imported with the legacy Gemini SDK in runtime.
    # Static compilation of that module is covered by the import check.
    from content_agent.video import gemini_analyzer
    try:
        gemini_analyzer.parse_gemini_json({"raw_response": "bad"})
    except gemini_analyzer.GeminiAnalysisError:
        assert True
    else:
        assert False
