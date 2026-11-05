# Extended test: 200 incidents

Scores below measure agreement with the reviewed, rule-derived references. "Base model" means the pretrained model without the task adapter.

After the original experiment was finished, I added 107 new test incidents to the original 93, for 200 in total. The new incidents come from the same server and the same reserved test pool. Nothing was retrained or reselected: the same base model, rank-8 adapter, rule parser, prompt and settings were used throughout.

The new incidents were picked and checked against their source logs before any model was run on them. This was a tool-assisted review, not an independent expert one. The original 93-incident files were left unchanged, and the tables below show the original group, the new group and both combined.

## Main results

| Group | System | Incidents | Finding F1 | F1 with fences removed | Records fully correct | Valid format | Evidence F1 | Malformed JSON | Unsupported finding rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original | Rule parser | 93 | 1.000 | 1.000 | 93/93 | 93/93 | 1.000 | 0 | 0.000 |
| Original | Base model | 93 | 0.257 | 0.615 | 0/93 | 17/93 | 0.276 | 76 | 0.140 |
| Original | Fine-tuned | 93 | 1.000 | 1.000 | 93/93 | 93/93 | 1.000 | 0 | 0.000 |
| New | Rule parser | 107 | 1.000 | 1.000 | 107/107 | 107/107 | 1.000 | 0 | 0.000 |
| New | Base model | 107 | 0.270 | 0.697 | 0/107 | 26/107 | 0.279 | 81 | 0.224 |
| New | Fine-tuned | 107 | 1.000 | 1.000 | 105/107 | 107/107 | 1.000 | 0 | 0.000 |
| Combined | Rule parser | 200 | 1.000 | 1.000 | 200/200 | 200/200 | 1.000 | 0 | 0.000 |
| Combined | Base model | 200 | 0.265 | 0.664 | 0/200 | 43/200 | 0.277 | 157 | 0.185 |
| Combined | Fine-tuned | 200 | 1.000 | 1.000 | 198/200 | 200/200 | 1.000 | 0 | 0.000 |

## Field and evidence accuracy

| Group | System | User | Source IP | Outcome | Hostname | Evidence precision | Evidence recall |
|---|---|---:|---:|---:|---:|---:|---:|
| Original | Rule parser | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Original | Base model | 0.118 | 0.183 | 0.183 | 0.183 | 0.500 | 0.190 |
| Original | Fine-tuned | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| New | Rule parser | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| New | Base model | 0.159 | 0.243 | 0.243 | 0.243 | 0.500 | 0.193 |
| New | Fine-tuned | 0.991 | 0.991 | 1.000 | 1.000 | 1.000 | 1.000 |
| Combined | Rule parser | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Combined | Base model | 0.140 | 0.215 | 0.215 | 0.215 | 0.500 | 0.192 |
| Combined | Fine-tuned | 0.995 | 0.995 | 1.000 | 1.000 | 1.000 | 1.000 |

The fine-tuned model got two records wrong in the new group: one username and one source IP. Its finding F1 is still perfect, which is why identity fields are scored separately. Both errors stay in the scores. The [methodology notes](../docs/METHODOLOGY.md#error-analysis-and-limits) describe them in more detail.

## What the test covers

| Finding | Original | New | Combined |
|---|---:|---:|---:|
| invalid_user | 19 | 38 | 57 |
| password_authentication_failed | 51 | 100 | 151 |
| authentication_accepted | 10 | 0 | 10 |
| too_many_authentication_failures | 12 | 24 | 36 |
| connection_closed | 51 | 69 | 120 |
| insufficient_evidence | 20 | 7 | 27 |

| Group | Success | Failure | Unknown |
|---|---:|---:|---:|
| Original | 10 | 54 | 29 |
| New | 0 | 100 | 7 |
| Combined | 10 | 154 | 36 |

There were 540 unused families in the reserved test pool. 13 were too long, leaving 527 to choose from. The original sampler and seed picked 107. None of the reviewed answers needed correcting, and no replacements were needed for length.

The new group has no successful logins, so it adds no evidence about how well successful logins are detected. That still rests on the original 10 cases. The extra incidents mainly widen coverage of failures.

The sample is balanced across event types, so it doesn't reflect real-world frequencies. Templates and identities can still repeat across splits. The parser also wrote the draft answers, so its perfect agreement is expected.

Scoring works the same way as on the original test. Malformed or wrongly structured answers count as failures. "F1 with fences removed" strips Markdown fences and nothing else. Evidence is matched to the reviewed line IDs.

## Run sessions

Timing and memory are reported for each new run session and aren't pooled with the original measurements. Model loading, warm-up and saving progress are excluded from generation latency. The adapter was slower than the base model in this additional cohort, despite being faster in the earlier matched 20-incident check. Neither measurement establishes a universal speed gain.

| System | Session | Incidents completed | Status | Median seconds | p95 seconds | Peak allocated MiB | Peak reserved MiB | On mains power (start/end) |
|---|---|---:|---|---:|---:|---:|---:|---|
| Rule parser | 1 | 31 | interrupted | 0.0002 | 0.0003 | 0 | 0 | yes / unknown |
| Rule parser | 2 | 76 | complete | 0.0002 | 0.0004 | 0 | 0 | yes / yes |
| Base model | 1 | 107 | complete | 8.7620 | 12.4822 | 1484 | 2382 | yes / yes |
| Fine-tuned | 1 | 107 | complete | 9.2162 | 12.0691 | 1517 | 2422 | yes / yes |

## Reproducibility

- Original experiment SHA-256: `17c2c0ad5db69aba0f4ea38619ae371cd2a5450b7b2ea925039ce29638bf792b`
- New incidents SHA-256: `00b5ca6da465c4032c9c0fe96382ce80b387005477ee940ff9a65b72f46ca30c`
- Combined test SHA-256: `1557a7a2925ee29bbf01bae668429e31e9fa94881af20aaf3f431c2f44c174bf`

29 files from the original experiment, including its datasets, predictions and adapter, were checked and confirmed unchanged. The full metrics and settings are in [extended_200_metrics.json](extended_200_metrics.json). Raw logs, reviewed answers and prediction caches stay local in the ignored `data/` folder.
