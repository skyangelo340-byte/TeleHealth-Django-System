import math
import re
from collections import Counter, defaultdict
from decimal import Decimal

# NLP-enhanced Naive Bayes prototype.
# This is an academic/decision-support model, not a medical diagnostic model.
MODEL_VERSION = "NLP-NB-2.0"

TRAINING_DATA = [
    # General Medicine
    ("General Medicine", "fever cough headache body pain"),
    ("General Medicine", "fever chills weakness fatigue"),
    ("General Medicine", "stomach pain abdominal pain nausea"),
    ("General Medicine", "vomiting diarrhea stomach ache"),
    ("General Medicine", "dizziness headache weakness"),
    ("General Medicine", "persistent cough cold flu"),
    ("General Medicine", "sore throat fever cough"),
    ("General Medicine", "back pain muscle pain fatigue"),
    ("General Medicine", "high temperature body aches"),
    ("General Medicine", "nausea vomiting fever"),
    ("General Medicine", "my head hurts and I feel weak"),
    ("General Medicine", "I have a fever and chills"),
    ("General Medicine", "my stomach hurts and I feel nauseous"),
    ("General Medicine", "I have been coughing with a sore throat"),
    ("General Medicine", "I feel dizzy and tired"),

    # Dermatology
    ("Dermatology", "skin rash itchy skin"),
    ("Dermatology", "red rash itching skin irritation"),
    ("Dermatology", "eczema dry itchy skin"),
    ("Dermatology", "skin allergy rash"),
    ("Dermatology", "hives itchy red patches"),
    ("Dermatology", "acne pimples skin inflammation"),
    ("Dermatology", "skin infection redness swelling"),
    ("Dermatology", "itchy skin rash spots"),
    ("Dermatology", "my skin is very itchy with red patches"),
    ("Dermatology", "I have a red rash on my arms"),
    ("Dermatology", "my skin is irritated and itchy"),
    ("Dermatology", "there are itchy spots on my skin"),

    # Dental
    ("Dental", "tooth pain toothache"),
    ("Dental", "painful tooth swollen gums"),
    ("Dental", "bleeding gums tooth pain"),
    ("Dental", "dental pain cavity"),
    ("Dental", "swollen gum jaw pain"),
    ("Dental", "broken tooth mouth pain"),
    ("Dental", "tooth sensitivity gum swelling"),
    ("Dental", "my tooth hurts when I eat"),
    ("Dental", "I have painful swollen gums"),
    ("Dental", "my tooth has been hurting since yesterday"),
    ("Dental", "I think I have a cavity and tooth pain"),

    # Ophthalmology
    ("Ophthalmology", "eye pain blurred vision"),
    ("Ophthalmology", "red eye eye irritation"),
    ("Ophthalmology", "blurred vision eye pain"),
    ("Ophthalmology", "itchy eyes watery eyes"),
    ("Ophthalmology", "eye swelling vision problem"),
    ("Ophthalmology", "difficulty seeing eye discomfort"),
    ("Ophthalmology", "I cannot see clearly"),
    ("Ophthalmology", "my eyes are red and irritated"),
    ("Ophthalmology", "my vision is blurry"),
    ("Ophthalmology", "my eyes are itchy and watery"),

    # Pediatrics
    ("Pediatrics", "child fever cough"),
    ("Pediatrics", "baby fever crying"),
    ("Pediatrics", "child vomiting diarrhea"),
    ("Pediatrics", "child cough cold fever"),
    ("Pediatrics", "infant fever weakness"),
    ("Pediatrics", "young child stomach pain"),
    ("Pediatrics", "kid sore throat fever"),

    # Obstetrics
    ("Obstetrics", "pregnant pregnancy abdominal pain"),
    ("Obstetrics", "pregnancy nausea vomiting"),
    ("Obstetrics", "pregnant back pain"),
    ("Obstetrics", "pregnancy bleeding"),
    ("Obstetrics", "pregnant headache dizziness"),
    ("Obstetrics", "pregnancy checkup concern"),
    ("Obstetrics", "pregnant abdominal discomfort"),
    ("Obstetrics", "I am pregnant and have abdominal discomfort"),
    ("Obstetrics", "I am pregnant and feeling nauseous"),
]

