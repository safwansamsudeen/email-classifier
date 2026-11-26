import os
from time import time
import requests

# Set D1_BACKEND="local" (Ollama) or "liquid" (hosted API)
BACKEND = os.getenv("D1_BACKEND", "liquid").lower()

CONFIG = {
    "local": {
        "url": "http://localhost:11434/v1/systemone",
        "model": "d1",  # Must match `NAME` in `ollama list` (e.g., "d1" or "d1:3b")
        "api_key": None,
    },
    "liquid": {
        "url": "https://api.liquid.ai/decisions/v1/systemone",
        "model": "d1.2",
        "api_key": os.getenv("LIQUID_API_KEY"),
    },
}

EVENT_INSTRUCTIONS = (
    "Is this email announcing, inviting people to, or reminding them about a scheduled "
    "event or gathering (such as a talk, seminar, workshop, club activity, screening, "
    "performance, mixer, sports match, town hall, or live info session)? "
    "Answer no for administrative deadlines (course registration, fee payments, "
    "application cutoffs), core-team hiring or recruitment calls, general policy notices, "
    "mess menu updates, IT alerts, or job/internship postings without a live session."
)


def classify_event(sender: str, subject: str, body: str, threshold: float = 0.5) -> dict:
    if BACKEND not in CONFIG:
        raise ValueError(f"Unsupported BACKEND '{BACKEND}'. Use 'local' or 'liquid'.")

    cfg = CONFIG[BACKEND]
    headers = {"Content-Type": "application/json"}

    if cfg["api_key"] is not None or BACKEND == "liquid":
        if not cfg["api_key"]:
            raise RuntimeError("LIQUID_API_KEY must be set when D1_BACKEND='liquid'.")
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    payload = {
        "model": cfg["model"],
        "state": f"From: {sender}\nSubject: {subject}\n\n{body.strip()}",
        "questions": {
            "is_event": {
                "type": "noul",
                "instructions": EVENT_INSTRUCTIONS,
            }
        },
    }

    resp = requests.post(cfg["url"], json=payload, headers=headers, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"{BACKEND} API error {resp.status_code}: {resp.text}")

    prob = float(resp.json()["answers"]["is_event"]["noul"])
    return {
        "backend": BACKEND,
        "is_event": prob >= threshold,
        "probability": round(prob, 4),
    }





# For testing alone

MAIL_SAMPLE = """
Dear All,


Greetings from HDFC Library!


On the occasion of Constitution Day of India, 26 November, 2026 , HDFC Library, Ashoka University, warmly invites all members of the university community to explore a special display of books on the Indian Constitution.


The curated collection offers an opportunity to discover the ideas, values, history, and principles that shape our democracy and deepen our understanding of the Constitution.


Let us come together to honour the foundational ideals of Justice, Liberty, Equality, and Fraternity that guide our nation.


Event Details:


• Date: 26 November, 2026

• Venue: AC04, HDFC Library, 2nd Floor

• Time: 11:00 AM Onwards

• Event: Special Book Display on the Indian Constitution




We encourage all faculty members and students to visit the library, explore the collection, and take a moment to reflect on the values enshrined in our Constitution.


Read. Reflect. Remember. Celebrate the Constitution!


We look forward to your participation.

Warm Regards,
​​​​​​​Library Operations
"""

email = {
    "sender": "economics-dept@ashoka.edu.in",
    "subject": "Welcome email",
    "body": MAIL_SAMPLE,
}

if __name__ == "__main__":
    a = time()
    result = classify_event(email["sender"], email["subject"], email["body"])
    b = time()
    print(f"{MAIL_SAMPLE.strip()[:50]} -> {result}")
    print(f"Time taken: {b - a:.4f}s")
