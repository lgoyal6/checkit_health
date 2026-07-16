from fact_checker import _overlap


def test_overlap_accepts_a_close_fact_check_match():
    assert _overlap(
        "Ivermectin cures COVID-19.",
        "Fact check: Ivermectin does not cure COVID-19.",
    ) >= 0.5


def test_overlap_rejects_an_unrelated_fact_check():
    assert _overlap(
        "Ivermectin cures COVID-19.",
        "Vaccines contain microchips.",
    ) < 0.5
