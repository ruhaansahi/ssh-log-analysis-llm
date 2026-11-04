"""Deterministic scoring against reviewed or explicitly weak reference labels."""

from collections import Counter
import json
from pathlib import Path
import re
import statistics
import time

from .schema import FINDINGS, RULES, annotate, validate_answer
from .storage import read_jsonl, write_json, write_jsonl


def parse_prediction(raw, lines):
    def reject_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    except (ValueError, TypeError):
        return None, False, ["Invalid JSON"]
    errors = validate_answer(value, [line["id"] for line in lines])
    return (None if errors else value), True, errors


def prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
            "tp": tp, "fp": fp, "fn": fn}


def score_predictions(rows, predictions, include_content_diagnostic=True):
    if not rows:
        raise ValueError("Cannot score an empty dataset")
    indexed = {}
    for prediction in predictions:
        if prediction["id"] in indexed:
            raise ValueError("Duplicate prediction ID")
        indexed[prediction["id"]] = prediction
    if set(indexed) - {r["id"] for r in rows}:
        raise ValueError("Predictions contain unknown incident IDs")
    labels = {label: Counter(tp=0, fp=0, fn=0) for label in FINDINGS}
    fields = Counter()
    evidence = Counter(tp=0, fp=0, fn=0)
    syntactic = valid = exact = unsupported = invalid = 0
    cited = supported_citations = 0
    latencies, details = [], []
    for row in rows:
        prediction = indexed.get(row["id"], {"raw": ""})
        answer, is_json, errors = parse_prediction(prediction["raw"], row["lines"])
        syntactic += is_json
        valid += answer is not None
        invalid += answer is None
        target = row["answer"]
        expected = set(target["findings"])
        actual = set(answer["findings"]) if answer else set()
        for label in FINDINGS:
            labels[label]["tp"] += label in actual & expected
            labels[label]["fp"] += label in actual - expected
            labels[label]["fn"] += label in expected - actual
        for field in ("user", "source_ip", "connection_outcome", "server_hostname"):
            fields[field] += answer is not None and answer[field] == target[field]
        gold_evidence = set(target["evidence_line_ids"])
        pred_evidence = set(answer["evidence_line_ids"]) if answer else set()
        evidence["tp"] += len(gold_evidence & pred_evidence)
        evidence["fp"] += len(pred_evidence - gold_evidence)
        evidence["fn"] += len(gold_evidence - pred_evidence)
        unsupported += bool(actual - expected)
        for line in row["lines"]:
            if line["id"] in pred_evidence:
                cited += 1
                supported_citations += any(
                    f in actual and pattern.search(line["message"]) for f, pattern in RULES.items()
                ) or (actual == {"insufficient_evidence"} and expected == actual)
        matches = answer is not None and all(
            set(answer[key]) == set(target[key]) if key in ("findings", "evidence_line_ids") else answer[key] == target[key]
            for key in target
        )
        exact += matches
        if prediction.get("latency_s") is not None:
            latencies.append(prediction["latency_s"])
        details.append({"id": row["id"], "exact_match": matches, "errors": errors,
                        "expected": target, "predicted": answer, "raw": prediction["raw"]})
    per_class = {label: prf(**dict(counts)) for label, counts in labels.items()}
    totals = {key: sum(c[key] for c in labels.values()) for key in ("tp", "fp", "fn")}
    present = [v["f1"] for v in per_class.values() if v["tp"] + v["fn"]]
    metrics = {
        "incidents": len(rows), "valid_json_rate": syntactic / len(rows),
        "schema_valid_rate": valid / len(rows), "invalid_outputs": invalid,
        "exact_record_accuracy": exact / len(rows), "findings_micro": prf(**totals),
        "findings_macro_f1": statistics.mean(present) if present else 0.0,
        "findings_per_class": per_class, "field_accuracy": {k: fields[k] / len(rows) for k in fields.keys() | {"user", "source_ip", "connection_outcome", "server_hostname"}},
        "canonical_evidence": prf(**dict(evidence)),
        "supported_citation_rate": supported_citations / cited if cited else 0.0,
        "unsupported_finding_rate": unsupported / len(rows),
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "latency_p95_s": sorted(latencies)[min(len(latencies)-1, int(len(latencies)*0.95))] if latencies else None,
        "reference_note": "Agreement with observable-event annotations; independent expert ground truth is not claimed.",
    }
    if include_content_diagnostic:
        normalized = []
        fenced = 0
        for prediction in predictions:
            match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", prediction["raw"].strip(), flags=re.S | re.I)
            fenced += match is not None
            normalized.append({**prediction, "raw": match.group(1) if match else prediction["raw"]})
        content, _ = score_predictions(rows, normalized, include_content_diagnostic=False)
        metrics["markdown_fenced_outputs"] = fenced
        metrics["fence_normalized_content"] = {key: content[key] for key in (
            "schema_valid_rate", "exact_record_accuracy", "findings_micro", "field_accuracy", "canonical_evidence")}
        metrics["content_diagnostic_note"] = "Secondary diagnostic removes only exact outer Markdown JSON fences. Primary strict metrics still count those responses as failures. Defined on validation before final test."
    return metrics, details


def evaluate_rules(root="data", split="test", output="reports/rules", dataset_folder=None):
    folder = Path(dataset_folder) if dataset_folder else Path(root) / "processed"
    rows = read_jsonl(folder / f"{split}.jsonl")
    predictions = []
    for row in rows:
        start = time.perf_counter()
        raw = json.dumps(annotate(row["lines"]))
        predictions.append({"id": row["id"], "raw": raw, "latency_s": time.perf_counter()-start})
    metrics, details = score_predictions(rows, predictions)
    metrics.update(system="rules", split=split, peak_gpu_memory_mib=0,
                   circular_baseline_note="The annotation rules also define this baseline. Perfect agreement is expected and is not evidence of real-world perfection.")
    if (folder / "manifest.json").exists():
        metrics["dataset_sha256"] = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["dataset_sha256"]
    write_json(str(output) + ".json", metrics)
    write_jsonl(str(output) + "_predictions.jsonl", predictions)
    write_jsonl(str(output) + "_failures.jsonl", [d for d in details if not d["exact_match"]])
    return metrics
