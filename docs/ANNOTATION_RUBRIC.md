# Annotation rubric

Version: observable_event_rules_v2. Annotate facts visible inside one prepared incident; do not add surrounding events from other partitions.

| Finding | Required observation |
|---|---|
| invalid_user | Explicit Invalid user or input_userauth_request invalid-user event |
| password_authentication_failed | Explicit Failed password event, including a repeated-message record |
| authentication_accepted | Accepted password, publickey, or keyboard-interactive authentication |
| too_many_authentication_failures | Explicit Too many authentication failures message |
| connection_closed | Connection closed by, Received disconnect from, Disconnected from, or PAM session closed |
| insufficient_evidence | No event in the five specific categories is established |

Multiple specific findings may coexist. `insufficient_evidence` is exclusive and means insufficient evidence for those specific categories; a generic PAM failure can still establish a failure outcome without establishing a password-specific finding.

Outcome: acceptance wins over earlier failures. Without acceptance, an explicit invalid user, password failure, excessive failures, or generic PAM authentication failure establishes `failure`. Closure, malformed protocol, missing identification strings, and reverse-DNS warnings alone leave authentication outcome `unknown`.

Extract a username, source IP, or hostname only when there is a unique supported value. A hostname in `rhost=` is not an IP address. An IPv4 address immediately before a disconnect-message colon is still an address. Multiple conflicting values result in `null`.

Cite the first line supporting each finding and deduplicate citations. If generic PAM failure alone establishes a failure outcome, include that line. For incidents outside the specific taxonomy, cite L1. All evidence IDs must exist in the supplied input.

No label establishes stolen credentials, malicious intent, password expiration, brute force intent, or compromise. Those conclusions require additional evidence and a different benchmark.

## Audit process

1. Read the source incident and confirm session boundaries are plausible.
2. Check identity fields, every finding, outcome precedence, and canonical evidence.
3. Check absent values and insufficient-evidence cases without guessing.
4. Record the review method and corrections; do not call automated or assisted checks independent human expert validation.
5. Freeze reference hashes before model scoring. A later reference correction invalidates the corresponding measurements and must be reported as a benchmark revision.
