from local_speech import build_stt_prompt


def test_prompt_includes_phonetic_alphabet_and_base():
    prompt = build_stt_prompt(None)
    assert "Alfa Bravo Charlie" in prompt
    assert "ICAO English" in prompt


def test_prompt_appends_expected_phrase_and_tokens_last():
    prompt = build_stt_prompt({"phrase": "Ready for taxi", "tokens": ["25R", "DLH123"]})
    # Expected content is appended after the generic bias (survives truncation).
    assert prompt.index("Ready for taxi") > prompt.index("Alfa Bravo Charlie")
    assert "25R" in prompt and "DLH123" in prompt
