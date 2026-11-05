# Methodology

## Objective and task boundary

Extract observable SSH authentication events into a six-field JSON record with source-line evidence. The system identifies a username, source IP, authentication outcome, supported findings, evidence IDs, and server hostname. Missing or conflicting identity values become `null`.

The task does not establish malicious intent, stolen credentials, compromise, or hidden root causes. A disconnect alone does not establish authentication failure. An explicit acceptance takes precedence over earlier failures in the same incident.

## Data preparation and reference annotations

The source is Loghub's historical OpenSSH laboratory-server dataset. Download URLs and SHA-256 checksums are retained locally. Raw logs are excluded from Git.

Events are grouped by server hostname, SSH process ID, and time continuity. Disconnect messages terminate a group. Duplicate-family keys preserve identities and event text while normalizing port numbers. Families are assigned to deterministic partitions before balanced sampling. Training-only username, IP, and hostname substitutions preserve the parent partition and are rejected when they alter finding or outcome semantics.

References follow the [annotation rubric](ANNOTATION_RUBRIC.md). Automated checks validate the schema, supported events, outcome precedence, and evidence references. All 100 selected test incidents underwent assisted source-log review before training; 93 remained after length filtering. This is not independent expert validation. The audit corrected an IPv4 delimiter extraction bug and a generic PAM failure-outcome bug before model scoring.

The frozen experiment uses 1,599 training examples (1,330 source incidents and 269 identity variations), 142 validation incidents, and 93 test incidents. Combined prompt and target lengths must fit 1,024 tokens. Oversized examples are excluded consistently from every compared system, preserving complete evidence rather than silently truncating it.

## Same-source test extension

After the original experiment was complete, a separate extension added 107 previously unused incidents to make a combined 200-incident benchmark. The original 93-case datasets, predictions, metrics, training partitions, validation partitions, and model weights were preserved. There was no retraining or new model selection.

Candidates were reconstructed from the original downloaded logs using the same hostname/process/time grouping, duplicate-family definitions, and reserved test partition. Every family selected in the original processed training, validation, or test partitions was excluded, including examples subsequently removed by token filtering. This left 540 unused reserved families; 13 exceeded the 1,024-token combined prompt/target limit, leaving 527 eligible families. Incidents also had to fit the original 32-line limit. The existing category sampler and seed 42 selected 107 cases, and source IDs were recorded before examining model predictions.

All 107 selected references underwent assisted source-log inspection against the existing observable-event rubric before inference. Schema and evidence-reference checks were recorded separately from that inspection. Reviewed references could differ from parser drafts; no corrections were needed in this selection. Independent human or expert validation is not claimed. A final token-length check preceded freezing of reference and combined-test fingerprints. A deterministic reserve supplies replacements only for pre-evaluation length ineligibility, with mandatory source review of each replacement.

The additional cohort has 100 failure and seven unknown outcomes, and no accepted-authentication positives. It does not increase successful-authentication coverage. Class supports are reported separately for the original, additional, and combined cohorts. Balanced selection is intended for coverage rather than estimating production event frequencies.

The original model, selected rank-8 adapter, and rule parser are evaluated on identical additional IDs. Original 93-case predictions are reused rather than regenerated. Combined metrics are computed from all 200 individual records, rather than averaging cohort F1 scores. The same six-field schema, model revision, prompt, NF4/double quantization, BF16 computation, non-thinking template, deterministic decoding, 256-token response limit, and inference batch size one are retained.

Each completed prediction is saved atomically to a local cache. Temporary Windows file locks receive bounded retries. Resume checks include reference, original dataset, model revision, adapter weights/configuration, prompt and scoring code, and decoding fingerprints; duplicate or out-of-order prediction IDs are rejected. A process lock prevents concurrent evaluation of the same extension. Timing, power observations, and GPU memory are reported per new execution session, including interrupted sessions; historical and new timing is not pooled into a controlled speed comparison. A separate local MLflow evaluation run links back to the selected training run.

See the [separate extended report](../reports/EXTENDED_TEST_200.md) and [aggregate metrics](../reports/extended_200_metrics.json). This remains a same-source benchmark with assisted reference review. No examples or model settings are changed in response to extension predictions.

## Model adaptation

