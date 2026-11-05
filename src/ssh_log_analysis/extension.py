"""Reviewed same-source test extension, preserving the original experiment."""

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time
import uuid

from .data import balance, family_id, make_record, sessionize
from .evaluation import score_predictions
from .modeling import MODEL_REVISIONS, active_data, infer_rows, pair_for, tokenizer_for
from .schema import annotate, validate_answer
from .storage import digest, read_jsonl, write_jsonl


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    # Windows sync/indexing processes can briefly lock a recently replaced file.
    for attempt in range(7):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(0.05 * 2**attempt)


def now():
    return datetime.now(timezone.utc).isoformat()


def base_data(root):
    folder, manifest = active_data(root)
    for split, expected in manifest["partition_hashes"].items():
        if digest(read_jsonl(folder / f"{split}.jsonl")) != expected:
            raise ValueError(f"Original {split} data changed")
    return folder, manifest, read_jsonl(folder / "test.jsonl")


def preservation_snapshot(root, folder):
    paths = [Path(root) / "active.json", Path(root) / "raw/source.json", Path(root) / "raw/OpenSSH.log",
             Path(root) / "processed/manifest.json", folder / "manifest.json", Path("reports/selection.json")]
    for split in ("train", "validation", "test"):
        paths.extend([Path(root) / "processed" / f"{split}.jsonl", folder / f"{split}.jsonl"])
    for system in ("rules", "original", "adapter"):
        paths.extend(Path("reports") / f"{system}_test{suffix}" for suffix in (".json", "_predictions.jsonl", "_failures.jsonl"))
    for rank in (8, 16):
        paths.extend(Path(f"artifacts/rank-{rank}") / name for name in
                     ("training.json", "history.json", "adapter/adapter_config.json", "adapter/adapter_model.safetensors"))
    return {str(path): file_hash(path) for path in paths}


def assert_preserved(manifest):
    for path, expected in manifest["original_files"].items():
        if not Path(path).is_file() or file_hash(path) != expected:
            raise ValueError(f"Original experiment file changed: {path}")


def eligible_candidates(text, used_families, length, max_length=1024):
    sessions, skipped = sessionize(text)
    seen, eligible = set(), []
    candidates = excluded_length = 0
    for lines in sessions:
        if len(lines) > 32:
            continue
        family = family_id(lines)
        if family in seen:
            continue
        seen.add(family)
        if family in used_families or int(digest("split42:" + family)[:8], 16) % 100 >= 12:
            continue
        candidates += 1
        row = make_record(lines)
        if length(row) > max_length:
            excluded_length += 1
        else:
            eligible.append(row)
    return eligible, {"unused_reserved_candidates": candidates, "eligible": len(eligible),
                      "excluded_by_token_length": excluded_length, "unparsed_source_lines": skipped}


def token_length(tokenizer, row):
    pair = pair_for(tokenizer, row)
    return len(tokenizer(pair["prompt"] + pair["completion"], add_special_tokens=False)["input_ids"])


def audit_entry(row):
    return {"id": row["id"], "input_sha256": digest(row["lines"]), "draft_answer": row["answer"],
            "answer": row["answer"], "review_status": "pending", "review_method": "",
            "review_notes": "", "independent_human_review": False}


