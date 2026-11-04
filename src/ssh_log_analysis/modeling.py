"""Local GPU loading, completion-only QLoRA, inference, and experiment tracking."""

import gc
import json
import math
import os
from pathlib import Path
import platform
import time

from .prompts import messages
from .storage import digest, read_jsonl, write_json, write_jsonl

# Pin the default model and tokenizer to the revision used in the measured run.
MODEL_REVISIONS = {"Qwen/Qwen3-1.7B": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"}


def configure_cache():
    os.environ.setdefault("HF_HOME", str(Path(".cache/huggingface").resolve()))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("MLFLOW_TRACKING_URI", Path("mlruns").resolve().as_uri())


def environment():
    configure_cache()
    import importlib.metadata
    import torch
    import psutil
    packages = {}
    for name in ("torch", "transformers", "datasets", "peft", "trl", "bitsandbytes", "mlflow"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result = {"python": platform.python_version(), "platform": platform.platform(), "packages": packages,
              "cuda_available": torch.cuda.is_available(), "torch_cuda_version": torch.version.cuda}
    memory = psutil.virtual_memory()
    result.update(ram_total_gib=memory.total/1024**3, ram_available_gib=memory.available/1024**3)
    if result["cuda_available"]:
        free, total = torch.cuda.mem_get_info()
        result.update(gpu=torch.cuda.get_device_name(), compute_capability=torch.cuda.get_device_capability(),
                      memory_total_mib=total/1024**2, memory_free_mib=free/1024**2,
                      bf16_supported=torch.cuda.is_bf16_supported())
    return result


def tokenizer_for(model_id):
    configure_cache()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=MODEL_REVISIONS.get(model_id), trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    return tokenizer


def prompt_text(tokenizer, row):
    return tokenizer.apply_chat_template(messages(row), tokenize=False, add_generation_prompt=True, enable_thinking=False)


def pair_for(tokenizer, row):
    return {"prompt": prompt_text(tokenizer, row),
            "completion": json.dumps(row["answer"], separators=(",", ":")) + tokenizer.eos_token}


def filter_data(model_id="Qwen/Qwen3-1.7B", max_length=1024, root="data"):
    tokenizer = tokenizer_for(model_id)
    source = Path(root) / "processed"
    destination = Path(root) / "filtered" / f"{model_id.replace('/', '--')}-{max_length}"
    counts, excluded, hashes = {}, {}, {}
    for split in ("train", "validation", "test"):
        keep, drop = [], []
        for row in read_jsonl(source / f"{split}.jsonl"):
            pair = pair_for(tokenizer, row)
            length = len(tokenizer(pair["prompt"] + pair["completion"], add_special_tokens=False)["input_ids"])
            (keep if length <= max_length else drop).append(row)
        if not keep:
            raise ValueError(f"No {split} examples fit {max_length} tokens")
        write_jsonl(destination / f"{split}.jsonl", keep)
        counts[split], excluded[split] = len(keep), [r["id"] for r in drop]
        hashes[split] = digest(keep)
    manifest = {"model": model_id, "max_length": max_length, "folder": str(destination),
                "counts": counts, "excluded_ids": excluded, "partition_hashes": hashes,
                "dataset_sha256": digest(hashes), "source_manifest": json.loads((source / "manifest.json").read_text())}
    write_json(destination / "manifest.json", manifest)
    write_json(Path(root) / "active.json", manifest)
    return manifest


def active_data(root="data"):
    manifest = json.loads((Path(root) / "active.json").read_text(encoding="utf-8"))
    return Path(manifest["folder"]), manifest


def load_model(model_id):
    configure_cache()
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU unavailable; use env-check to diagnose the installation")
    torch.set_num_threads(4)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                    bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(model_id, quantization_config=quantization,
                                                revision=MODEL_REVISIONS.get(model_id),
                                                dtype=dtype, device_map={"": 0},
                                                attn_implementation="sdpa", trust_remote_code=False)
    return model, dtype


def smoke_rows():
    from .data import make_record, sessionize
    text = ("Nov 04 00:00:00 lab sshd[101]: Invalid user backup-agent from 192.0.2.10\n"
            "Nov 04 00:00:01 lab sshd[101]: Failed password for invalid user backup-agent from 192.0.2.10 port 1234 ssh2\n"
            "Nov 04 00:00:02 lab sshd[101]: Connection closed by 192.0.2.10 [preauth]\n"
            "Nov 04 00:00:03 lab sshd[102]: Accepted publickey for deploy from 192.0.2.11 port 1235 ssh2\n"
            "Nov 04 00:00:04 lab sshd[102]: Received disconnect from 192.0.2.11: 11: Bye Bye [preauth]")
    return [make_record(lines) for lines in sessionize(text)[0]]


def train(config, root="data", smoke=False):
    configure_cache()
    if smoke:
        config = {**config, "rank": 8, "gradient_accumulation_steps": 1}
    import mlflow
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import TrainerCallback, set_seed
    from trl import SFTConfig, SFTTrainer
    set_seed(config["seed"])
    torch.set_num_threads(4)
    model_id = config["model"]
    tokenizer = tokenizer_for(model_id)
    output = Path("artifacts") / ("smoke" if smoke else f"rank-{config['rank']}")
    if smoke:
        train_rows, val_rows = smoke_rows() * 4, smoke_rows()
        manifest = {"dataset_sha256": "smoke-only-not-a-benchmark"}
    else:
        folder, manifest = active_data(root)
        if manifest["model"] != model_id or manifest["max_length"] != config["max_length"]:
            raise ValueError("Run filter-data with the matching model and maximum length first")
        train_rows, val_rows = read_jsonl(folder / "train.jsonl"), read_jsonl(folder / "validation.jsonl")
    model, dtype = load_model(model_id)
    revision = getattr(model.config, "_commit_hash", None)
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    adapter_config = LoraConfig(r=8 if smoke else config["rank"], lora_alpha=16 if smoke else config["rank"]*2,
                               lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                               target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, adapter_config)
    trainable, total = model.get_nb_trainable_parameters()
    base_frozen = all(not p.requires_grad for name, p in model.named_parameters() if "lora_" not in name)
    tracked_name, tracked = next((name, p) for name, p in model.named_parameters() if "lora_B" in name)
    before = tracked.detach().float().cpu().clone()
    records = []
    class TrackingCallback(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in (logs or {}).values()):
                raise RuntimeError("Non-finite training or validation metric")
            numeric = {k: float(v) for k, v in (logs or {}).items() if isinstance(v, (int, float)) and math.isfinite(v)}
            records.append({"step": state.global_step, **numeric})
            mlflow.log_metrics(numeric, step=state.global_step)
    args = SFTConfig(output_dir=str(output / "checkpoints"),
                     num_train_epochs=config["epochs"], max_steps=5 if smoke else -1,
                     per_device_train_batch_size=config["microbatch_size"], per_device_eval_batch_size=1,
                     gradient_accumulation_steps=1 if smoke else config["gradient_accumulation_steps"],
                     learning_rate=config["learning_rate"], max_length=config["max_length"],
                     completion_only_loss=True, packing=False, bf16=dtype == torch.bfloat16,
                     fp16=dtype == torch.float16, gradient_checkpointing=True,
                     gradient_checkpointing_kwargs={"use_reentrant": False},
                     optim="adamw_torch", lr_scheduler_type="cosine", warmup_ratio=0.05,
                     logging_steps=1 if smoke else 10, eval_strategy="no" if smoke else "epoch",
                     save_strategy="no" if smoke else "epoch", save_total_limit=1,
                     load_best_model_at_end=not smoke, metric_for_best_model="eval_loss",
                     greater_is_better=False, report_to="none", seed=config["seed"],
                     logging_nan_inf_filter=False,
                     dataloader_num_workers=0, dataset_num_proc=None, dataloader_pin_memory=False)
    trainer = SFTTrainer(model=model, args=args, processing_class=tokenizer,
                         train_dataset=Dataset.from_list([pair_for(tokenizer, row) for row in train_rows]),
                         eval_dataset=Dataset.from_list([pair_for(tokenizer, row) for row in val_rows]),
                         callbacks=[TrackingCallback()])
    mlflow.set_experiment("ssh-log-qlora")
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    with mlflow.start_run(run_name="smoke" if smoke else f"rank-{config['rank']}") as run:
        mlflow.log_params({**config, "smoke": smoke, "dataset_sha256": manifest["dataset_sha256"],
                           "model_revision": revision or "unknown", "precision": str(dtype),
                           "train_incidents": len(train_rows), "validation_incidents": len(val_rows)})
        result = trainer.train()
        changed = not torch.equal(before, dict(model.named_parameters())[tracked_name].detach().float().cpu())
        adapter_finite = all(torch.isfinite(p).all().item() for p in model.parameters() if p.requires_grad)
        if not base_frozen or not changed or not adapter_finite or not math.isfinite(result.training_loss):
            raise RuntimeError("Training verification failed: base freeze, adapter update, or finite loss")
        output.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(output / "adapter"))
        tokenizer.save_pretrained(str(output / "adapter"))
        elapsed = time.perf_counter() - start
        summary = {"config": config, "smoke": smoke, "model_revision": revision, "dataset_sha256": manifest["dataset_sha256"],
                   "train_incidents": len(train_rows), "validation_incidents": len(val_rows),
                   "training_loss": result.training_loss, "training_seconds": elapsed,
                   "peak_gpu_memory_mib": torch.cuda.max_memory_allocated()/1024**2,
                   "peak_reserved_memory_mib": torch.cuda.max_memory_reserved()/1024**2,
                   "trainable_parameters": trainable, "total_parameters": total,
                   "base_frozen": base_frozen, "adapter_changed": changed, "precision": str(dtype),
                   "adapter_finite": adapter_finite,
                   "best_validation_loss": trainer.state.best_metric, "mlflow_run_id": run.info.run_id,
                   "optimizer_steps": trainer.state.global_step,
                   "adapter": str(output / "adapter")}
        write_json(output / "training.json", summary)
        write_json(output / "history.json", records)
        mlflow.log_metrics({k: float(v) for k, v in summary.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)})
        mlflow.log_artifact(str(output / "training.json"))
        mlflow.log_artifact(str(output / "history.json"))
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    return summary


