import math
import re
from collections import Counter, defaultdict
from decimal import Decimal

MODEL_VERSION = "NB-1.0"

TRAINING_DATA = [
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

    ("Dermatology", "skin rash itchy skin"),
    ("Dermatology", "red rash itching skin irritation"),
    ("Dermatology", "eczema dry itchy skin"),
    ("Dermatology", "skin allergy rash"),
    ("Dermatology", "hives itchy red patches"),
    ("Dermatology", "acne pimples skin inflammation"),
    ("Dermatology", "skin infection redness swelling"),
    ("Dermatology", "itchy skin rash spots"),

    ("Dental", "tooth pain toothache"),
    ("Dental", "painful tooth swollen gums"),
    ("Dental", "bleeding gums tooth pain"),
    ("Dental", "dental pain cavity"),
    ("Dental", "swollen gum jaw pain"),
    ("Dental", "broken tooth mouth pain"),
    ("Dental", "tooth sensitivity gum swelling"),

    ("Ophthalmology", "eye pain blurred vision"),
    ("Ophthalmology", "red eye eye irritation"),
    ("Ophthalmology", "blurred vision eye pain"),
    ("Ophthalmology", "itchy eyes watery eyes"),
    ("Ophthalmology", "eye swelling vision problem"),
    ("Ophthalmology", "difficulty seeing eye discomfort"),

    ("Pediatrics", "child fever cough"),
    ("Pediatrics", "baby fever crying"),
    ("Pediatrics", "child vomiting diarrhea"),
    ("Pediatrics", "child cough cold fever"),
    ("Pediatrics", "infant fever weakness"),
    ("Pediatrics", "young child stomach pain"),
    ("Pediatrics", "kid sore throat fever"),

    ("Obstetrics", "pregnant pregnancy abdominal pain"),
    ("Obstetrics", "pregnancy nausea vomiting"),
    ("Obstetrics", "pregnant back pain"),
    ("Obstetrics", "pregnancy bleeding"),
    ("Obstetrics", "pregnant headache dizziness"),
    ("Obstetrics", "pregnancy checkup concern"),
    ("Obstetrics", "pregnant abdominal discomfort"),
]


def _tokenize(text):
    return re.findall(r"[a-z]+", str(text).lower())


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
    tokens = _tokenize(text)

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

    best_label, best_score = ranked[0]

    max_score = max(scores.values())

    exp_scores = {
        label: math.exp(score - max_score)
        for label, score in scores.items()
    }

    total = sum(exp_scores.values())

    confidence = (
        exp_scores[best_label] / total
    ) * 100

    confidence = max(
        50.0,
        min(98.0, confidence),
    )

    return best_label, Decimal(f"{confidence:.2f}")


def predict_specialty(data):
    symptoms = data.get("symptoms", [])

    if isinstance(symptoms, str):
        symptoms = [symptoms]

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

    return {
        "specialty": specialty,
        "confidence": confidence,
        "model": MODEL_VERSION,
    }