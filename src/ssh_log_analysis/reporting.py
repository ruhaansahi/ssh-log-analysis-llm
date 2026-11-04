"""Write results from measured files, without inventing missing measurements."""

import json
import math
from collections import Counter
from pathlib import Path
import statistics

from .storage import read_jsonl, write_json


def make_report():
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    systems = {}
    for name in ("rules", "original", "adapter"):
        path = report_dir / f"{name}_test.json"
        if path.exists():
            systems[name] = json.loads(path.read_text(encoding="utf-8"))
    lines = ["# Measured SSH Incident Extraction Results", "",
             "This benchmark measures agreement with observable-event annotations. The rule baseline also generates those annotations, so its perfect agreement is expected. Independent expert ground truth and unseen-template generalization are not claimed.", "",
             "| System | Incidents | Strict finding F1 | F1 after fence removal | Exact record | Valid schema | Canonical evidence F1 | Median seconds | Peak GPU MiB |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, result in systems.items():
        content = result.get("fence_normalized_content", {}).get("findings_micro", {}).get("f1")
        content_text = f"{content:.3f}" if content is not None else "not computed"
        lines.append(f"| {name} | {result['incidents']} | {result['findings_micro']['f1']:.3f} | {content_text} | "
                     f"{result['exact_record_accuracy']:.3f} | {result['schema_valid_rate']:.3f} | "
                     f"{result['canonical_evidence']['f1']:.3f} | {result['latency_median_s']:.4f} | "
                     f"{result['peak_gpu_memory_mib']:.0f} |")
    if len(systems) != 3:
        lines += ["", "Status: incomplete. Missing systems have not been measured."]
    else:
        delta = systems["adapter"]["findings_micro"]["f1"] - systems["original"]["findings_micro"]["f1"]
        exact_delta = systems["adapter"]["exact_record_accuracy"] - systems["original"]["exact_record_accuracy"]
        lines += ["", f"Adapter minus original finding F1: {delta:+.3f}. Exact-record accuracy change: {exact_delta:+.3f}.",
                  "", "Training is useful only to the extent these results and the error analysis justify it. The deterministic parser remains a serious alternative for this constrained task."]
    if systems:
        lines += ["", "## Extraction, evidence, and output reliability", "",
                  "| System | User accuracy | IP accuracy | Outcome accuracy | Host accuracy | Evidence precision | Evidence recall | Invalid output rate | Unsupported finding rate |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for name, result in systems.items():
            fields, evidence = result["field_accuracy"], result["canonical_evidence"]
            lines.append(f"| {name} | {fields['user']:.3f} | {fields['source_ip']:.3f} | "
                         f"{fields['connection_outcome']:.3f} | {fields['server_hostname']:.3f} | "
                         f"{evidence['precision']:.3f} | {evidence['recall']:.3f} | "
                         f"{result['invalid_outputs']/result['incidents']:.3f} | {result['unsupported_finding_rate']:.3f} |")
        lines += ["", "Unsupported finding rate counts schema-valid responses with extra findings, divided by all incidents. Invalid responses fail the primary metrics and are reported separately; a low unsupported rate alone does not establish reliable output.",
                  "", "| System | Median seconds | p95 seconds | Peak allocated MiB | Peak reserved MiB |",
                  "|---|---:|---:|---:|---:|"]
        for name, result in systems.items():
            lines.append(f"| {name} | {result['latency_median_s']:.4f} | {result['latency_p95_s']:.4f} | "
                         f"{result['peak_gpu_memory_mib']:.0f} | {result.get('peak_reserved_memory_mib', 0):.0f} |")
    if len(systems) == 3 and (report_dir / "latency_protocol.json").exists():
        paired = paired_latency(report_dir)
        if paired:
            lines += ["", "### Matched AC-power latency check", "",
                      "The full original-model test began on battery and continued on AC power. Its full-test timing is observational. For a fairer comparison, the following paired window was fixed after AC connection and before these incidents were processed: the last 20 test incidents, with batch size 1 for both systems.",
                      "", "| System | Matched incidents | Median seconds | p95 seconds | Mean generated tokens |",
                      "|---|---:|---:|---:|---:|"]
            for name, result in paired["systems"].items():
                lines.append(f"| {name} | {paired['incidents']} | {result['median_s']:.4f} | "
                             f"{result['p95_s']:.4f} | {result['mean_generated_tokens']:.1f} |")
            lines += ["", "Response lengths and formatting can affect latency. This small, paired device measurement is not a production throughput benchmark."]
    lines += ["", "## Training-run comparison", "",
              "| Rank | Trainable parameters | Percent trainable | Minutes | Peak allocated / reserved MiB | Validation loss | Validation exact record | Validation finding F1 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    histories, training_records = [], {}
    for rank in (16, 8):
        training_path = Path(f"artifacts/rank-{rank}/training.json")
        validation_path = report_dir / f"rank_{rank}_validation.json"
        if not training_path.exists() or not validation_path.exists():
            continue
        training = json.loads(training_path.read_text(encoding="utf-8"))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        training_records[str(rank)] = training
        loss = training["best_validation_loss"]
        lines.append(f"| {rank} | {training['trainable_parameters']:,} | "
                     f"{100*training['trainable_parameters']/training['total_parameters']:.2f}% | "
                     f"{training['training_seconds']/60:.2f} | {training['peak_gpu_memory_mib']:.0f} / {training['peak_reserved_memory_mib']:.0f} | "
                     f"{loss:.5f} | {validation['exact_record_accuracy']:.3f} | {validation['findings_micro']['f1']:.3f} |")
        histories.append((rank, json.loads(training_path.with_name("history.json").read_text(encoding="utf-8"))))
    if training_records:
        write_json(report_dir / "training_runs.json", training_records)
        write_json(report_dir / "training_histories.json", {str(rank): history for rank, history in histories})
    selection_path = report_dir / "selection.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        lines += ["", f"Selected rank: **{selection['rank']}**, using {selection['criterion']}. Final test scores did not select this configuration."]
    if histories:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7.5, 4.2), layout="constrained")
        for rank, history in histories:
            points = [(entry["step"], entry["loss"]) for entry in history if "loss" in entry and entry["loss"] > 0]
            if points:
                ax.plot(*zip(*points), marker=".", label=f"LoRA rank {rank}")
        ax.set(xlabel="Optimizer updates", ylabel="Response-token cross-entropy (log scale)",
               title="Measured QLoRA training loss")
        ax.set_yscale("log")
        ax.grid(alpha=0.2)
        ax.legend()
        fig.savefig(report_dir / "training_loss.png", dpi=160)
        plt.close(fig)
        lines += ["", "![Measured training curves](training_loss.png)", "",
                  "Training loss alone does not establish generalization; use the held-out comparison above. Trainer logs round loss values; entries recorded as zero are omitted from this logarithmic plot."]
    active = Path("data/active.json")
    if active.exists():
        manifest = json.loads(active.read_text(encoding="utf-8"))
        lines += ["", "## Dataset", "", f"Model: `{manifest['model']}`. Maximum combined training length: {manifest['max_length']} tokens.",
                  f"Partition counts after length filtering: `{manifest['counts']}`.",
                  f"Dataset SHA-256: `{manifest['dataset_sha256']}`.",
                  "", "Long incidents are excluded rather than silently truncated. Balanced sampling changes the natural event frequencies; these are not production-prevalence estimates."]
        profile = {}
        for split in ("train", "validation", "test"):
            rows = read_jsonl(Path(manifest["folder"]) / f"{split}.jsonl")
            profile[split] = {"originals": sum(not row["augmented"] for row in rows),
                              "augmented": sum(row["augmented"] for row in rows),
                              "outcomes": dict(Counter(row["answer"]["connection_outcome"] for row in rows)),
                              "findings": dict(Counter(f for row in rows for f in row["answer"]["findings"]))}
        summary = {key: manifest[key] for key in ("model", "max_length", "counts", "partition_hashes", "dataset_sha256")}
        summary["partition_profile"] = profile
        write_json(report_dir / "dataset_summary.json", summary)
        lines += ["", "| Partition | Original incidents | Identity variations | Success | Failure | Unknown |",
                  "|---|---:|---:|---:|---:|---:|"]
        for split, values in profile.items():
            outcomes = values["outcomes"]
            lines.append(f"| {split} | {values['originals']} | {values['augmented']} | "
                         f"{outcomes.get('success', 0)} | {outcomes.get('failure', 0)} | {outcomes.get('unknown', 0)} |")
        lines += ["", "| Finding | Training incidents | Validation incidents | Test incidents |",
                  "|---|---:|---:|---:|"]
        from .schema import FINDINGS
        for finding in FINDINGS:
            counts = [profile[split]["findings"].get(finding, 0) for split in ("train", "validation", "test")]
            lines.append(f"| {finding} | {counts[0]} | {counts[1]} | {counts[2]} |")
    lines += ["", "## Metric interpretation", "",
              "Canonical evidence F1 requires the reference line IDs. An alternative supporting line can be substantively correct but lose canonical-match credit; supported-citation rate is also recorded in the JSON metrics.",
              "Malformed or schema-invalid output is a failed incident. Missing predictions are failures. Null-field accuracy includes correctly absent values.",
              "A secondary content diagnostic removes only exact outer Markdown fences, without extracting objects from prose or repairing JSON. It separates formatting compliance from extraction quality; its rule was fixed using validation before the final test.",
              "Peak memory is PyTorch allocated memory, not total GPU process memory. Timing excludes model loading and a fixed synthetic warm-up; inference is sequential with batch size 1.",
              "Reserved memory includes allocator cache and can be substantially larger than allocated tensors. Training time includes the epoch-end validation and adapter save; inference runs and initial model loading are outside that measurement.",
              "P95 latency uses the nearest-rank percentile.",
              "", "## Limits", "",
              "- One historical lab source, small test set, shared event templates, and rule-derived references limit generalization claims.",
              "- Host/PID/idle-gap sessionization is approximate. Orphaned lines can occur after disconnect events.",
              "- Findings describe observable events; attack intent and hidden root causes are outside the label taxonomy.",
              "- Final test results are not used to select prompts, hyperparameters, or checkpoints."]
    (report_dir / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"complete": len(systems) == 3, "report": "reports/RESULTS.md", "systems": list(systems)}


def paired_latency(report_dir):
    protocol = json.loads((report_dir / "latency_protocol.json").read_text(encoding="utf-8"))
    destination = report_dir / "latency_comparison.json"
    paths = {name: report_dir / f"{name}_test_predictions.jsonl" for name in ("original", "adapter")}
    if not all(path.exists() for path in paths.values()):
        return json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else None
    systems = {}
    for name, path in paths.items():
        indexed = {row["id"]: row for row in read_jsonl(path)}
        selected = [indexed[incident_id] for incident_id in protocol["incident_ids"]]
        latencies = sorted(row["latency_s"] for row in selected)
        systems[name] = {"median_s": statistics.median(latencies),
                         "p95_s": latencies[math.ceil(len(latencies)*0.95)-1],
                         "mean_generated_tokens": statistics.mean(row["generated_tokens"] for row in selected)}
    result = {"incidents": protocol["incidents"], "dataset_sha256": protocol["dataset_sha256"],
              "incident_ids": protocol["incident_ids"], "protocol": "latency_protocol.json", "systems": systems}
    write_json(destination, result)
    return result
