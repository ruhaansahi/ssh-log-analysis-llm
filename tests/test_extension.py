import json
from pathlib import Path

import pytest

from ssh_log_analysis.data import balance, make_record
from ssh_log_analysis.extension import (accuracy_metrics, assert_preserved, atomic_json, audit_entry,
                                       cached_predictions, eligible_candidates, file_hash, freeze_extension,
                                       evaluation_lock, reviewed_rows)
from ssh_log_analysis.storage import digest, read_jsonl, write_json, write_jsonl
from test_data import lines


def source_text(count=300):
    return "\n".join(f"Nov 04 12:00:00 lab sshd[{i}]: Invalid user account{i} from 192.0.2.{1+i%254}" for i in range(count))


def complete_audit(row):
    return {**audit_entry(row), "review_status": "rubric_review_completed", "review_method": "source inspection",
            "review_notes": "Reference checked against supplied evidence."}


def test_extension_selection_is_repeatable_reserved_and_disjoint():
    eligible, first = eligible_candidates(source_text(), set(), lambda row: 100)
    assert len(eligible) > 5
    used = {eligible[0]["family_id"]}
    remaining, second = eligible_candidates(source_text(), used, lambda row: 100)
    assert second["eligible"] == first["eligible"]-1
    selected = balance(remaining, 5, 42)
    repeated = balance(eligible_candidates(source_text(), used, lambda row: 100)[0], 5, 42)
    assert selected == repeated
    assert len({row["family_id"] for row in selected}) == 5
    assert all(row["family_id"] not in used and not row["augmented"] for row in selected)
    assert all(int(digest("split42:"+row["family_id"])[:8], 16) % 100 < 12 for row in selected)


def test_candidate_token_filter_reports_exclusions():
    candidates, _ = eligible_candidates(source_text(), set(), lambda row: 100)
    rejected_id = candidates[0]["id"]
    eligible, inventory = eligible_candidates(source_text(), set(), lambda row: 1025 if row["id"] == rejected_id else 1024)
    assert rejected_id not in {row["id"] for row in eligible}
    assert inventory["excluded_by_token_length"] == 1
    assert len(eligible) == len(candidates)-1


def test_review_gate_and_source_fingerprint():
    row = make_record(lines("Invalid user alice from 192.0.2.1"))
    with pytest.raises(ValueError, match="Missing completed source review"):
        reviewed_rows([row], [audit_entry(row)])
    stale = {**complete_audit(row), "input_sha256": "old-source"}
    with pytest.raises(ValueError, match="Stale audit source"):
        reviewed_rows([row], [stale])


def test_reviewed_labels_need_not_equal_annotation_parser():
    row = make_record(lines("Invalid user account. from 192.0.2.1"))
    assert row["answer"]["user"] == "account"
    audit = complete_audit(row)
    audit["answer"] = {**row["answer"], "user": "account."}
    assert reviewed_rows([row], [audit])[0]["answer"]["user"] == "account."


def test_invalid_reviewed_evidence_is_rejected():
    row = make_record(lines("Invalid user alice from 192.0.2.1"))
    audit = complete_audit(row)
    audit["answer"] = {**row["answer"], "evidence_line_ids": ["L99"]}
    with pytest.raises(ValueError, match="Invalid reviewed reference"):
        reviewed_rows([row], [audit])


def test_original_file_mutation_is_detected(tmp_path):
    path = tmp_path / "original.txt"
    path.write_text("preserve me")
    manifest = {"original_files": {str(path): file_hash(path)}}
    assert_preserved(manifest)
    path.write_text("changed")
    with pytest.raises(ValueError, match="Original experiment file changed"):
        assert_preserved(manifest)


