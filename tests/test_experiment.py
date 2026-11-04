import json

import pytest

from ssh_log_analysis.data import make_record
from ssh_log_analysis.storage import write_json, write_jsonl
from test_data import lines


def setup_experiment(tmp_path, monkeypatch, reviewed=True):
    monkeypatch.chdir(tmp_path)
    row = make_record(lines("Invalid user alice from 192.0.2.1"))
    folder = tmp_path / "data" / "filtered"
    write_jsonl(folder / "test.jsonl", [row])
    write_json("data/active.json", {"model": "test-model", "max_length": 1024, "folder": str(folder), "dataset_sha256": "fixed-data", "source_manifest": {"dataset_sha256": "prepared-data"}})
    write_json("data/processed/manifest.json", {"dataset_sha256": "prepared-data"})
    write_json("reports/audit.json", [{"id": row["id"], "answer": row["answer"], "review_status": "rubric_review_completed" if reviewed else "pending"}])
    return {"model": "test-model", "max_length": 1024, "rank": 16}, row


def test_unreviewed_test_labels_block_model_runs(tmp_path, monkeypatch):
    from ssh_log_analysis.experiment import run_experiment
    config, _ = setup_experiment(tmp_path, monkeypatch, reviewed=False)
    with pytest.raises(ValueError, match="Missing or stale test annotation review"):
        run_experiment(config)


def test_changed_dataset_rejects_cached_measurements(tmp_path, monkeypatch):
    from ssh_log_analysis.experiment import run_experiment
    config, _ = setup_experiment(tmp_path, monkeypatch)
    write_json("reports/original_validation.json", {"dataset_sha256": "old-data"})
    with pytest.raises(ValueError, match="Stale measurement"):
        run_experiment(config)


def test_changed_prepared_data_requires_refiltering(tmp_path, monkeypatch):
    from ssh_log_analysis.experiment import run_experiment
    config, _ = setup_experiment(tmp_path, monkeypatch)
    write_json("data/processed/manifest.json", {"dataset_sha256": "new-source"})
    with pytest.raises(ValueError, match="Prepared data changed"):
        run_experiment(config)


def test_same_dataset_with_wrong_model_rejects_cached_measurement(tmp_path, monkeypatch):
    from ssh_log_analysis.experiment import run_experiment
    config, _ = setup_experiment(tmp_path, monkeypatch)
    write_json("reports/original_validation.json", {"dataset_sha256": "fixed-data", "model": "different-model",
                                                  "adapter": None, "split": "validation"})
    with pytest.raises(ValueError, match="Measurement model, adapter, or split differs"):
        run_experiment(config)