def prepare_extension(total=200, root="data"):
    original_folder, original_manifest, original = base_data(root)
    if total <= len(original):
        raise ValueError("Total must exceed the original test size")
    folder = Path(root) / "test_extensions" / f"total-{total}"
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        assert_preserved(manifest)
        if manifest["total"] != total or manifest["original_dataset_sha256"] != original_manifest["dataset_sha256"]:
            raise ValueError("Existing extension belongs to another experiment")
        audit = read_json(folder / "audit.json")
        return {"folder": str(folder), "original": len(original), "additional": len(audit), "total": total,
                "pending_review": sum(a["review_status"] != "rubric_review_completed" for a in audit), "reused": True}
    source = Path(root) / "raw/OpenSSH.log"
    source_meta = read_json(Path(root) / "raw/source.json")
    if file_hash(source) != source_meta["log_sha256"]:
        raise ValueError("Raw source checksum differs from the original download")
    used = {row["family_id"] for split in ("train", "validation", "test")
            for row in read_jsonl(Path(root) / "processed" / f"{split}.jsonl")}
    tokenizer = tokenizer_for(original_manifest["model"])
    eligible, inventory = eligible_candidates(source.read_text(encoding="utf-8"), used,
                                             lambda row: token_length(tokenizer, row), original_manifest["max_length"])
    selected = balance(eligible, total-len(original), 42)
    if len(selected) != total-len(original):
        raise ValueError("Not enough unused length-valid test families")
    selected_ids = {row["id"] for row in selected}
    reserve = [row for row in balance(eligible, len(eligible), 42) if row["id"] not in selected_ids]
    manifest = {"version": 1, "created_at_utc": now(), "total": total, "original_count": len(original),
                "additional_count": len(selected), "seed": 42, "model": original_manifest["model"],
                "model_revision": MODEL_REVISIONS[original_manifest["model"]],
                "max_length": original_manifest["max_length"], "inventory": inventory,
                "original_dataset_sha256": original_manifest["dataset_sha256"],
                "selected_inputs_sha256": digest([{ "id": r["id"], "lines": r["lines"]} for r in selected]),
                "selected_ids": [r["id"] for r in selected], "original_files": preservation_snapshot(root, original_folder),
                "selection_note": "Original reserved test partition; unused families; length filtering; existing category sampler and seed 42",
                "review_note": "Rule-derived drafts require assisted source review before predictions; not independent expert ground truth",
                "replacement_history": []}
    write_jsonl(folder / "draft.jsonl", selected)
    write_jsonl(folder / "reserve.jsonl", reserve)
    atomic_json(folder / "audit.json", [audit_entry(row) for row in selected])
    atomic_json(manifest_path, manifest)
    return {"folder": str(folder), "original": len(original), "additional": len(selected), "total": total,
            "inventory": inventory, "pending_review": len(selected), "reused": False}


def reviewed_rows(draft, audit):
    if len(audit) != len(draft) or len({a["id"] for a in audit}) != len(audit):
        raise ValueError("Duplicate or missing audit IDs")
    indexed = {a["id"]: a for a in audit}
    result = []
    for row in draft:
        entry = indexed.get(row["id"], {})
        if entry.get("review_status") != "rubric_review_completed" or not entry.get("review_method") or not entry.get("review_notes"):
            raise ValueError(f"Missing completed source review: {row['id']}")
        if entry.get("input_sha256") != digest(row["lines"]):
            raise ValueError("Stale audit source")
        errors = validate_answer(entry["answer"], [line["id"] for line in row["lines"]])
        if errors:
            raise ValueError(f"Invalid reviewed reference {row['id']}: {errors}")
        result.append({**row, "answer": entry["answer"], "provenance": "observable_event_rules_v2_with_assisted_source_review"})
    return result