def test_atomic_cache_write_retries_temporary_windows_lock(tmp_path, monkeypatch):
    import ssh_log_analysis.extension as extension
    path = tmp_path / "cache.json"
    atomic_json(path, {"old": True})
    replace = Path.replace
    attempts = []
    def transient_lock(source, target):
        attempts.append(1)
        assert json.loads(path.read_text()) == {"old": True}
        if len(attempts) < 3:
            raise PermissionError("temporary sync lock")
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", transient_lock)
    monkeypatch.setattr(extension.time, "sleep", lambda _: None)
    atomic_json(path, {"new": True})
    assert len(attempts) == 3
    assert json.loads(path.read_text()) == {"new": True}


def test_atomic_cache_write_keeps_previous_file_on_persistent_lock(tmp_path, monkeypatch):
    import ssh_log_analysis.extension as extension
    path = tmp_path / "cache.json"
    atomic_json(path, {"old": True})
    def locked(*_):
        raise PermissionError("persistent lock")
    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(extension.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="persistent lock"):
        atomic_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}


def test_evaluation_lock_rejects_live_owner_and_cleans_up(tmp_path):
    with evaluation_lock(tmp_path):
        with pytest.raises(ValueError, match="Another evaluation process"):
            with evaluation_lock(tmp_path):
                pytest.fail("Concurrent evaluation acquired the same lock")
        assert (tmp_path / "evaluation.lock").exists()
    assert not (tmp_path / "evaluation.lock").exists()


def test_evaluation_lock_recovers_from_reused_pid(tmp_path):
    import os
    write_json(tmp_path / "evaluation.lock", {"pid": os.getpid(), "created": 0.0})
    with evaluation_lock(tmp_path):
        current = json.loads((tmp_path / "evaluation.lock").read_text())
        assert current["pid"] == os.getpid() and current["created"] > 0
    assert not (tmp_path / "evaluation.lock").exists()


def test_length_replacement_requires_new_review_before_freeze(tmp_path, monkeypatch):
    import ssh_log_analysis.extension as extension
    monkeypatch.chdir(tmp_path)
    original = make_record(lines("Accepted password for original from 192.0.2.1 port 1 ssh2"))
    row = make_record(lines("Invalid user alice from 192.0.2.2"))
    replacement = make_record(lines("Invalid user bob from 192.0.2.3"))
    folder = Path("data/test_extensions/total-2")
    for split in ("train", "validation", "test"):
        write_jsonl(Path("data/processed") / f"{split}.jsonl", [original])
    manifest = {"original_files": {}, "selected_ids": [row["id"]], "model": "test", "max_length": 1024,
                "selected_inputs_sha256": digest([{"id": row["id"], "lines": row["lines"]}]), "replacement_history": []}
    write_json(folder / "manifest.json", manifest)
    write_jsonl(folder / "draft.jsonl", [row])
    write_jsonl(folder / "reserve.jsonl", [replacement])
    write_json(folder / "audit.json", [complete_audit(row)])
    monkeypatch.setattr(extension, "base_data", lambda root: (Path("data"), {"dataset_sha256": "original"}, [original]))
    monkeypatch.setattr(extension, "tokenizer_for", lambda model: None)
    monkeypatch.setattr(extension, "token_length", lambda tokenizer, r: 1025 if r["id"] == row["id"] else 100)
    with pytest.raises(ValueError, match="review the new pending incidents"):
        freeze_extension(2)
    assert not (folder / "frozen.json").exists()
    assert json.loads((folder / "audit.json").read_text())[0]["review_status"] == "pending"
    write_json(folder / "audit.json", [complete_audit(replacement)])
    _, _, frozen, _, rows = freeze_extension(2)
    assert rows[0]["id"] == replacement["id"]
    assert frozen["combined_test_sha256"] == digest([original] + rows)
    audit = complete_audit(replacement)
    audit["answer"] = {**replacement["answer"], "user": None}
    write_json(folder / "audit.json", [audit])
    with pytest.raises(ValueError, match="references changed after freezing"):
        freeze_extension(2)


def prediction(row):
    return {"id": row["id"], "raw": json.dumps(row["answer"]), "latency_s": 0.1}