# Natural-language phrases are normalized into concepts already represented
# in the training vocabulary. Matching is deliberately transparent and
# deterministic so the prototype can be audited during a school/demo project.
SYMPTOM_ALIASES = {
    "headache": [
        r"\bheadache\b",
        r"\bhead aches?\b",
        r"\bhead hurts?\b",
        r"\bhead .*hurts?\b",
        r"\bmy head is hurting\b",
    ],
    "fever": [
        r"\bfever\b",
        r"\bfeverish\b",
        r"\bhigh temperature\b",
        r"\btemperature is high\b",
    ],
    "cough": [r"\bcough(?:ing)?\b"],
    "sore throat": [r"\bsore throat\b", r"\bthroat hurts?\b"],
    "chills": [r"\bchills?\b", r"\bshivering\b"],
    "weakness": [r"\bweak(?:ness)?\b", r"\bfeeling weak\b"],
    "fatigue": [r"\bfatigue\b", r"\btired\b", r"\bvery tired\b", r"\bexhausted\b"],
    "body pain": [r"\bbody aches?\b", r"\bbody pain\b", r"\bwhole body hurts?\b"],
    "stomach pain": [r"\bstomach ache\b", r"\bstomach pain\b", r"\bstomach hurts?\b"],
    "abdominal pain": [r"\babdominal pain\b", r"\babdomen hurts?\b", r"\btummy hurts?\b"],
    "nausea": [r"\bnausea\b", r"\bnauseous\b", r"\bfeel sick\b"],
    "vomiting": [r"\bvomit(?:ing)?\b", r"\bthrowing up\b", r"\bthrew up\b"],
    "diarrhea": [r"\bdiarrhea\b", r"\bdiarrhoea\b", r"\bloose stools?\b"],
    "dizziness": [r"\bdizz(?:y|iness)\b", r"\blightheaded\b", r"\blight headed\b"],
    "back pain": [r"\bback pain\b", r"\bback hurts?\b"],
    "muscle pain": [r"\bmuscle pain\b", r"\bmuscles? hurt\b"],

    "itchy skin": [
        r"\bitchy skin\b",
        r"\bskin is itchy\b",
        r"\bskin has been itchy\b",
        r"\bskin has been .* itchy\b",
        r"\bskin keeps itching\b",
        r"\bskin itching\b",
    ],
    "skin rash": [r"\bskin rash\b", r"\bred rash\b", r"\brash\b"],
    "skin irritation": [r"\bskin irritation\b", r"\bskin is irritated\b", r"\bskin irritation\b"],
    "eczema": [r"\beczema\b"],
    "hives": [r"\bhives?\b"],
    "red patches": [r"\bred patches?\b", r"\bred marks?\b"],
    "acne": [r"\bacne\b", r"\bpimples?\b"],
    "skin inflammation": [r"\bskin inflammation\b", r"\binflamed skin\b"],

    "tooth pain": [
        r"\btooth pain\b",
        r"\btoothache\b",
        r"\btooth hurts?\b",
        r"\btooth has been hurting\b",
        r"\bteeth hurt\b",
    ],
    "swollen gums": [r"\bswollen gums?\b", r"\bswelling in my gums?\b"],
    "bleeding gums": [r"\bbleeding gums?\b", r"\bgums? are bleeding\b"],
    "cavity": [r"\bcavit(?:y|ies)\b"],
    "jaw pain": [r"\bjaw pain\b", r"\bjaw hurts?\b"],
    "broken tooth": [r"\bbroken tooth\b", r"\bchipped tooth\b"],
    "tooth sensitivity": [r"\btooth sensitivity\b", r"\bsensitive tooth\b"],

    "eye pain": [r"\beye pain\b", r"\beye hurts?\b", r"\beyes hurt\b"],
    "blurred vision": [
        r"\bblurred vision\b",
        r"\bblurry vision\b",
        r"\bvision is blurry\b",
        r"\bcan'?t see clearly\b",
        r"\bcannot see clearly\b",
        r"\bcan not see clearly\b",
        r"\bdifficulty seeing\b",
    ],
    "red eye": [r"\bred eye\b", r"\bred eyes\b", r"\beyes are red\b"],
    "eye irritation": [r"\beye irritation\b", r"\beyes? are irritated\b"],
    "itchy eyes": [r"\bitchy eyes?\b", r"\beyes? itch\b"],
    "watery eyes": [r"\bwatery eyes?\b", r"\beyes? are watering\b"],
    "eye swelling": [r"\beye swelling\b", r"\bswollen eye\b", r"\bswollen eyes\b"],
    "vision problem": [r"\bvision problem\b", r"\bproblem with my vision\b"],

    "pregnancy": [r"\bpregnan(?:t|cy)\b", r"\bexpecting a baby\b", r"\bexpecting\b"],
    "pregnancy bleeding": [r"\bpregnancy bleeding\b", r"\bpregnant.*\bbleeding\b"],
    "pregnancy nausea": [r"\bpregnant.*\bnause(?:a|ous)\b"],
}

