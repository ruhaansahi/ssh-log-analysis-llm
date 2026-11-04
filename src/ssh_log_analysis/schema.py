"""Strict public output contract and evidence-grounded annotation rules."""

import ipaddress
import re

FINDINGS = (
    "invalid_user", "password_authentication_failed", "authentication_accepted",
    "too_many_authentication_failures", "connection_closed", "insufficient_evidence",
)
FIELDS = {"user", "source_ip", "connection_outcome", "findings", "evidence_line_ids", "server_hostname"}
RULES = {
    "invalid_user": re.compile(r"(?:Invalid user\s+\S+\s+from|input_userauth_request: invalid user\s+\S+)", re.I),
    "password_authentication_failed": re.compile(r"Failed password for\s+", re.I),
    "authentication_accepted": re.compile(r"Accepted (?:password|publickey|keyboard-interactive(?:/pam)?) for\s+", re.I),
    "too_many_authentication_failures": re.compile(r"Too many authentication failures", re.I),
    "connection_closed": re.compile(r"Connection closed by|Received disconnect from|Disconnected from|session closed for user", re.I),
}
USER_PATTERNS = [
    re.compile(r"(?:Invalid user|input_userauth_request: invalid user)\s+(\S+)", re.I),
    re.compile(r"(?:Failed password|Accepted \S+) for (?:invalid user )?(\S+)", re.I),
    re.compile(r"Too many authentication failures for (\S+)", re.I),
    re.compile(r"\buser=([^\s;]+)"),
    re.compile(r"session (?:opened|closed) for user (\S+)", re.I),
]
IP_PATTERN = re.compile(r"(?:\bfrom\s+|\bby\s+|\brhost=)([0-9a-fA-F:.]+)(?=\s|$|;|\])")


def unique_or_none(values):
    values = set(values)
    return next(iter(values)) if len(values) == 1 else None


def annotate(lines):
    """Extract observable events, not unobservable attack intent or root causes.

    Each line is {id, text, message, hostname}. First evidence per finding is
    canonical; an evaluator may separately validate alternative supporting lines.
    """
    if not lines:
        raise ValueError("An incident needs at least one log line")
    evidence, users, ips, hosts = {}, set(), set(), set()
    for line in lines:
        message = line["message"]
        hosts.add(line["hostname"])
        for finding, pattern in RULES.items():
            if pattern.search(message):
                evidence.setdefault(finding, line["id"])
        for pattern in USER_PATTERNS:
            for match in pattern.finditer(message):
                users.add(match.group(1).rstrip(".,;:"))
        for match in IP_PATTERN.finditer(message):
            try:
                candidate = match.group(1)
                if "." in candidate:
                    candidate = candidate.rstrip(":")
                ips.add(str(ipaddress.ip_address(candidate)))
            except ValueError:
                pass
    findings = [f for f in FINDINGS if f in evidence]
    outcome = "unknown"
    if "authentication_accepted" in findings:
        outcome = "success"
    elif any(f in findings for f in ("invalid_user", "password_authentication_failed", "too_many_authentication_failures")):
        outcome = "failure"
    elif any(re.search(r"authentication failure", line["message"], re.I) for line in lines):
        # PAM explicitly establishes failure even without an identified method.
        outcome = "failure"
        evidence["outcome_failure"] = next(line["id"] for line in lines if re.search(r"authentication failure", line["message"], re.I))
    if not findings:
        findings = ["insufficient_evidence"]
    return {
        "user": unique_or_none(users), "source_ip": unique_or_none(ips),
        "connection_outcome": outcome, "findings": findings,
        "evidence_line_ids": list(dict.fromkeys(evidence.values())) or [lines[0]["id"]],
        "server_hostname": unique_or_none(hosts),
    }


def validate_answer(answer, line_ids=None):
    """Return errors instead of silently repairing malformed model predictions."""
    errors = []
    if not isinstance(answer, dict) or set(answer) != FIELDS:
        return ["Output must be an object with exactly the six documented fields"]
    for key in ("user", "source_ip", "server_hostname"):
        if answer[key] is not None and (not isinstance(answer[key], str) or not answer[key].strip()):
            errors.append(f"{key} must be a nonempty string or null")
    if isinstance(answer["source_ip"], str):
        try:
            ipaddress.ip_address(answer["source_ip"])
        except ValueError:
            errors.append("source_ip is not a valid IP address")
    if answer["connection_outcome"] not in ("success", "failure", "unknown"):
        errors.append("Invalid connection_outcome")
    for key, allowed in (("findings", set(FINDINGS)), ("evidence_line_ids", None)):
        value = answer[key]
        if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
            errors.append(f"{key} must be a nonempty list of strings")
        elif len(set(value)) != len(value):
            errors.append(f"{key} contains duplicates")
        elif allowed is not None and not set(value) <= allowed:
            errors.append("Unknown finding")
        elif key == "evidence_line_ids" and line_ids is not None and not set(value) <= set(line_ids):
            errors.append("Evidence references nonexistent lines")
    if isinstance(answer["findings"], list) and "insufficient_evidence" in answer["findings"] and len(answer["findings"]) > 1:
        errors.append("insufficient_evidence cannot accompany explicit findings")
    return errors
