from prefilter import is_possibly_medical


def test_matches_a_single_health_keyword():
    assert is_possibly_medical("New study on the covid vaccine came out today")


def test_matches_a_multi_word_phrase():
    assert is_possibly_medical("They claim big pharma is hiding the truth")


def test_matching_is_case_insensitive():
    assert is_possibly_medical("IVERMECTIN cured everyone in the trial")


def test_rejects_obviously_non_medical_text():
    assert not is_possibly_medical("The football match went to penalties last night")


def test_requires_whole_word_boundaries():
    # "vaxx" style substrings inside unrelated words must not match "vax".
    assert not is_possibly_medical("I relaxed on the beach all weekend")


def test_empty_or_missing_text_is_not_medical():
    assert not is_possibly_medical("")
    assert not is_possibly_medical(None)