def test_interrupted_predictions_resume_without_repeating_saved_rows(tmp_path):
    rows = [make_record(lines(f"Invalid user account{i} from 192.0.2.1")) for i in range(3)]
    path = tmp_path / "cache.json"
    signature = {"system": "rules", "model_revision": "fixed"}
    def interrupted(remaining, callback):
        callback(prediction(remaining[0]), {})
        raise RuntimeError("simulated interruption")
    with pytest.raises(RuntimeError, match="simulated interruption"):
        cached_predictions(path, rows, signature, interrupted)
    saved = json.loads(path.read_text())
    assert len(saved["predictions"]) == 1
    calls = []
    def resume(remaining, callback):
        calls.extend(row["id"] for row in remaining)
        predictions = [prediction(row) for row in remaining]
        for p in predictions:
            callback(p, {})
        return predictions, {}
    result = cached_predictions(path, rows, signature, resume)
    assert calls == [row["id"] for row in rows[1:]]
    assert [p["id"] for p in result["predictions"]] == [row["id"] for row in rows]
    assert len(result["sessions"]) == 2
    assert cached_predictions(path, rows, signature, lambda *_: pytest.fail("Completed cache replayed"))["predictions"] == result["predictions"]


@pytest.mark.parametrize("change", ["signature", "duplicate", "unknown", "invalid_latency"])
def test_stale_or_invalid_prediction_caches_are_rejected(tmp_path, change):
    rows = [make_record(lines("Invalid user alice from 192.0.2.1"))]
    signature = {"system": "rules", "model_revision": "fixed"}
    state = {"signature": signature, "predictions": [prediction(rows[0])], "sessions": []}
    if change == "signature":
        state["signature"] = {**signature, "model_revision": "changed"}
    elif change == "duplicate":
        state["predictions"] *= 2
    elif change == "unknown":
        state["predictions"][0]["id"] = "not-in-dataset"
    else:
        state["predictions"][0]["latency_s"] = float("nan")
    path = tmp_path / "cache.json"
    atomic_json(path, state)
    with pytest.raises(ValueError):
        cached_predictions(path, rows, signature, lambda *_: pytest.fail("Invalid cache ran inference"))


def test_actual_inference_settings_must_match_protocol(tmp_path):
    row = make_record(lines("Invalid user alice from 192.0.2.1"))
    def infer(rows, callback):
        callback(prediction(row), {"model_revision": "fixed", "precision": "torch.float16", "inference_batch_size": 1})
    with pytest.raises(ValueError, match="settings differ"):
        cached_predictions(tmp_path / "cache.json", [row], {"system": "original", "model_revision": "fixed", "precision": "torch.bfloat16"}, infer)


def test_cache_recovers_when_process_stopped_after_last_prediction(tmp_path):
    row = make_record(lines("Invalid user alice from 192.0.2.1"))
    signature = {"system": "rules"}
    path = tmp_path / "cache.json"
    atomic_json(path, {"signature": signature, "predictions": [prediction(row)],
                       "sessions": [{"status": "running", "completed": 1}]})
    state = cached_predictions(path, [row], signature, lambda *_: pytest.fail("Saved final prediction repeated"))
    assert len(state["sessions"]) == 1
    assert state["sessions"][0]["status"] == "predictions_complete_before_resume"


def test_combined_scores_use_predictions_not_average_of_cohort_f1():
    one = make_record(lines("Invalid user alice from 192.0.2.1"))
    three = make_record(lines("Invalid user bob from 192.0.2.2", "Failed password for invalid user bob from 192.0.2.2 port 1 ssh2", "Connection closed by 192.0.2.2"))
    metrics = accuracy_metrics([one, three], [prediction(one)])
    assert metrics["incidents"] == 2 and metrics["invalid_outputs"] == 1
    assert metrics["findings_micro"]["f1"] == 0.4
    assert not any(key.startswith("latency_") for key in metrics)
