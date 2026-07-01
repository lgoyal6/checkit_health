"""Stage 1.5: cheap keyword pre-filter.

Runs before the LLM classifier to drop obviously non-medical posts so they
never cost an API call. This is intentionally permissive — it should let
through anything that *might* be a health claim and only reject clear misses.
"""

import re
from typing import List

# Common health / medicine vocabulary. Matched as whole words, case-insensitive.
# Kept broad on purpose: a false positive here just means one extra LLM call,
# while a false negative silently drops a real claim.
KEYWORDS: List[str] = [
    "vaccine", "vaccinated", "vaccination", "vax", "antivax", "jab", "booster",
    "drug", "drugs", "medication", "medicine", "pill", "pills", "dose", "dosage",
    "treatment", "treat", "cure", "cures", "heal", "healing", "remedy", "therapy",
    "symptom", "symptoms", "disease", "illness", "infection", "virus", "viral",
    "bacteria", "cancer", "tumor", "diabetes", "covid", "coronavirus", "flu",
    "measles", "autism", "immune", "immunity", "antibody", "antibodies",
    "doctor", "doctors", "physician", "nurse", "hospital", "clinic", "patient",
    "fda", "cdc", "who", "pfizer", "moderna", "big pharma", "pharma", "pharmaceutical",
    "health", "healthy", "diet", "nutrition", "vitamin", "vitamins", "supplement",
    "supplements", "protein", "detox", "toxin", "toxins", "chemical", "chemicals",
    "mrna", "dna", "gene", "genetic", "steroid", "antibiotic", "antibiotics",
    "insulin", "blood", "heart", "lung", "brain", "liver", "kidney",
    "ivermectin", "hydroxychloroquine", "chemo", "chemotherapy", "radiation",
    "fluoride", "mercury", "aluminum", "microchip", "5g", "essential oil",
    "cholesterol", "sugar", "carbs", "fasting", "keto", "weight loss", "obesity",
    "depression", "anxiety", "mental health", "sunscreen", "cancer-causing",
    "carcinogen", "overdose", "side effect", "side effects", "clinical trial",
]

# Precompile a single alternation regex with word boundaries. Multi-word phrases
# are included verbatim; \b around them still works because they start/end on
# word characters.
_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def is_possibly_medical(text: str) -> bool:
    """Return True if the text contains any health-related keyword.

    Fast, no network. Used to skip the LLM classifier for obviously
    non-medical posts.
    """
    if not text:
        return False
    return _PATTERN.search(text) is not None
