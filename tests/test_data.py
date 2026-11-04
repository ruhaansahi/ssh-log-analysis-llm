from pathlib import Path

from ssh_log_analysis.data import augment, family_id, make_record, prepare, sessionize, validate_dataset
from ssh_log_analysis.schema import annotate
from ssh_log_analysis.storage import read_jsonl


def lines(*messages, pid=101):
    text = "\n".join(f"Nov 04 12:00:{i:02d} lab sshd[{pid}]: {message}" for i, message in enumerate(messages))
    return sessionize(text)[0][0]


def test_interleaved_pids_are_separate_connections():
    text = "\n".join([
        "Nov 04 12:00:00 lab sshd[1]: Invalid user alice from 192.0.2.1",
        "Nov 04 12:00:01 lab sshd[2]: Accepted password for bob from 192.0.2.2 port 1234 ssh2",
        "Nov 04 12:00:02 lab sshd[1]: Connection closed by 192.0.2.1 [preauth]",
    ])
    sessions, skipped = sessionize(text)
    assert skipped == 0 and sorted(map(len, sessions)) == [1, 2]
    assert annotate(sessions[0])["user"] == "alice"


def test_pid_reuse_after_close_or_idle_starts_new_incident():
    text = "\n".join([
        "Nov 04 12:00:00 lab sshd[1]: Connection closed by 192.0.2.1 [preauth]",
        "Nov 04 12:00:01 lab sshd[1]: Invalid user alice from 192.0.2.1",
        "Nov 04 13:00:01 lab sshd[1]: Invalid user bob from 192.0.2.2",
    ])
    assert len(sessionize(text)[0]) == 3


def test_acceptance_after_failure_is_success_with_both_findings():
    answer = annotate(lines("Failed password for alice from 192.0.2.1 port 1234 ssh2",
                            "Accepted password for alice from 192.0.2.1 port 1234 ssh2"))
    assert answer["connection_outcome"] == "success"
    assert answer["findings"] == ["password_authentication_failed", "authentication_accepted"]
    assert answer["evidence_line_ids"] == ["L1", "L2"]


def test_close_alone_does_not_imply_authentication_failure():
    answer = annotate(lines("Connection closed by 192.0.2.1 [preauth]"))
    assert answer["connection_outcome"] == "unknown" and answer["user"] is None


def test_conflicting_users_are_null_and_unknown_events_stay_unknown():
    answer = annotate(lines("pam_unix(sshd:auth): user=alice", "pam_unix(sshd:auth): user=bob"))
    assert answer["user"] is None
    assert answer["findings"] == ["insufficient_evidence"]


def test_invalid_ip_is_not_extracted():
    assert annotate(lines("Connection closed by 999.999.999.999 [preauth]"))["source_ip"] is None


def test_disconnect_ip_before_colon_is_extracted():
    assert annotate(lines("Received disconnect from 192.0.2.1: 11: Bye Bye [preauth]"))["source_ip"] == "192.0.2.1"


def test_pam_failure_establishes_outcome_without_inventing_method():
    answer = annotate(lines("PAM 1 more authentication failure; rhost=192.0.2.1"))
    assert answer["connection_outcome"] == "failure"
    assert answer["findings"] == ["insufficient_evidence"]


def test_augmentation_preserves_family_and_updates_answer():
    original = make_record(lines("Invalid user alice from 192.0.2.1"))
    changed = augment(original, 5)
    assert changed["family_id"] == original["family_id"]
    assert changed["answer"]["user"] == "service-0005"
    assert changed["answer"]["source_ip"] == "192.0.2.6"
    assert changed["answer"]["server_hostname"] == "lab-host-0005"


def test_augmentation_rejects_substitution_that_changes_event_semantics():
    original = make_record(lines("Invalid user user from 192.0.2.1"))
    assert augment(original, 5) is None


def test_family_normalizes_ports_but_preserves_identity():
    assert family_id(lines("Failed password for alice from 192.0.2.1 port 1234 ssh2")) == family_id(lines("Failed password for alice from 192.0.2.1 port 5678 ssh2"))
    assert family_id(lines("Invalid user alice from 192.0.2.1")) != family_id(lines("Invalid user bob from 192.0.2.1"))


def test_preparation_is_repeatable_and_partitions_are_disjoint(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("\n".join(f"Nov 04 12:00:00 lab sshd[{i}]: Invalid user user{i} from 192.0.2.{1+i%254}" for i in range(300)))
    first = prepare(tmp_path / "one", source=source, train_count=40, validation_count=10, test_count=10)
    second = prepare(tmp_path / "two", source=source, train_count=40, validation_count=10, test_count=10)
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert validate_dataset(tmp_path / "one")["valid"]
    families = [{row["family_id"] for row in read_jsonl(tmp_path / "one" / "processed" / f"{split}.jsonl")} for split in ("train", "validation", "test")]
    assert not families[0] & families[1] and not families[0] & families[2] and not families[1] & families[2]
