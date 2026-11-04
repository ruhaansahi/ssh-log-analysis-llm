"""Download, sessionize, annotate, partition, and audit public SSH logs."""

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import random
import re
import tarfile
import urllib.request

from .schema import annotate, validate_answer
from .storage import digest, read_jsonl, write_json, write_jsonl

SOURCE_URL = "https://zenodo.org/records/8196385/files/SSH.tar.gz?download=1"
SAMPLE_URL = "https://raw.githubusercontent.com/logpai/loghub/master/OpenSSH/OpenSSH_2k.log"
SOURCE_PAGE = "https://github.com/logpai/loghub/tree/master/OpenSSH"
PREFIX = re.compile(r"^(?P<stamp>[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+(?P<host>\S+)\s+sshd\[(?P<pid>\d+)\]:\s*(?P<message>.*)$")


def download_source(root="data", sample=False):
    raw = Path(root) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    destination = raw / ("OpenSSH_2k.log" if sample else "SSH.tar.gz")
    url = SAMPLE_URL if sample else SOURCE_URL
    if not destination.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "ssh-log-analysis-llm/0.1 (academic experiment)"})
        temporary = destination.with_suffix(destination.suffix + ".partial")
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        temporary.replace(destination)
    manifest = {"url": url, "source_page": SOURCE_PAGE, "sha256": digest(destination.read_bytes()),
                "bytes": destination.stat().st_size, "license_note": "Loghub: research/academic use; retain source and paper attribution."}
    write_json(raw / "source.json", manifest)
    if sample:
        return destination, manifest
    log = raw / "OpenSSH.log"
    if not log.exists():
        with tarfile.open(destination, "r:gz") as archive:
            members = [m for m in archive.getmembers() if m.isfile() and m.name.lower().endswith(".log")]
            if not members:
                raise ValueError("Source archive has no .log member")
            member = max(members, key=lambda m: m.size)
            if member.size > 300_000_000:
                raise ValueError("Unexpected source archive size")
            with archive.extractfile(member) as source, log.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    manifest["log_sha256"] = digest(log.read_bytes())
    write_json(raw / "source.json", manifest)
    return log, manifest


def sessionize(text, gap_seconds=1800):
    """Host + PID + idle-gap boundaries; close events also terminate sessions."""
    active, sessions = {}, []
    skipped, year, previous = 0, 2000, None
    for source_line, raw in enumerate(text.splitlines(), 1):
        match = PREFIX.match(raw)
        if not match:
            skipped += 1
            continue
        stamp = datetime.strptime(f"{year} {match['stamp']}", "%Y %b %d %H:%M:%S")
        if previous is not None and (previous - stamp).days > 180:
            year += 1
            stamp = stamp.replace(year=year)
        previous = stamp
        key = (match["host"], match["pid"])
        current = active.get(key)
        if current and ((stamp - current["last"]).total_seconds() > gap_seconds or stamp < current["last"]):
            sessions.append(current["lines"])
            current = None
        if current is None:
            current = {"last": stamp, "lines": []}
            active[key] = current
        current["last"] = stamp
        current["lines"].append({"id": f"L{len(current['lines'])+1}", "text": raw,
                                 "message": match["message"], "hostname": match["host"],
                                 "source_line": source_line})
        # Keep the final disconnect and all preceding messages in one incident.
        if re.search(r"Connection closed by|Received disconnect from|Disconnected from", match["message"], re.I):
            sessions.append(current["lines"])
            active.pop(key)
    sessions.extend(value["lines"] for value in active.values())
    sessions.sort(key=lambda lines: lines[0]["source_line"])
    return sessions, skipped


def family_id(lines):
    # Keep identities: repeated attempts from the same source stay together.
    # Shared event templates across splits are explicitly reported, not concealed.
    return digest([(line["hostname"], re.sub(r"\bport \d+", "port <PORT>", line["message"])) for line in lines])


def make_record(lines):
    return {"id": digest([line["text"] for line in lines])[:20], "family_id": family_id(lines),
            "lines": lines, "answer": annotate(lines), "provenance": "observable_event_rules_v2",
            "augmented": False}


def balance(rows, count, seed):
    rng = random.Random(seed)
    buckets = defaultdict(list)
    priority = ("authentication_accepted", "too_many_authentication_failures", "invalid_user",
                "password_authentication_failed", "connection_closed", "insufficient_evidence")
    for row in rows:
        category = next(f for f in priority if f in row["answer"]["findings"])
        buckets[category].append(row)
    for values in buckets.values():
        rng.shuffle(values)
    result = []
    while buckets and len(result) < count:
        for key in list(sorted(buckets)):
            if len(result) >= count:
                break
            result.append(buckets[key].pop())
            if not buckets[key]:
                del buckets[key]
    rng.shuffle(result)
    return result