def freeze_extension(total=200, root="data"):
    folder = Path(root) / "test_extensions" / f"total-{total}"
    manifest = read_json(folder / "manifest.json")
    assert_preserved(manifest)
    _, source_manifest, original = base_data(root)
    draft, audit = read_jsonl(folder / "draft.jsonl"), read_json(folder / "audit.json")
    inputs = [{"id": r["id"], "lines": r["lines"]} for r in draft]
    if digest(inputs) != manifest["selected_inputs_sha256"] or [r["id"] for r in draft] != manifest["selected_ids"]:
        raise ValueError("Frozen source selection changed")
    rows = reviewed_rows(draft, audit)
    frozen_path = folder / "frozen.json"
    if frozen_path.exists():
        frozen = read_json(frozen_path)
        if frozen["extension_sha256"] != digest(rows) or frozen["audit_sha256"] != digest(audit):
            raise ValueError("Reviewed references changed after freezing")
        if digest(read_jsonl(folder / "test.jsonl")) != frozen["extension_sha256"]:
            raise ValueError("Frozen extension file changed")
        return folder, manifest, frozen, original, rows
    if list(folder.glob("*_cache.json")):
        raise ValueError("Prediction cache exists without frozen references")
    tokenizer = tokenizer_for(manifest["model"])
    oversized = [i for i, row in enumerate(rows) if token_length(tokenizer, row) > manifest["max_length"]]
    if oversized:
        reserve = read_jsonl(folder / "reserve.jsonl")
        for index in oversized:
            if not reserve:
                raise ValueError("No replacement candidate remains")
            replacement = reserve.pop(0)
            manifest["replacement_history"].append({"old_id": draft[index]["id"], "new_id": replacement["id"],
                                                     "reason": "Reviewed target exceeded token budget before prediction"})
            draft[index], audit[index] = replacement, audit_entry(replacement)
        manifest["selected_ids"] = [r["id"] for r in draft]
        manifest["selected_inputs_sha256"] = digest([{"id": r["id"], "lines": r["lines"]} for r in draft])
        write_jsonl(folder / "draft.jsonl", draft)
        write_jsonl(folder / "reserve.jsonl", reserve)
        atomic_json(folder / "audit.json", audit)
        atomic_json(folder / "manifest.json", manifest)
        raise ValueError("Length-ineligible references replaced; review the new pending incidents before evaluation")
    all_ids = [row["id"] for row in original + rows]
    if len(all_ids) != total or len(set(all_ids)) != total:
        raise ValueError("Incorrect extension count or duplicate incident IDs")
    used = {r["family_id"] for s in ("train", "validation", "test") for r in read_jsonl(Path(root) / "processed" / f"{s}.jsonl")}
    if len({r["family_id"] for r in rows}) != len(rows) or any(r["family_id"] in used or r["augmented"] for r in rows):
        raise ValueError("Family leakage or augmentation in extension")
    frozen = {"frozen_at_utc": now(), "extension_sha256": digest(rows), "combined_test_sha256": digest(original + rows),
              "audit_sha256": digest(audit), "original_dataset_sha256": source_manifest["dataset_sha256"],
              "selected_ids": all_ids, "review_method": "assisted source inspection", "independent_human_validation": False}
    write_jsonl(folder / "test.jsonl", rows)
    atomic_json(frozen_path, frozen)
    return folder, manifest, frozen, original, rows


@contextmanager
def evaluation_lock(folder):
    import psutil
    path = folder / "evaluation.lock"
    owner = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    if path.exists():
        previous = read_json(path)
        try:
            active = psutil.Process(previous["pid"]).create_time() == previous["created"]
        except psutil.NoSuchProcess:
            active = False
        if active:
            raise ValueError("Another evaluation process owns this extension")
        path.unlink()
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(owner, handle)
    try:
        yield
    finally:
        if path.exists() and read_json(path) == owner:
            path.unlink()


def power_observation():
    import psutil
    battery = psutil.sensors_battery()
    return {"ac_connected": battery.power_plugged if battery else None, "battery_percent": battery.percent if battery else None}


