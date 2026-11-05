# Methodology

This document explains how the experiment was built and why each decision was made. The [README](../README.md) gives the short version.

## Goal

The goal is to turn an incident, a group of related SSH log lines that can include several authentication attempts, into a six-field JSON record: username, source IP, outcome, findings, evidence line IDs and server hostname. Missing or conflicting identity fields (`user`, `source_ip`, `server_hostname`) become `null`. The outcome is an enum and findings and evidence are nonempty lists.

The task covers what the logs show and nothing more. A failed password is recorded as a failed password. Whether it was an attack, a typo or a stolen credential can't be told from these lines, so the model isn't asked to decide. Two rules matter a lot in practice:

- A disconnect on its own doesn't mean authentication failed.
- If a login is accepted, the outcome is `success`, even if earlier attempts in the same incident failed.

## Data

The source is the [Loghub OpenSSH dataset](https://github.com/logpai/loghub/tree/master/OpenSSH), a set of real logs from a lab server. The download step stores the source URL and a SHA-256 checksum. The raw logs aren't committed to Git.

Log lines are grouped into incidents by server hostname and SSH process ID. Explicit SSH disconnect messages end an incident. An idle gap longer than 30 minutes or a backwards timestamp also starts a new group. This produced 157,611 candidate incidents; groups longer than 32 lines were excluded before sampling. These boundaries are an approximation of connections.

## Labels and review

Reference answers come from a rule-based parser that follows the [labeling rubric](ANNOTATION_RUBRIC.md). Automated checks confirm that every answer matches the schema, that each finding is supported by a line in the incident, that the outcome follows the precedence rules, and that every cited line exists.

Before training, all 100 selected test incidents were checked against their original log lines, using tool-assisted review alongside the automated checks. This was not an independent expert review. It found two parser bugs, both fixed before any model was scored:

1. An IPv4 address directly followed by a colon was not being extracted.
2. A generic PAM authentication failure was leaving the outcome as `unknown` when it should have been `failure`.

## Splits

Many incidents are near-copies of each other, because bots repeat the same attempt. A "family" matches the server hostname and ordered messages after normalizing port numbers. This comparison ignores timestamps and process IDs but retains usernames and IP addresses. One original representative per family is retained, leaving 5,729 families, and a fixed hash assigns each family to train, validation or test. A check confirms that no family appears in more than one split.

After that, each split is sampled so that each type of event is represented, since successful logins are rare compared with failures. This means the class mix doesn't match what a real server would see.

For training only, a fifth of the training incidents were copied with made-up usernames, IP addresses and hostnames swapped in. A copy was thrown away if the swap changed its findings or outcome. Each copy stays in the same split as the incident it came from. After the length filter described below, 269 of these copies remained.

Prompt and answer together had to fit in 1,024 tokens. Longer incidents were removed from every system's test set. They were never cut short, because cutting them could remove the very lines that prove a finding. The final splits were:

| Split | Incidents |
|---|---:|
| Training | 1,599 (1,330 real, 269 with swapped names) |
| Validation | 142 |
| Test | 93 |

## Training

The base model is Qwen3-1.7B, pinned to revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`. Training used PyTorch, Transformers, Datasets, PEFT and TRL on a 12 GB RTX 5070 Laptop GPU, on Windows with Python 3.12.

The method is QLoRA:

- The base model is loaded in 4-bit NF4 with double quantization and kept frozen.
- LoRA adapters are added to every attention projection (q, k, v, o) and MLP projection (gate, up, down). Only the adapters are trained.
- The loss is cross-entropy on the JSON answer and the end-of-answer token only. The prompt tokens don't count towards the loss.
- Computation runs in BF16.

Both runs used one epoch, a learning rate of 2e-4 with a cosine schedule and 5% warm-up, a micro-batch of 1 with 8 gradient accumulation steps, gradient checkpointing, a maximum length of 1,024 tokens and seed 42. One epoch is 200 optimizer steps.

Before the real runs, a five-step smoke test checks that the loss stays finite, that the base weights are frozen, that the adapter weights actually change, and that the saved adapter loads again and produces output. It isn't a benchmark.

## Choosing between runs

Two adapter sizes were trained:

| Rank | Trainable parameters | Share of model | Training time | Validation records fully correct | Validation finding F1 |
|---|---:|---:|---:|---:|---:|
| 16 | 17,432,576 | 1.00% | 28.59 min | 141 / 142 | 0.998 |
| 8 | 8,716,288 | 0.50% | 30.40 min | 141 / 142 | 1.000 |

The choice was made on validation results only, in this order: fully correct records, then finding F1, then valid-format rate, then validation loss. Both runs tied on the first, so rank 8 won on finding F1. Rank 16 had the lower validation loss, which shows that the lowest loss doesn't always give the best task score.

Rank 8 was not faster to train, despite being smaller. These were single runs on a laptop, so the timing difference can't be pinned on rank alone.

## Evaluation

The rule parser, the base model and the fine-tuned model are all scored on the same frozen test incidents. Because the parser also wrote the reference answers, its perfect score is expected. It shows that the labels are consistent with the rules, not that the rules are perfect.

Both models use the same prompt, the same 4-bit quantization, Qwen's non-thinking chat template, greedy decoding and a 256-token answer limit. The reference answer is never part of the input. Validation inference ran in batches of four, so its timing is an average per incident. Final test inference used a batch size of one, so timing measures sequential model generation per incident, not end-to-end service latency. Greedy decoding disables random sampling but does not guarantee bit-for-bit repeatability across platforms or library versions; see [PyTorch's reproducibility notes](https://docs.pytorch.org/docs/2.8/notes/randomness.html).

What gets measured:

- Precision, recall and F1 for findings, overall and for each finding type
- Accuracy for each field
- Exact-record accuracy, where all six fields must be right
- Evidence precision and recall against the reference line IDs
- Invalid-output rate and unsupported-finding rate
- Generation latency and GPU memory

Malformed output, output that breaks the schema and missing predictions all count as failures. Duplicate prediction IDs are rejected. The order of findings and evidence lines doesn't matter.

There is also a secondary score that strips Markdown code fences from around an answer and nothing else. It doesn't repair broken JSON or dig answers out of surrounding text. It was defined on validation data before the final test, so that formatting problems and content problems can be told apart. The main score still counts fenced answers as wrong.

Evidence is matched against the reference line IDs. A model could cite a different line that also supports a finding and lose credit for it, so an additional supported-citation rate is recorded using the parser's event rules. That metric is an automated rule check, not an independent judgment that a citation supports every field or the outcome.

The laptop switched from battery to mains power partway through the full test, which affects timing. To get a fair speed comparison, the last 20 test incidents were picked in advance, after the switch, and both models were timed on them at batch size one. Model loading and a short warm-up are excluded from timing. PyTorch's allocated and reserved GPU memory are reported separately.

Training parameters, dataset fingerprints, logged losses, validation results, resource summaries and the original and extended comparison reports are recorded in a local MLflow store. Individual CLI commands are not all separate tracked runs.

## The 200-incident extension

After the original experiment was finished, 107 more test incidents were added to make 200. Nothing was retrained or reselected, and the original data, predictions and model files were left untouched. A check confirms they still match their saved hashes.

The new incidents came from the same reserved test families, using the same grouping and family rules. Every family already used in training, validation or the original test set was excluded, including ones later removed for length. That left 540 unused families. 13 were over the token limit, leaving 527. The original sampler and seed then picked 107, and their IDs were recorded before any model saw them.

All 107 were checked against their source log lines before inference. None needed correcting. The new incidents contain 100 failures and 7 unknown outcomes, and no successful logins, so the test still has only 10 successful-login cases in total.

The base model, the rank-8 adapter and the parser were run on the same 107 incidents with identical settings. The original 93 predictions were reused. Combined scores are calculated from all 200 individual records, not by averaging the two groups. Predictions are saved after every incident, and a resumed run only continues if the data, model, adapter, prompt and decoding settings all match.

See the [200-incident report](../reports/EXTENDED_TEST_200.md) for the full tables.

## Error analysis and limits

On the original 93 test incidents, the fine-tuned model got all six fields right every time, with finding F1 of 1.000 and no invalid output.

The base model wrapped 76 of its 93 answers in Markdown code fences. Its strict finding F1 was 0.257, rising to 0.615 once only the fences were removed. Its remaining errors were real content mistakes: reporting "too many authentication failures" when the log didn't say so, missing disconnects, and citing the wrong lines.

On the 107 new incidents, the fine-tuned model got 105 records fully right. Finding F1, evidence F1, outcome accuracy and format validity were all perfect, but two fields were wrong:

- One username was copied incorrectly.
- In one incident, a destination address from a `connect_to` error was reported as the source IP, when the source should have been left empty.

Across all 200 incidents, that gives 198 fully correct records (99%), finding F1 of 1.000 and no invalid output. The base model scored 0.265 strict F1 overall, or 0.664 with fences removed. These two errors are a good example of why exact-record accuracy matters: finding F1 alone would have shown a perfect score.

On validation, both adapters missed the same record, which involved a PAM summary line. Rank 16 put a reverse-DNS hostname into `source_ip`, which breaks the rule that it must be an IP address or `null`. Rank 8 left the IP empty correctly but missed the username. Neither case led to any change in references or prompts.

The main limit is that everything comes from one historical server with one hostname. Log templates, and some usernames and IP addresses, appear in more than one split. Keeping the defined families separate prevents overlap under that specific matching rule, but does not rule out other semantic similarities or make this a test on new templates, new users or new servers. The sampling is also balanced rather than true to real-world frequencies.

The next steps follow from that:

1. Test on logs from other sources and newer OpenSSH message formats, with answers labeled independently of the parser.
2. Add a few-shot prompting baseline to see how much of the gain needs fine-tuning at all.
3. Compare model size, learning rate and training-set size on that harder test set.

For a single fixed log format, the rule parser remains a practical choice. The value of a fine-tuned model would come from handling formats a parser wasn't written for.
