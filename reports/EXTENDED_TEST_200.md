# Extended SSH Test: 200 Incidents

This is a same-source extension evaluated with the unchanged original model, selected rank-8 adapter, and parser. References use the existing observable-event rubric and assisted source review. Independent human or expert validation is not claimed.

The original 93-case artifacts remain unchanged. Additional cases were selected and reviewed before generating predictions. This report separates the historical cohort, the new cohort, and their combined scores.

| Cohort | System | Incidents | Strict finding F1 | Fence-normalized F1 | Exact records | Schema valid | Evidence F1 | Malformed JSON | Invalid outputs | Unsupported finding rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| original | rules | 93 | 1.000 | 1.000 | 93/93 | 93/93 | 1.000 | 0 | 0 | 0.000 |
| original | original | 93 | 0.257 | 0.615 | 0/93 | 17/93 | 0.276 | 76 | 76 | 0.140 |
| original | adapter | 93 | 1.000 | 1.000 | 93/93 | 93/93 | 1.000 | 0 | 0 | 0.000 |
| additional | rules | 107 | 1.000 | 1.000 | 107/107 | 107/107 | 1.000 | 0 | 0 | 0.000 |
| additional | original | 107 | 0.270 | 0.697 | 0/107 | 26/107 | 0.279 | 81 | 81 | 0.224 |
| additional | adapter | 107 | 1.000 | 1.000 | 105/107 | 107/107 | 1.000 | 0 | 0 | 0.000 |
| combined | rules | 200 | 1.000 | 1.000 | 200/200 | 200/200 | 1.000 | 0 | 0 | 0.000 |
| combined | original | 200 | 0.265 | 0.664 | 0/200 | 43/200 | 0.277 | 157 | 157 | 0.185 |
| combined | adapter | 200 | 1.000 | 1.000 | 198/200 | 200/200 | 1.000 | 0 | 0 | 0.000 |

## Field and evidence accuracy

| Cohort | System | User | Source IP | Outcome | Hostname | Evidence precision | Evidence recall |
|---|---|---:|---:|---:|---:|---:|---:|
| original | rules | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| original | original | 0.118 | 0.183 | 0.183 | 0.183 | 0.500 | 0.190 |
| original | adapter | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| additional | rules | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| additional | original | 0.159 | 0.243 | 0.243 | 0.243 | 0.500 | 0.193 |
| additional | adapter | 0.991 | 0.991 | 1.000 | 1.000 | 1.000 | 1.000 |
| combined | rules | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| combined | original | 0.140 | 0.215 | 0.215 | 0.215 | 0.500 | 0.192 |
| combined | adapter | 0.995 | 0.995 | 1.000 | 1.000 | 1.000 | 1.000 |

The additional adapter cohort has 2 non-exact records. Field errors: user 1, source IP 1, outcome 0, and hostname 0. Finding F1 alone does not measure correct identity extraction. See the [error analysis](../docs/METHODOLOGY.md#error-analysis-and-limits) for interpretation; all failures remain in scoring.

## Coverage and interpretation

| Finding | Original positives | Additional positives | Combined positives |
|---|---:|---:|---:|
| invalid_user | 19 | 38 | 57 |
| password_authentication_failed | 51 | 100 | 151 |
| authentication_accepted | 10 | 0 | 10 |
| too_many_authentication_failures | 12 | 24 | 36 |
| connection_closed | 51 | 69 | 120 |
| insufficient_evidence | 20 | 7 | 27 |

| Cohort | Success | Failure | Unknown |
|---|---:|---:|---:|
| original | 10 | 54 | 29 |
| additional | 0 | 100 | 7 |
| combined | 10 | 154 | 36 |

Candidate inventory: 540 unused reserved families, 13 excluded by token length, and 527 eligible. All 107 selected incidents received assisted source review; 0 references were corrected and 0 length replacements were needed. Automated schema and evidence-reference validation passed separately from source inspection.

The additional cohort has no accepted-authentication positives; its size must not be presented as additional evidence about successful authentication. Class supports describe coverage, and zero-support classes are not evidence of successful detection. False positives still affect the overall metrics.

The original class sampler and seed 42 were reused after filtering unused reserved test families. This is a coverage-oriented sample, not an estimate of production event frequencies. Shared templates and identities remain possible. The parser also produced draft references; high parser agreement is expected and does not establish independent correctness.

Strict scores count malformed or schema-invalid output as failed incidents. The existing secondary diagnostic removes only exact outer Markdown fences. Canonical evidence scores require the reviewed line IDs; an alternative supporting line can lose canonical-match credit. Unsupported finding rate counts schema-valid responses with extra findings, with invalid responses reported separately.

## New evaluation sessions

Timing is reported per new execution session only. Original and additional timing or memory measurements are not pooled into a model-speed comparison. Loading, warm-up, and checkpoint writes are outside the recorded generation latency. GPU peaks cover warm-up and generation after model loading, rather than load-time transients. Reserved GPU memory includes allocator cache; allocated memory is not total process/device usage.

| System | Session | Completed incidents | Status | Median seconds | p95 seconds | Peak allocated MiB | Peak reserved MiB | AC at start/end |
|---|---|---:|---|---:|---:|---:|---:|---|
| rules | 1 | 31 | interrupted | 0.0002 | 0.0003 | 0 | 0 | True/unknown |
| rules | 2 | 76 | complete | 0.0002 | 0.0004 | 0 | 0 | True/True |
| original | 1 | 107 | complete | 8.7620 | 12.4822 | 1484 | 2382 | True/True |
| adapter | 1 | 107 | complete | 9.2162 | 12.0691 | 1517 | 2422 | True/True |

## Reproducibility

Original experiment SHA-256: `17c2c0ad5db69aba0f4ea38619ae371cd2a5450b7b2ea925039ce29638bf792b`.

Reviewed extension SHA-256: `00b5ca6da465c4032c9c0fe96382ce80b387005477ee940ff9a65b72f46ca30c`.

Combined test SHA-256: `1557a7a2925ee29bbf01bae668429e31e9fa94881af20aaf3f431c2f44c174bf`.

Original file preservation: 29 files verified unchanged, including original datasets, predictions, and adapter artifacts.

Full aggregate metrics, session metadata, and frozen inference settings: [extended_200_metrics.json](extended_200_metrics.json).

Raw source logs, reviewed annotations, incremental prediction caches, and failure records remain local under the ignored data directory. No model training or selection was performed for this extension.
