# Results: original 93-incident test

These are the results on the original frozen test set. Scores measure agreement with the reviewed, rule-derived reference answers. "Base model" means pretrained Qwen without the task adapter. The reference answers come from the rule-based parser, so the parser's perfect score is expected. For the 200-incident version, see [EXTENDED_TEST_200.md](EXTENDED_TEST_200.md).

## Main comparison

| System | Incidents | Finding F1 | F1 with code fences removed | Records fully correct | Valid format | Evidence F1 | Median seconds | Peak GPU MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rule parser | 93 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.0001 | 0 |
| Base Qwen3-1.7B | 93 | 0.257 | 0.615 | 0.000 | 0.183 | 0.276 | 12.0833 | 1482 |
| Fine-tuned (rank 8) | 93 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 7.6831 | 1515 |

Fine-tuning raised finding F1 by 0.743 and exact-record accuracy from 0 to 1.

## Fields, evidence and output quality

| System | User | Source IP | Outcome | Hostname | Evidence precision | Evidence recall | Invalid output rate | Unsupported finding rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rule parser | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 |
| Base model | 0.118 | 0.183 | 0.183 | 0.183 | 0.500 | 0.190 | 0.817 | 0.140 |
| Fine-tuned | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 |

The unsupported finding rate counts answers in the correct format that include a finding absent from the reference answer, divided by all incidents. It measures extra findings relative to the annotation policy. Invalid answers are counted separately, so a low unsupported rate on its own doesn't mean the output was reliable.

## Speed and memory

| System | Median seconds | p95 seconds | Peak allocated MiB | Peak reserved MiB |
|---|---:|---:|---:|---:|
| Rule parser | 0.0001 | 0.0002 | 0 | 0 |
| Base model | 12.0833 | 16.3656 | 1482 | 2388 |
| Fine-tuned | 7.6831 | 10.9255 | 1515 | 2422 |

### Matched speed check

The base model's full test started on battery and finished on mains power, so its full-test timing isn't a fair comparison. To fix this, the last 20 test incidents were chosen in advance, after the laptop was plugged in, and both models were timed on them one at a time.

| System | Incidents | Median seconds | p95 seconds | Average answer length (tokens) |
|---|---:|---:|---:|---:|
| Base model | 20 | 8.7614 | 11.7684 | 94.0 |
| Fine-tuned | 20 | 7.2068 | 9.9702 | 55.4 |

Answer lengths also differed, so this timing check does not isolate per-token speed or establish a general latency advantage. The additional 107-incident run had a slower adapter median than the base model, as shown in the extended report. This is a small check on one laptop, not a production benchmark.

## Training runs

| Rank | Trainable parameters | Share of model | Minutes | Peak allocated / reserved MiB | Validation loss | Validation records fully correct | Validation finding F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 16 | 17,432,576 | 1.00% | 28.59 | 3575 / 11732 | 0.00080 | 0.993 | 0.998 |
| 8 | 8,716,288 | 0.50% | 30.40 | 3442 / 11216 | 0.00131 | 0.993 | 1.000 |

Rank 8 was selected using validation results only: fully correct records first, then finding F1, then valid-format rate, then validation loss. The test set played no part in the choice.

![Training loss for both runs](training_loss.png)

The plot uses a log scale. The trainer rounds logged loss values, so steps logged as exactly zero are left off.

## Data

Model `Qwen/Qwen3-1.7B`, maximum combined prompt and answer length 1,024 tokens.

Dataset SHA-256: `17c2c0ad5db69aba0f4ea38619ae371cd2a5450b7b2ea925039ce29638bf792b`

| Split | Real incidents | Swapped-name copies | Success | Failure | Unknown |
|---|---:|---:|---:|---:|---:|
| Training | 1330 | 269 | 57 | 1239 | 303 |
| Validation | 142 | 0 | 11 | 95 | 36 |
| Test | 93 | 0 | 10 | 54 | 29 |

| Finding | Training | Validation | Test |
|---|---:|---:|---:|
| invalid_user | 463 | 32 | 19 |
| password_authentication_failed | 1196 | 89 | 51 |
| authentication_accepted | 57 | 11 | 10 |
| too_many_authentication_failures | 250 | 24 | 12 |
| connection_closed | 1050 | 78 | 51 |
| insufficient_evidence | 201 | 30 | 20 |

Incidents over the length limit were removed rather than cut short. The splits are balanced across event types, so they don't reflect how often each event happens on a real server.

## Reading the numbers

- Evidence F1 compares cited lines with the reference line IDs. A model could cite a different line that also supports a finding and lose credit, so an additional citation-support metric based on the parser's event rules is saved in the JSON metrics. It is not independent verification of every claim in an answer.
- Malformed or wrongly structured answers count as failures, as do missing predictions. Correctly empty (`null`) fields count as correct.
- "F1 with code fences removed" strips Markdown fences from around an answer and nothing else. It doesn't repair JSON. The rule was fixed on validation data before the final test.
- Peak memory is PyTorch's allocated memory. Reserved memory includes PyTorch's cache, so it is higher. Neither is the GPU's total usage.
- Timing excludes model loading and a short warm-up. Test inference runs one incident at a time. Training time includes end-of-epoch validation and saving the adapter.
- p95 latency uses the nearest-rank method.

## Limits

- One lab server, a small test set, shared log templates and parser-made answers all limit how far these results stretch.
- Grouping lines by host, process ID and time gap is approximate. Stray lines can end up as their own incident after a disconnect.
- The findings describe events in the log. Intent and root cause are outside what is labeled.
