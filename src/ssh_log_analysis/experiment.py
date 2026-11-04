"""Resume-safe experiment orchestration; final test is not a tuning signal."""

import json
from pathlib import Path

from .storage import read_jsonl, write_json


def run_experiment(config):
    from .evaluation import evaluate_rules
    from .modeling import active_data, evaluate_model, filter_data, train
    from .reporting import make_report
    active = Path("data/active.json")
    if not active.exists():
        filter_data(config["model"], config["max_length"])
    folder, manifest = active_data()
    prepared_manifest = json.loads(Path("data/processed/manifest.json").read_text(encoding="utf-8"))
    if manifest.get("source_manifest", {}).get("dataset_sha256") != prepared_manifest["dataset_sha256"]:
        raise ValueError("Prepared data changed; rerun filter-data before continuing")
    if (manifest["model"], manifest["max_length"]) != (config["model"], config["max_length"]):
        raise ValueError("Active data differs from configuration. Run filter-data explicitly before restarting.")
    audit = Path("reports/audit.json")
    if not audit.exists():
        raise ValueError("Export and review the test annotation audit before the final experiment")
    reviewed = {row["id"]: row for row in json.loads(audit.read_text(encoding="utf-8"))}
    for row in read_jsonl(folder / "test.jsonl"):
        entry = reviewed.get(row["id"], {})
        if entry.get("review_status") != "rubric_review_completed" or entry.get("answer") != row["answer"]:
            raise ValueError(f"Missing or stale test annotation review: {row['id']}")
    def measured(path, callback, expected=None):
        path = Path(path)
        if path.exists():
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("dataset_sha256") != manifest["dataset_sha256"]:
                raise ValueError(f"Stale measurement: {path}")
            if any(result.get(key) != value for key, value in (expected or {}).items()):
                raise ValueError(f"Measurement model, adapter, or split differs: {path}")
            return result
        return callback()
    measured("reports/original_validation.json", lambda: evaluate_model(split="validation", output="reports/original_validation"),
             {"model": config["model"], "adapter": None, "split": "validation"})
    runs = []
    for rank in (config["rank"], 8):
        if runs and (rank == config["rank"] or runs[0]["training"]["training_seconds"] * 2 > 3*3600):
            break
        run_config = {**config, "rank": rank}
        training_path = Path(f"artifacts/rank-{rank}/training.json")
        if training_path.exists():
            training = json.loads(training_path.read_text(encoding="utf-8"))
            if training["config"] != run_config or training["dataset_sha256"] != manifest["dataset_sha256"]:
                raise ValueError(f"Stale training artifact: {training_path}")
        else:
            training = train(run_config)
        validation = measured(f"reports/rank_{rank}_validation.json", lambda: evaluate_model(
            split="validation", adapter=training["adapter"], output=f"reports/rank_{rank}_validation"),
            {"model": config["model"], "adapter": training["adapter"], "split": "validation"})
        runs.append({"rank": rank, "training": training, "validation": validation})
    selected = max(runs, key=lambda r: (r["validation"]["exact_record_accuracy"],
                                       r["validation"]["findings_micro"]["f1"],
                                       r["validation"]["schema_valid_rate"],
                                       -(r["training"]["best_validation_loss"] if r["training"]["best_validation_loss"] is not None else float("inf"))))
    selection = {"rank": selected["rank"], "adapter": selected["training"]["adapter"],
                 "dataset_sha256": manifest["dataset_sha256"],
                 "criterion": "validation exact-record accuracy, then finding F1, schema validity, validation loss",
                 "candidate_ranks": [r["rank"] for r in runs]}
    write_json("reports/selection.json", selection)
    # The baseline shares the annotation rules. That dependency is reported.
    rules = evaluate_rules(dataset_folder=folder, output="reports/rules_test")
    rules["dataset_sha256"] = manifest["dataset_sha256"]
    write_json("reports/rules_test.json", rules)
    measured("reports/original_test.json", lambda: evaluate_model(output="reports/original_test"),
             {"model": config["model"], "adapter": None, "split": "test"})
    measured("reports/adapter_test.json", lambda: evaluate_model(adapter=selection["adapter"], output="reports/adapter_test"),
             {"model": config["model"], "adapter": selection["adapter"], "split": "test"})
    report = make_report()
    from .tracking import log_comparison
    report["tracking"] = log_comparison()
    return report