def augment(row, index):
    """Replace identified values while preserving event semantics and partition."""
    replacements = {}
    answer = row["answer"]
    if answer["user"]:
        replacements[answer["user"]] = f"service-{index:04d}"
    if answer["source_ip"]:
        replacements[answer["source_ip"]] = f"192.0.2.{1 + index % 254}"
    if answer["server_hostname"]:
        replacements[answer["server_hostname"]] = f"lab-host-{index:04d}"
    if not replacements:
        return None
    pattern = re.compile("|".join(re.escape(x) for x in sorted(replacements, key=len, reverse=True)))
    def substitute(value):
        return pattern.sub(lambda m: replacements[m.group()], value)
    lines = [{**line, "text": substitute(line["text"]), "message": substitute(line["message"]),
              "hostname": substitute(line["hostname"])} for line in row["lines"]]
    result = make_record(lines)
    if result["answer"]["findings"] != answer["findings"] or result["answer"]["connection_outcome"] != answer["connection_outcome"]:
        return None
    result.update(family_id=row["family_id"], augmented=True, parent_id=row["id"], provenance="training_identity_substitution_v1")
    return result


def prepare(root="data", source=None, sample=False, train_count=1500, validation_count=150, test_count=100):
    root = Path(root)
    if source:
        path = Path(source)
        manifest = {"url": "local_file", "sha256": digest(path.read_bytes()), "source_page": SOURCE_PAGE}
    else:
        path, manifest = download_source(root, sample)
    sessions, skipped = sessionize(path.read_text(encoding="utf-8", errors="replace"))
    partitions, seen = defaultdict(list), set()
    oversized = 0
    for lines in sessions:
        if len(lines) > 32:
            oversized += 1
            continue
        row = make_record(lines)
        family = row["family_id"]
        if family in seen:
            continue
        seen.add(family)
        bucket = int(digest("split42:" + family)[:8], 16) % 100
        split = "test" if bucket < 12 else "validation" if bucket < 24 else "train"
        partitions[split].append(row)
    targets = {"train": train_count, "validation": validation_count, "test": test_count}
    selected = {split: balance(partitions[split], count, 42) for split, count in targets.items()}
    original_train = list(selected["train"])
    variations = [augment(row, i) for i, row in enumerate(original_train[:len(original_train)//5])]
    selected["train"].extend(row for row in variations if row is not None)
    for split, rows in selected.items():
        write_jsonl(root / "processed" / f"{split}.jsonl", rows)
    counts = {s: {"incidents": len(rows), "originals": sum(not r["augmented"] for r in rows),
                  "findings": dict(Counter(f for r in rows for f in r["answer"]["findings"]))} for s, rows in selected.items()}
    summary = {"source": manifest, "seed": 42, "raw_sessions": len(sessions), "unparsed_lines": skipped,
               "oversized_sessions_excluded": oversized, "unique_families": len(seen), "partitions": counts,
               "targets": targets, "dataset_sha256": digest(selected),
               "limitations": ["Labels are rule-generated, not independent expert ground truth.",
                               "Log templates may be shared across partitions; this is not an unseen-template benchmark.",
                               "Duplicate families retain identities and normalize ports; grouping is not a guarantee of semantic independence."]}
    write_json(root / "processed" / "manifest.json", summary)
    validate_dataset(root)
    return summary


def validate_dataset(root="data"):
    folder = Path(root) / "processed"
    partitions = {s: read_jsonl(folder / f"{s}.jsonl") for s in ("train", "validation", "test")}
    seen_families, seen_ids = {}, set()
    for split, rows in partitions.items():
        if not rows:
            raise ValueError(f"Empty {split} partition")
        for row in rows:
            errors = validate_answer(row["answer"], [line["id"] for line in row["lines"]])
            if errors or row["answer"] != annotate(row["lines"]):
                raise ValueError(f"Invalid annotation {row['id']}: {errors}")
            if row["id"] in seen_ids:
                raise ValueError("Duplicate incident ID")
            seen_ids.add(row["id"])
            previous = seen_families.setdefault(row["family_id"], split)
            if previous != split:
                raise ValueError("Family leakage across partitions")
            if split != "train" and row["augmented"]:
                raise ValueError("Augmentation outside training")
    return {"valid": True, "counts": {s: len(rows) for s, rows in partitions.items()}}


def export_audit(root="data", output="reports/audit.json"):
    """An audit queue. No item is automatically stamped as human-reviewed."""
    rows = read_jsonl(Path(root) / "processed" / "test.jsonl")
    audit = [{"id": row["id"], "logs": [line["text"] for line in row["lines"]],
              "answer": row["answer"], "source_line_numbers": [line["source_line"] for line in row["lines"]],
              "automated_checks": "passed", "review_status": "pending", "review_notes": ""} for row in rows]
    write_json(output, audit)
    return {"incidents": len(audit), "path": str(output)}
