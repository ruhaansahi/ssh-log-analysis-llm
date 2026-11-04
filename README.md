# SSH Log Fine-Tuning

Evidence-grounded extraction of SSH authentication events using a small open-weight language model, completion-only supervised fine-tuning, and QLoRA.

The experiment compares an explicit rule baseline, the original model, and a trained adapter. It records annotation provenance, partition boundaries, evidence quality, and GPU costs. Rule-generated references are not independent expert ground truth.

## Environment

Python 3.12, an NVIDIA CUDA GPU, and `uv` are required for training. Resolve the pinned environment with `uv sync`, then use the project interpreter from `.venv` in VS Code.

Dataset: [Loghub OpenSSH](https://github.com/logpai/loghub/tree/master/OpenSSH). Data is intended for research/academic use; see the source repository for terms and citation.
