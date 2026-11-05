"""Separate cohort reports and tracking for additional held-out incidents."""

from pathlib import Path

from .schema import FINDINGS
from .storage import digest, write_json


def report_extension(result):
    total = result["total"]
    paths = {"report": Path(f"reports/EXTENDED_TEST_{total}.md"),
             "metrics": Path(f"reports/extended_{total}_metrics.json")}
    write_json(paths["metrics"], result)
    cohorts = result["cohorts"]
    lines = [f"# Extended SSH Test: {total} Incidents", "",
             "This is a same-source extension evaluated with the unchanged original model, selected rank-8 adapter, and parser. References use the existing observable-event rubric and assisted source review. Independent human or expert validation is not claimed.", "",
             "The original 93-case artifacts remain unchanged. Additional cases were selected and reviewed before generating predictions. This report separates the historical cohort, the new cohort, and their combined scores.", "",
             "| Cohort | System | Incidents | Strict finding F1 | Fence-normalized F1 | Exact records | Schema valid | Evidence F1 | Malformed JSON | Invalid outputs | Unsupported finding rate |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for cohort, data in cohorts.items():
        n = data["profile"]["incidents"]
        for system, metrics in data["systems"].items():
            lines.append(f"| {cohort} | {system} | {n} | {metrics['findings_micro']['f1']:.3f} | "
                         f"{metrics['fence_normalized_content']['findings_micro']['f1']:.3f} | "
                         f"{round(metrics['exact_record_accuracy']*n)}/{n} | {round(metrics['schema_valid_rate']*n)}/{n} | "
                         f"{metrics['canonical_evidence']['f1']:.3f} | {round((1-metrics['valid_json_rate'])*n)} | "
                         f"{metrics['invalid_outputs']} | {metrics['unsupported_finding_rate']:.3f} |")
    lines += ["", "## Field and evidence accuracy", "",
              "| Cohort | System | User | Source IP | Outcome | Hostname | Evidence precision | Evidence recall |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for cohort, data in cohorts.items():
        for system, metrics in data["systems"].items():
            fields, evidence = metrics["field_accuracy"], metrics["canonical_evidence"]
            lines.append(f"| {cohort} | {system} | {fields['user']:.3f} | {fields['source_ip']:.3f} | "
                         f"{fields['connection_outcome']:.3f} | {fields['server_hostname']:.3f} | "
                         f"{evidence['precision']:.3f} | {evidence['recall']:.3f} |")
    adapter = cohorts["additional"]["systems"]["adapter"]
    n = cohorts["additional"]["profile"]["incidents"]
    errors = {field: round((1-accuracy)*n) for field, accuracy in adapter["field_accuracy"].items()}
    lines += ["", f"The additional adapter cohort has {round((1-adapter['exact_record_accuracy'])*n)} non-exact records. "
              f"Field errors: user {errors['user']}, source IP {errors['source_ip']}, "
              f"outcome {errors['connection_outcome']}, and hostname {errors['server_hostname']}. "
              "Finding F1 alone does not measure correct identity extraction. "
              "See the [error analysis](../docs/METHODOLOGY.md#error-analysis-and-limits) for interpretation; all failures remain in scoring."]
    lines += ["", "## Coverage and interpretation", "",
              "| Finding | Original positives | Additional positives | Combined positives |", "|---|---:|---:|---:|"]
    for finding in FINDINGS:
        counts = [cohorts[c]["profile"]["findings"].get(finding, 0) for c in ("original", "additional", "combined")]
        lines.append(f"| {finding} | {counts[0]} | {counts[1]} | {counts[2]} |")
    lines += ["", "| Cohort | Success | Failure | Unknown |", "|---|---:|---:|---:|"]
    for cohort, data in cohorts.items():
        counts = data["profile"]["outcomes"]
        lines.append(f"| {cohort} | {counts.get('success', 0)} | {counts.get('failure', 0)} | {counts.get('unknown', 0)} |")
    inventory, review = result["inventory"], result["review"]
    lines += ["", f"Candidate inventory: {inventory['unused_reserved_candidates']} unused reserved families, "
              f"{inventory['excluded_by_token_length']} excluded by token length, and {inventory['eligible']} eligible. "
              f"All {review['reviewed_incidents']} selected incidents received assisted source review; "
              f"{review['corrected_references']} references were corrected and {review['replacement_count']} length replacements were needed. "
              "Automated schema and evidence-reference validation passed separately from source inspection."]
    lines += ["", "The additional cohort has no accepted-authentication positives; its size must not be presented as additional evidence about successful authentication. Class supports describe coverage, and zero-support classes are not evidence of successful detection. False positives still affect the overall metrics.",
              "", "The original class sampler and seed 42 were reused after filtering unused reserved test families. This is a coverage-oriented sample, not an estimate of production event frequencies. Shared templates and identities remain possible. The parser also produced draft references; high parser agreement is expected and does not establish independent correctness.",
              "", "Strict scores count malformed or schema-invalid output as failed incidents. The existing secondary diagnostic removes only exact outer Markdown fences. Canonical evidence scores require the reviewed line IDs; an alternative supporting line can lose canonical-match credit. Unsupported finding rate counts schema-valid responses with extra findings, with invalid responses reported separately.",
              "", "## New evaluation sessions", "",
              "Timing is reported per new execution session only. Original and additional timing or memory measurements are not pooled into a model-speed comparison. Loading, warm-up, and checkpoint writes are outside the recorded generation latency. GPU peaks cover warm-up and generation after model loading, rather than load-time transients. Reserved GPU memory includes allocator cache; allocated memory is not total process/device usage.",
              "", "| System | Session | Completed incidents | Status | Median seconds | p95 seconds | Peak allocated MiB | Peak reserved MiB | AC at start/end |",
              "|---|---|---:|---|---:|---:|---:|---:|---|"]
    for system, sessions in result["sessions"].items():
        for index, session in enumerate(sessions, 1):
            resources = session.get("resources") or {}
            median, p95 = session["latency_median_s"], session["latency_p95_s"]
            times = (f"{median:.4f}", f"{p95:.4f}") if median is not None else ("n/a", "n/a")
            memory = [f"{resources[key]:.0f}" if key in resources else "n/a"
                      for key in ("peak_gpu_memory_mib", "peak_reserved_memory_mib")]
            lines.append(f"| {system} | {index} | {session['completed']} | {session['status']} | {times[0]} | {times[1]} | "
                         f"{memory[0]} | {memory[1]} | "
                         f"{session['power_start']['ac_connected']}/{session.get('power_end', {}).get('ac_connected', 'unknown')} |")
    lines += ["", "## Reproducibility", "",
              f"Original experiment SHA-256: `{result['original_dataset_sha256']}`.", "",
              f"Reviewed extension SHA-256: `{result['extension_sha256']}`.", "",
              f"Combined test SHA-256: `{result['combined_test_sha256']}`.", "",
              f"Original file preservation: {result['preservation']['checked_files']} files verified unchanged, including original datasets, predictions, and adapter artifacts.",
              "", f"Full aggregate metrics, session metadata, and frozen inference settings: [extended_{total}_metrics.json](extended_{total}_metrics.json).",
              "", "Raw source logs, reviewed annotations, incremental prediction caches, and failure records remain local under the ignored data directory. No model training or selection was performed for this extension."]
    paths["report"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths


def track_extension(result, paths, training_run_id):
    from .modeling import configure_cache
    from .tracking import numeric_metrics
    from .extension import read_json
    configure_cache()
    import mlflow
    tracking_path = Path(f"reports/extended_{result['total']}_tracking.json")
    fingerprint = digest(result)
    saved = read_json(tracking_path) if tracking_path.exists() else None
    mlflow.set_experiment("ssh-log-qlora")
    with mlflow.start_run(run_id=saved["run_id"] if saved and saved["evaluation_sha256"] == fingerprint else None,
                          run_name=f"same-source-test-{result['total']}") as run:
        mlflow.log_params({"original_dataset_sha256": result["original_dataset_sha256"],
                           "extension_sha256": result["extension_sha256"], "combined_test_sha256": result["combined_test_sha256"],
                           "test_incidents": result["total"], "additional_incidents": result["cohorts"]["additional"]["profile"]["incidents"],
                           "training_run_id": training_run_id, "model": result["protocols"]["adapter"]["model"],
                           "model_revision": result["protocols"]["adapter"]["model_revision"], "batch_size": 1,
                           "max_new_tokens": 256, "retraining": False})
        mlflow.set_tags({"evaluation_kind": "same-source held-out extension", "reference_review": "assisted source review; not independent human validation",
                         "selected_training_run_id": training_run_id})
        for cohort, data in result["cohorts"].items():
            for system, metrics in data["systems"].items():
                mlflow.log_metrics(numeric_metrics(metrics, f"{cohort}.{system}"))
        for system, sessions in result["sessions"].items():
            for index, session in enumerate(sessions, 1):
                values = {key: session[key] for key in ("completed", "latency_median_s", "latency_p95_s")}
                values["resources"] = session.get("resources") or {}
                mlflow.log_metrics(numeric_metrics(values, f"new_session.{system}.{index}"))
        for path in paths.values():
            mlflow.log_artifact(str(path))
        summary = {"run_id": run.info.run_id, "training_run_id": training_run_id, "evaluation_sha256": fingerprint,
                   "extension_sha256": result["extension_sha256"], "combined_test_sha256": result["combined_test_sha256"]}
        write_json(tracking_path, summary)
        mlflow.log_artifact(str(tracking_path))
    return summary