def cached_predictions(path, rows, signature, infer):
    path = Path(path)
    state = read_json(path) if path.exists() else {"signature": signature, "predictions": [], "sessions": []}
    if state["signature"] != signature:
        raise ValueError("Stale prediction cache: dataset, model, adapter, prompt, or decoding changed")
    expected = [row["id"] for row in rows]
    ids = [p["id"] for p in state["predictions"]]
    if len(set(ids)) != len(ids) or ids != expected[:len(ids)]:
        raise ValueError("Duplicate, unknown, or out-of-order cached prediction IDs")
    if any(not isinstance(p.get("raw"), str) or not math.isfinite(p.get("latency_s", float("nan"))) or p["latency_s"] < 0 for p in state["predictions"]):
        raise ValueError("Invalid cached prediction record")
    if len(ids) == len(expected):
        for old in state["sessions"]:
            if old["status"] == "running":
                old.update(status="predictions_complete_before_resume", detected_at_utc=now())
        atomic_json(path, state)
        return state
    for old in state["sessions"]:
        if old["status"] == "running":
            old["status"] = "interrupted_before_resume"
    session = {"id": uuid.uuid4().hex, "started_at_utc": now(), "status": "running", "completed": 0,
               "power_start": power_observation(), "resources": None}
    state["sessions"].append(session)
    atomic_json(path, state)
    remaining = rows[len(ids):]
    def completed(prediction, resources):
        if prediction["id"] != expected[len(state["predictions"])]:
            raise ValueError("Inference returned an unexpected incident")
        if signature["system"] != "rules" and (resources["model_revision"] != signature["model_revision"] or
                                               resources["precision"] != signature["precision"] or resources["inference_batch_size"] != 1):
            raise ValueError("Actual inference settings differ from frozen protocol")
        if not isinstance(prediction.get("raw"), str) or not math.isfinite(prediction.get("latency_s", float("nan"))) or prediction["latency_s"] < 0:
            raise ValueError("Invalid inference prediction record")
        state["predictions"].append({**prediction, "evaluation_session_id": session["id"]})
        session["completed"] += 1
        session["resources"] = resources
        atomic_json(path, state)
    try:
        predictions, resources = infer(remaining, completed)
        if len(predictions) != len(remaining) or session["completed"] != len(remaining):
            raise ValueError("Incomplete inference callback stream")
        session.update(status="complete", finished_at_utc=now(), resources=resources, power_end=power_observation())
        atomic_json(path, state)
    except BaseException as error:
        session.update(status="interrupted", error_type=type(error).__name__, last_saved_at_utc=now())
        atomic_json(path, state)
        raise
    return state


def profile(rows):
    return {"incidents": len(rows), "findings": dict(Counter(f for row in rows for f in row["answer"]["findings"])),
            "outcomes": dict(Counter(row["answer"]["connection_outcome"] for row in rows))}


def accuracy_metrics(rows, predictions):
    metrics, _ = score_predictions(rows, predictions)
    return {key: value for key, value in metrics.items() if not key.startswith("latency_")}


def protocol_for(system, manifest, frozen, selection):
    package = Path(__file__).parent
    adapter = Path(selection["adapter"])
    return {"version": 1, "system": system, "extension_sha256": frozen["extension_sha256"],
            "original_dataset_sha256": manifest["original_dataset_sha256"], "model": manifest["model"],
            "model_revision": manifest["model_revision"], "adapter": str(adapter) if system == "adapter" else None,
            "adapter_weights_sha256": file_hash(adapter / "adapter_model.safetensors") if system == "adapter" else None,
            "adapter_config_sha256": file_hash(adapter / "adapter_config.json") if system == "adapter" else None,
            "code_sha256": {name: file_hash(package / name) for name in ("modeling.py", "prompts.py", "schema.py", "evaluation.py")},
            "precision": "torch.bfloat16", "quantization": "nf4", "double_quantization": True,
            "max_new_tokens": 256, "batch_size": 1, "do_sample": False, "thinking": False}