The original model is Qwen3-1.7B, pinned to revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`. Training uses PyTorch, Transformers, Datasets, PEFT, and TRL on a 12 GB RTX 5070 Laptop GPU under native Windows and Python 3.12.

Completion-only SFT computes next-token cross-entropy on the JSON response and response-ending token. Prompt tokens are excluded from the loss. QLoRA keeps the original model frozen with NF4 weights and double quantization while optimizing low-rank adapters on attention and MLP projections. Computation uses BF16 on the verified GPU.

Both candidates use one epoch, learning rate `2e-4`, microbatch 1, gradient accumulation 8, maximum combined sequence length 1,024, gradient checkpointing, and seed 42. One epoch yields 200 optimizer updates. The five-step smoke test separately verifies finite optimization, frozen base-parameter flags, changing adapter weights, and successful save/reload. It is not a performance benchmark.

| Candidate | Trainable parameters | Percent trainable | Training minutes | Validation exact records | Validation finding F1 |
|---|---:|---:|---:|---:|---:|
| Rank 16 | 17,432,576 | 1.00% | 28.59 | 141/142 | 0.998 |
| Rank 8 | 8,716,288 | 0.50% | 30.40 | 141/142 | 1.000 |

Rank 8 was selected using validation exact-record accuracy, followed by finding F1, schema validity, and validation loss. Rank 16 had lower token-level validation loss but lost the finding-F1 tie-break. The smaller adapter was not faster to train in these single runs; device conditions and runtime variability were not tightly controlled enough to attribute that timing difference to rank alone.

## Evaluation protocol

The rule parser, original model, and selected adapter are scored on identical frozen test incidents. The rule parser also generates the reference labels, so its perfect agreement is circular and expected. It establishes consistency with the annotation policy, not perfect real-world detection.

Original and adapted inference use identical prompts, quantization, non-thinking chat templates, deterministic decoding, and a 256-token response limit. Reference answers are not included in model inputs. Validation inference uses batches of four; final test inference uses batch size one. Validation timing is amortized batch time, not single-request latency.

Primary metrics include finding precision/recall/F1, field and outcome accuracy, exact-record accuracy, evidence precision/recall, invalid-output rate, unsupported-finding rate, generation latency, and GPU memory. Malformed or schema-invalid predictions fail the incident. Missing predictions also fail. Duplicate prediction IDs are rejected. Exact records compare finding and evidence lists without requiring their order to match.

A secondary diagnostic removes only exact outer Markdown JSON fences. It does not repair JSON or extract objects from prose. This diagnostic was defined using validation before final testing and separates formatting compliance from content extraction. The primary score continues to reject fenced responses.

Canonical evidence metrics require reference line IDs. Another supporting line can be substantively valid while losing canonical-match credit, so supported-citation rate is also recorded. Unsupported-finding rate counts schema-valid responses containing extra findings; invalid responses are reported separately.

During final evaluation, the laptop changed from battery to AC power. Full-test timing is observational. A paired AC-power comparison uses the last 20 frozen test incidents for both models; this window was fixed before those original-model timings were collected. Timing excludes model loading and a fixed synthetic warm-up. PyTorch allocated and reserved memory are reported separately.

See [measured results](../reports/RESULTS.md), machine-readable metrics, and [training histories](../reports/training_histories.json). Parameters, dataset versions, losses, validation results, resource use, and report artifacts are retained in local MLflow.

## Error analysis and limits

The selected rank-8 adapter matched all six reference fields for 93/93 test incidents, with finding F1 1.000 and no invalid outputs. The result is agreement with this constrained reference policy; it does not erase the validation failures or establish performance on new sources.

The original model returned Markdown fences in 76 of 93 test responses. Its strict finding F1 was 0.257; fence-only normalization raised it to 0.615. Content errors remained, including unsupported excessive-authentication-failure findings, omitted connection closures, and incorrect evidence references.

On the additional 107 incidents, the selected adapter matched 105 complete records. Finding F1, canonical evidence F1, outcome accuracy, and schema validity were all 1.000, but two identity-extraction errors remained: one username was copied incorrectly, and one destination address in a `connect_to` error was incorrectly reported as the source IP instead of leaving the source absent. User and source-IP accuracy were each 106/107. Both failures were retained without changing examples, references, prompts, or weights.

Combined with the original 93 cases, the adapter matched 198/200 complete records (99.0%), with finding F1 1.000 and no invalid outputs. The original model's additional-cohort strict finding F1 was 0.270, or 0.697 after outer-fence removal; its combined scores were 0.265 and 0.664, respectively. The expanded benchmark exposes field errors that finding F1 alone would hide. Successful-authentication support remains the original ten incidents.

Both adapters missed one validation record involving a PAM summary. Rank 16 copied a reverse-DNS hostname into `source_ip`, violating the IP-or-null contract. Rank 8 correctly left the IP absent but omitted the available username. These cases were retained; they did not trigger a reference or prompt change.

This benchmark has one historical source, one server hostname, shared event templates, some shared identities, and small class supports. Zero duplicate-family overlap does not establish unseen-template, unseen-user, unseen-IP, or cross-host generalization. Balanced sampling differs from production event prevalence. Independent annotations and a separate log source would be needed to support broader detection claims. For fixed log formats, the deterministic parser remains a practical alternative to an LLM.
