import json

import pytest

from ssh_log_analysis.data import make_record
from ssh_log_analysis.evaluation import parse_prediction, score_predictions
from ssh_log_analysis.schema import validate_answer
from test_data import lines


@pytest.fixture
def row():
    return make_record(lines("Invalid user alice from 192.0.2.1", "Connection closed by 192.0.2.1 [preauth]"))


def test_correct_output_scores_perfectly(row):
    metrics, _ = score_predictions([row], [{"id": row["id"], "raw": json.dumps(row["answer"])}])
    assert metrics["exact_record_accuracy"] == 1 and metrics["findings_micro"]["f1"] == 1


@pytest.mark.parametrize("raw", ["not json", "```json\n{}\n```", '{"user":"alice","user":"bob"}', '{"user":NaN}'])
def test_malformed_or_duplicate_key_outputs_fail(row, raw):
    metrics, _ = score_predictions([row], [{"id": row["id"], "raw": raw}])
    assert metrics["exact_record_accuracy"] == 0 and metrics["schema_valid_rate"] == 0
    assert metrics["findings_micro"]["recall"] == 0


def test_nonexistent_evidence_invalidates_prediction(row):
    answer = {**row["answer"], "evidence_line_ids": ["L999"]}
    parsed, is_json, errors = parse_prediction(json.dumps(answer), row["lines"])
    assert parsed is None and is_json and errors


def test_missing_prediction_counts_as_failure(row):
    metrics, _ = score_predictions([row], [])
    assert metrics["invalid_outputs"] == 1 and metrics["field_accuracy"]["user"] == 0


def test_fence_diagnostic_does_not_relax_primary_metrics(row):
    raw = "```json\n" + json.dumps(row["answer"]) + "\n```"
    metrics, _ = score_predictions([row], [{"id": row["id"], "raw": raw}])
    assert metrics["schema_valid_rate"] == 0
    assert metrics["fence_normalized_content"]["findings_micro"]["f1"] == 1
    assert metrics["markdown_fenced_outputs"] == 1


def test_content_diagnostic_does_not_extract_json_from_prose(row):
    raw = "Here is the answer: " + json.dumps(row["answer"])
    metrics, _ = score_predictions([row], [{"id": row["id"], "raw": raw}])
    assert metrics["fence_normalized_content"]["schema_valid_rate"] == 0


def test_wrong_user_and_unsupported_finding_are_penalized(row):
    answer = {**row["answer"], "user": "bob", "findings": ["authentication_accepted"]}
    metrics, _ = score_predictions([row], [{"id": row["id"], "raw": json.dumps(answer)}])
    assert metrics["field_accuracy"]["user"] == 0 and metrics["unsupported_finding_rate"] == 1
    assert metrics["findings_micro"]["fp"] == 1


def test_duplicate_predictions_raise(row):
    prediction = {"id": row["id"], "raw": json.dumps(row["answer"])}
    with pytest.raises(ValueError, match="Duplicate prediction"):
        score_predictions([row], [prediction, prediction])


def test_schema_rejects_duplicate_findings(row):
    answer = {**row["answer"], "findings": ["invalid_user", "invalid_user"]}
    assert validate_answer(answer)


def test_p95_uses_nearest_rank_for_twenty_observations(row):
    rows = [{**row, "id": str(index)} for index in range(20)]
    predictions = [{"id": r["id"], "raw": json.dumps(r["answer"]), "latency_s": index}
                   for index, r in enumerate(rows)]
    metrics, _ = score_predictions(rows, predictions)
    assert metrics["latency_p95_s"] == 18