def evaluate_extension(total=200, root="data"):
    folder = Path(root) / "test_extensions" / f"total-{total}"
    with evaluation_lock(folder):
        folder, manifest, frozen, original, additional = freeze_extension(total, root)
        selection = read_json("reports/selection.json")
        if selection["dataset_sha256"] != manifest["original_dataset_sha256"] or selection["rank"] != 8:
            raise ValueError("Original selected adapter changed")
        training = read_json(Path(selection["adapter"]).parent / "training.json")
        cohorts = {"original": {"profile": profile(original), "systems": {}},
                   "additional": {"profile": profile(additional), "systems": {}},
                   "combined": {"profile": profile(original + additional), "systems": {}}}
        sessions, protocols = {}, {}
        for system in ("rules", "original", "adapter"):
            old = read_jsonl(Path("reports") / f"{system}_test_predictions.jsonl")
            if {p["id"] for p in old} != {r["id"] for r in original} or len(old) != len(original):
                raise ValueError("Original prediction IDs differ from original test incidents")
            measured = read_json(Path("reports") / f"{system}_test.json")
            recomputed, _ = score_predictions(original, old)
            if any(measured.get(key) != value for key, value in recomputed.items()):
                raise ValueError("Original measurement does not match saved predictions")
            signature = protocol_for(system, manifest, frozen, selection)
            protocols[system] = signature
            def infer(rows, callback):
                if system == "rules":
                    predictions = []
                    resources = {"peak_gpu_memory_mib": 0, "peak_reserved_memory_mib": 0, "inference_batch_size": 1}
                    for row in rows:
                        start = time.perf_counter()
                        raw = json.dumps(annotate(row["lines"]))
                        prediction = {"id": row["id"], "raw": raw, "latency_s": time.perf_counter()-start}
                        predictions.append(prediction)
                        callback(prediction, resources)
                    return predictions, resources
                return infer_rows(manifest["model"], rows, selection["adapter"] if system == "adapter" else None,
                                  max_new_tokens=256, batch_size=1, on_prediction=callback)
            state = cached_predictions(folder / f"{system}_cache.json", additional, signature, infer)
            fresh = state["predictions"]
            cohorts["original"]["systems"][system] = accuracy_metrics(original, old)
            cohorts["additional"]["systems"][system] = accuracy_metrics(additional, fresh)
            cohorts["combined"]["systems"][system] = accuracy_metrics(original + additional, old + fresh)
            sessions[system] = []
            for session in state["sessions"]:
                latencies = sorted(p["latency_s"] for p in fresh if p["evaluation_session_id"] == session["id"])
                sessions[system].append({**session, "latency_median_s": statistics.median(latencies) if latencies else None,
                                         "latency_p95_s": latencies[math.ceil(len(latencies)*0.95)-1] if latencies else None})
            _, details = score_predictions(additional, fresh)
            write_jsonl(folder / f"{system}_predictions.jsonl", fresh)
            write_jsonl(folder / f"{system}_failures.jsonl", [d for d in details if not d["exact_match"]])
        assert_preserved(manifest)
        audit = read_json(folder / "audit.json")
        result = {"total": total, "original_dataset_sha256": manifest["original_dataset_sha256"],
                  "extension_sha256": frozen["extension_sha256"], "combined_test_sha256": frozen["combined_test_sha256"],
                  "review_method": "assisted source inspection against observable-event rubric", "independent_human_validation": False,
                  "review": {"reviewed_incidents": len(audit), "corrected_references": sum(a["answer"] != a["draft_answer"] for a in audit),
                             "audit_sha256": frozen["audit_sha256"], "selected_inputs_sha256": manifest["selected_inputs_sha256"],
                             "replacement_count": len(manifest["replacement_history"]),
                             "automated_schema_and_evidence_validation": "passed"},
                  "inventory": manifest["inventory"], "cohorts": cohorts, "sessions": sessions, "protocols": protocols,
                  "preservation": {"unchanged": True, "checked_files": len(manifest["original_files"]),
                                   "original_files_sha256": digest(manifest["original_files"])}}
        from .extension_report import report_extension, track_extension
        paths = report_extension(result)
        tracked = track_extension(result, paths, training["mlflow_run_id"])
        return {"report": str(paths["report"]), "metrics": str(paths["metrics"]), "total": total,
                "additional": len(additional), "original": len(original), "tracking": tracked}
