# Labeling rubric

Version: `observable_event_rules_v2`

These are the rules used to write the correct answer for each incident. Label only what appears in the incident itself, and ignore anything from other incidents.

## Findings

| Finding | When to use it |
|---|---|
| `invalid_user` | The log says `Invalid user`, or `input_userauth_request: invalid user` |
| `password_authentication_failed` | The log says `Failed password`, including repeated-message lines |
| `authentication_accepted` | The log says `Accepted password`, `Accepted publickey` or `Accepted keyboard-interactive` |
| `too_many_authentication_failures` | The log says `Too many authentication failures` |
| `connection_closed` | The log says `Connection closed by`, `Received disconnect from`, `Disconnected from`, or a PAM session closed |
| `insufficient_evidence` | None of the five findings above appear |

An incident can have several findings. `insufficient_evidence` is the exception and is always used on its own.

## Outcome

- Any accepted login makes the outcome `success`, even if there were failures before it.
- Otherwise, an invalid user, a failed password, too many failures or a generic PAM authentication failure makes it `failure`.
- A closed connection, a protocol error, a missing identification string or a reverse-DNS warning on its own leaves the outcome as `unknown`.

A generic PAM failure can set the outcome to `failure` without adding a password finding, because it doesn't say which method failed.

## Usernames, IPs and hostnames

- Fill in an identity field only when the supported log context shows exactly one value. Extract source addresses from source contexts such as `from`, `by` and `rhost=`; a destination in a `connect_to` error is not a source IP.
- If two different values appear, use `null`.
- A hostname in `rhost=` is not an IP address.
- An IPv4 address directly before a colon in a disconnect message still counts as an address.

## Evidence

- For each finding, cite the first line that supports it, and don't list the same line twice.
- If a generic PAM failure is the only thing that makes the outcome `failure`, cite that line too.
- When no explicit finding or generic authentication-failure line supplies evidence, cite `L1` as the fallback for `insufficient_evidence`. If a generic failure is the only evidence, cite that failure line instead; `insufficient_evidence` can accompany a `failure` outcome because the authentication method is unspecified.
- Every cited line must exist in the incident.

## What the labels don't say

No label claims stolen credentials, malicious intent, an expired password, a brute-force attack or a compromised system. Those would need more evidence and a different benchmark.

## Review process

1. Read the incident and check that the grouping of lines looks right.
2. Check each field, each finding, the outcome and the cited lines.
3. Check that empty values and `insufficient_evidence` are justified, without guessing.
4. Record how the review was done and any corrections. Don't describe automated or tool-assisted checks as independent expert review.
5. Freeze the answer hashes before scoring any model. If an answer is corrected afterwards, the affected results are no longer valid and the change has to be reported as a new benchmark version.