EMERGENCY_ALIASES = {
    "chest pain": [
        r"\bchest pain\b",
        r"\bpain in my chest\b",
        r"\bchest pressure\b",
    ],
    "difficulty breathing": [
        r"\bdifficulty breathing\b",
        r"\btrouble breathing\b",
        r"\bshortness of breath\b",
        r"\bshort of breath\b",
        r"\bcan'?t breathe\b",
        r"\bcannot breathe\b",
        r"\bcan not breathe\b",
        r"\bbreathing difficulty\b",
    ],
    "severe bleeding": [
        r"\bsevere bleeding\b",
        r"\bheavy bleeding\b",
        r"\bbleeding heavily\b",
        r"\bbleeding a lot\b",
    ],
    "unconscious": [
        r"\bunconscious\b",
        r"\bpassed out\b",
        r"\blost consciousness\b",
        r"\bloss of consciousness\b",
    ],
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "i", "in", "is", "it", "my",
    "of", "on", "or", "that", "the", "this", "to", "very", "was", "with",
    "since", "there", "were", "am", "also", "feel", "feeling",
}


def _tokenize(text):
    tokens = re.findall(r"[a-z]+", str(text).lower())
    return [token for token in tokens if token not in STOPWORDS]


def _clean_text(text):
    text = str(text or "").lower()
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _matches(text, patterns):
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def extract_symptoms(text):
    """Extract transparent, canonical symptom concepts from natural language."""
    cleaned = _clean_text(text)
    found = []

    for concept, patterns in SYMPTOM_ALIASES.items():
        if _matches(cleaned, patterns) and concept not in found:
            found.append(concept)

    return found


def extract_emergency_flags(text):
    """Return deterministic emergency concepts found in the patient's wording."""
    cleaned = _clean_text(text)
    return {
        concept: _matches(cleaned, patterns)
        for concept, patterns in EMERGENCY_ALIASES.items()
    }


def _expanded_model_text(text):
    cleaned = _clean_text(text)
    concepts = extract_symptoms(cleaned)

    # Keep the patient's original wording and append canonical concepts.
    # The canonical concepts make common paraphrases understandable to NB.
    return " ".join([cleaned] + concepts)


def _build_model():
    class_counts = Counter()
    word_counts = defaultdict(Counter)
    total_words = Counter()
    vocabulary = set()

    for label, text in TRAINING_DATA:
        tokens = _tokenize(text)

        class_counts[label] += 1

        for token in tokens:
            word_counts[label][token] += 1
            total_words[label] += 1
            vocabulary.add(token)

    return {
        "class_counts": class_counts,
        "word_counts": word_counts,
        "total_words": total_words,
        "vocabulary": vocabulary,
        "total_examples": len(TRAINING_DATA),
    }


_MODEL = _build_model()


def _predict(text):
    model_text = _expanded_model_text(text)
    tokens = _tokenize(model_text)

    if not tokens:
        return "General Medicine", Decimal("50.00")

    class_counts = _MODEL["class_counts"]
    word_counts = _MODEL["word_counts"]
    total_words = _MODEL["total_words"]
    vocabulary_size = len(_MODEL["vocabulary"])
    total_examples = _MODEL["total_examples"]

    scores = {}

    for label, class_count in class_counts.items():
        score = math.log(class_count / total_examples)
        denominator = total_words[label] + vocabulary_size

        for token in tokens:
            count = word_counts[label].get(token, 0)
            probability = (count + 1) / denominator
            score += math.log(probability)

        scores[label] = score

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    best_label, _ = ranked[0]
    max_score = max(scores.values())

    exp_scores = {
        label: math.exp(score - max_score)
        for label, score in scores.items()
    }
    total = sum(exp_scores.values())

    confidence = (exp_scores[best_label] / total) * 100
    confidence = max(50.0, min(98.0, confidence))

    return best_label, Decimal(f"{confidence:.2f}")


def predict_specialty(data):
    symptoms = data.get("symptoms", [])

    if isinstance(symptoms, str):
        symptom_text = symptoms
    else:
        symptom_text = " ".join(
            str(value).strip()
            for value in symptoms
            if str(value).strip()
        )

    questionnaire_text = []

    if data.get("chest_pain"):
        questionnaire_text.append("chest pain")

    if data.get("difficulty_breathing"):
        questionnaire_text.append("difficulty breathing")

    if data.get("severe_bleeding"):
        questionnaire_text.append("severe bleeding")

    if data.get("unconscious"):
        questionnaire_text.append("unconscious")

    combined_text = " ".join(
        [symptom_text] + questionnaire_text
    )

    return _predict(combined_text)


def ai_triage_recommendation(data):
    specialty, confidence = predict_specialty(data)

    symptoms = data.get("symptoms", "")
    if isinstance(symptoms, list):
        symptoms = " ".join(str(value) for value in symptoms)

    return {
        "specialty": specialty,
        "confidence": confidence,
        "model": MODEL_VERSION,
        "extracted_symptoms": extract_symptoms(symptoms),
    }