def verify_saved_adapter(summary):
    predictions, resources = infer_rows(summary["config"]["model"], smoke_rows(), summary["adapter"])
    if not all(prediction["raw"] for prediction in predictions):
        raise RuntimeError("Reloaded adapter produced empty output")
    summary["reload_verified"] = True
    write_json(Path(summary["adapter"]).parent / "training.json", summary)
    write_json(Path(summary["adapter"]).parent / "reload_check.json", {"predictions": predictions, "resources": resources})
    return summary


def infer_rows(model_id, rows, adapter=None, max_new_tokens=256, batch_size=1):
    import torch
    model, dtype = load_model(model_id)
    tokenizer = tokenizer_for(model_id)
    tokenizer.padding_side = "left"
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    model.config.use_cache = True
    torch.cuda.reset_peak_memory_stats()
    predictions = []
    # Exclude a fixed synthetic warm-up from timing and from the test benchmark.
    warmup = tokenizer(prompt_text(tokenizer, smoke_rows()[0]), return_tensors="pt").to("cuda")
    with torch.inference_mode():
        model.generate(**warmup, do_sample=False, max_new_tokens=4, pad_token_id=tokenizer.pad_token_id)
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset+batch_size]
        inputs = tokenizer([prompt_text(tokenizer, row) for row in batch], padding=True, return_tensors="pt").to("cuda")
        torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(**inputs, do_sample=False, max_new_tokens=max_new_tokens,
                                       pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        for index, row in enumerate(batch):
            response_ids = generated[index, inputs["input_ids"].shape[-1]:].tolist()
            raw = tokenizer.decode(response_ids, skip_special_tokens=True).strip()
            token_count = response_ids.index(tokenizer.eos_token_id)+1 if tokenizer.eos_token_id in response_ids else len(response_ids)
            predictions.append({"id": row["id"], "raw": raw, "latency_s": elapsed/len(batch),
                                "batch_latency_s": elapsed, "generated_tokens": token_count})
        if (offset + len(batch)) % 10 < batch_size or offset+len(batch) == len(rows):
            print(f"Inference: {offset+len(batch)}/{len(rows)} incidents (batch {batch_size})", flush=True)
    resources = {"peak_gpu_memory_mib": torch.cuda.max_memory_allocated()/1024**2,
                 "peak_reserved_memory_mib": torch.cuda.max_memory_reserved()/1024**2,
                 "model_revision": getattr(model.config, "_commit_hash", None), "precision": str(dtype),
                 "inference_batch_size": batch_size,
                 "timing_note": "Sequential generation latency" if batch_size == 1 else "Amortized batch generation time per incident; not single-request latency"}
    del model
    gc.collect()
    torch.cuda.empty_cache()
    return predictions, resources


def evaluate_model(root="data", split="test", adapter=None, output="reports/base", limit=None):
    from .evaluation import score_predictions
    folder, manifest = active_data(root)
    rows = read_jsonl(folder / f"{split}.jsonl")
    if limit:
        if split == "test":
            raise ValueError("Use the full final test set; use validation for exploratory subsets")
        rows = rows[:limit]
    predictions, resources = infer_rows(manifest["model"], rows, adapter, batch_size=4 if split == "validation" else 1)
    metrics, details = score_predictions(rows, predictions)
    metrics.update(resources, model=manifest["model"], adapter=adapter, split=split,
                   dataset_sha256=manifest["dataset_sha256"], system="adapter" if adapter else "original")
    write_json(str(output) + ".json", metrics)
    write_jsonl(str(output) + "_predictions.jsonl", predictions)
    write_jsonl(str(output) + "_failures.jsonl", [row for row in details if not row["exact_match"]])
    return metrics
