class HealthInput:
    def __init__(self, description, temperature, sleep_hours, heart_rate_bpm,
                 cough_type, sweating, sore_throat):
        self.description = description.lower()
        self.temperature = temperature
        self.sleep_hours = sleep_hours
        self.heart_rate_bpm = heart_rate_bpm
        self.cough_type = cough_type.lower()
        self.sweating = sweating
        self.sore_throat = sore_throat


class WindColdDetector:
    def __init__(self, health_input: HealthInput):
        self.input = health_input

    def evaluate(self):
        signs = {
            'dizziness': 'dizzy' in self.input.description,
            'movement_sensitive': 'walking' in self.input.description or 'movement' in self.input.description,
            'normal_temp': 36.3 <= self.input.temperature <= 37.0,
            'thin_cough': 'thin' in self.input.cough_type,
            'no_sweating': not self.input.sweating,
            'sore_throat': self.input.sore_throat,
        }

        if all(signs.values()):
            return self.generate_diagnosis(signs)
        else:
            return {"diagnosis": "Unclear or Not Wind-Cold", "matched_signs": signs}

    def generate_diagnosis(self, signs):
        diagnosis = "Wind-Cold Common Cold (风寒感冒)"
        suggestions = [
            "🛌 Rest well: target at least 8 hours of sleep tonight.",
            "🧣 Keep warm, especially your neck and back.",
            "🍵 Drink ginger tea with brown sugar to promote sweating.",
            "🥣 Try scallion + ginger congee (葱白姜粥).",
            "❌ Avoid cold/raw food and icy drinks.",
            "📍 Monitor for changes—see a doctor if symptoms worsen in 2–3 days."
        ]
        return {
            "diagnosis": diagnosis,
            "matched_signs": signs,
            "suggestions": suggestions
        }


# === Example Usage ===
if __name__ == "__main__":
    user_input = HealthInput(
        description="I felt a bit dizzy this morning—especially when walking. But when I sit down, I feel fine.",
        temperature=36.7,
        sleep_hours=6.3,
        heart_rate_bpm=70,
        cough_type="thin",
        sweating=False,
        sore_throat=True
    )

    detector = WindColdDetector(user_input)
    result = detector.evaluate()

    print("🩺 Diagnosis Result:")
    print("Diagnosis:", result["diagnosis"])
    print("\nMatched Signs:")
    for k, v in result["matched_signs"].items():
        print(f" - {k}: {'✅' if v else '❌'}")

    if "suggestions" in result:
        print("\n✅ Recovery Suggestions:")
        for s in result["suggestions"]:
            print(" -", s)
