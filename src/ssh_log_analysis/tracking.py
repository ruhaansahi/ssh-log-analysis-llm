"""Attach the complete held-out comparison to local MLflow training runs."""

import json
import math
from pathlib import Path
import subprocess

from .storage import digest, write_json


def numeric_metrics(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(numeric_metrics(item, name))
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            result[name] = float(item)
    return result


def log_report_artifacts(mlflow, reports, summary_path):
    paired_path = reports / "latency_comparison.json"
    if paired_path.exists():
        paired = json.loads(paired_path.read_text(encoding="utf-8"))
        mlflow.log_metrics(numeric_metrics(paired["systems"], "matched_ac_latency"))
    for path in sorted(reports.iterdir()):
        if path.suffix in (".json", ".md", ".png") and not path.name.startswith("audit") and path != summary_path:
            mlflow.log_artifact(str(path), artifact_path="reports")


def log_comparison():
    from .modeling import configure_cache
    configure_cache()
    import mlflow

    reports = Path("reports")
    systems = {name: json.loads((reports / f"{name}_test.json").read_text(encoding="utf-8"))
               for name in ("rules", "original", "adapter")}
    selection = json.loads((reports / "selection.json").read_text(encoding="utf-8"))
    versions = {r["dataset_sha256"] for r in systems.values()} | {selection["dataset_sha256"]}
    if len(versions) != 1:
        raise ValueError("Cannot track a comparison across different dataset versions")
    summary_path = reports / "mlflow_summary.json"
    fingerprint = digest(systems)
    if summary_path.exists():
        saved = json.loads(summary_path.read_text(encoding="utf-8"))
        if saved.get("metrics_sha256") == fingerprint:
            with mlflow.start_run(run_id=saved["comparison_run_id"]):
                log_report_artifacts(mlflow, reports, summary_path)
            return saved

    mlflow.set_experiment("ssh-log-qlora")
    training_runs = {}
    for rank in selection["candidate_ranks"]:
        training_path = Path(f"artifacts/rank-{rank}/training.json")
        training = json.loads(training_path.read_text(encoding="utf-8"))
        validation_path = reports / f"rank_{rank}_validation.json"
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if training["dataset_sha256"] not in versions or validation["dataset_sha256"] not in versions:
            raise ValueError("Training or validation dataset version differs from the final comparison")
        training_runs[str(rank)] = training["mlflow_run_id"]
        with mlflow.start_run(run_id=training["mlflow_run_id"]):
            mlflow.set_tag("selected_on_validation", rank == selection["rank"])
            mlflow.log_metrics(numeric_metrics(validation, "validation"))
            mlflow.log_artifact(str(validation_path), artifact_path="evaluation")
            if rank == selection["rank"]:
                mlflow.log_metrics(numeric_metrics(systems["adapter"], "test"))
                mlflow.log_artifact(str(reports / "adapter_test.json"), artifact_path="evaluation")

    with mlflow.start_run(run_name="held-out-comparison") as run:
        mlflow.log_params({"dataset_sha256": selection["dataset_sha256"],
                           "model": systems["original"]["model"],
                           "model_revision": systems["original"]["model_revision"],
                           "selected_rank": selection["rank"],
                           "test_incidents": systems["original"]["incidents"],
                           "test_batch_size": systems["original"]["inference_batch_size"],
                           "original_and_adapter_prompt": "identical; non-thinking; deterministic",
                           "quantization": "NF4 with double quantization"})
        status = subprocess.run(["git", "status", "--porcelain", "--", "src", "configs", "pyproject.toml", "uv.lock"],
                                capture_output=True, text=True, check=False)
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        mlflow.set_tags({"reference_labels": "observable-event rules; assisted source audit; not independent expert ground truth",
                         "implementation_uncommitted": bool(status.stdout.strip()) if status.returncode == 0 else "unknown",
                         "git_head": head.stdout.strip() if head.returncode == 0 else "unknown",
                         "training_run_ids": json.dumps(training_runs)})
        for name, metrics in systems.items():
            mlflow.log_metrics(numeric_metrics(metrics, name))
        log_report_artifacts(mlflow, reports, summary_path)
        summary = {"comparison_run_id": run.info.run_id, "training_run_ids": training_runs,
                   "dataset_sha256": selection["dataset_sha256"], "metrics_sha256": fingerprint,
                   "tracking_storage": "local ./mlruns", "selected_rank": selection["rank"]}
        write_json(summary_path, summary)
        mlflow.log_artifact(str(summary_path))
    return summary
