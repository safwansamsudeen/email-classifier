import json
from classifier import classify_event

with open("ashoka_event_testbank_100.json", encoding="utf-8") as f:
    emails = json.load(f)["emails"]

tp = fp = tn = fn = 0
errors = []

for item in emails:
    pred = classify_event(item["sender"], item["subject"], item["body"], threshold=0.5)
    y_true, y_pred = item["is_event"], pred["is_event"]
    print(y_true, y_pred)
    if y_true and y_pred:
        tp += 1
    elif not y_true and y_pred:
        fp += 1
        errors.append(("FP", item["id"], item["edge_case_type"], pred["probability"], item["subject"]))
    elif y_true and not y_pred:
        fn += 1
        errors.append(("FN", item["id"], item["edge_case_type"], pred["probability"], item["subject"]))
    else:
        tn += 1

precision = tp / (tp + fp) if (tp + fp) else 0.0
recall = tp / (tp + fn) if (tp + fn) else 0.0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

print(f"Accuracy: {(tp + tn) / len(emails):.1%} | Precision: {precision:.3f} | Recall: {recall:.3f} | F1: {f1:.3f}")
for err in errors:
    print(" ", err)
