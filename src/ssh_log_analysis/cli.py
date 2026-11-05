"""Command-line entry point; heavy ML imports stay inside their commands."""

import argparse
import json
from pathlib import Path


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="SSH log fine-tuning experiment")
    parser.add_argument("--version", action="version", version="0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("env-check", help="Inspect Python, packages, CUDA, and memory")
    for command in ("prepare-test-extension", "evaluate-test-extension"):
        extension = commands.add_parser(command, help="Prepare or evaluate an additional held-out test cohort")
        extension.add_argument("--total", type=int, default=200)
    prepare = commands.add_parser("prepare", help="Download logs and construct deterministic partitions")
    prepare.add_argument("--source")
    prepare.add_argument("--sample", action="store_true")
    for command in ("validate", "audit"):
        commands.add_parser(command)
    filtering = commands.add_parser("filter-data", help="Freeze token-length-filtered experiment partitions")
    filtering.add_argument("--model", default="Qwen/Qwen3-1.7B")
    filtering.add_argument("--max-length", type=int, default=1024)
    for command in ("smoke", "train", "run-experiment"):
        training = commands.add_parser(command)
        training.add_argument("--config", default="configs/train.json")
        training.add_argument("--rank", type=int)
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--system", choices=("rules", "model"), required=True)
    evaluation.add_argument("--split", choices=("validation", "test"), default="validation")
    evaluation.add_argument("--adapter")
    evaluation.add_argument("--output", required=True)
    evaluation.add_argument("--limit", type=int)
    prediction = commands.add_parser("predict")
    prediction.add_argument("--file", required=True)
    prediction.add_argument("--adapter")
    prediction.add_argument("--model", default="Qwen/Qwen3-1.7B")
    commands.add_parser("report", help="Generate the measured comparison report")
    args = parser.parse_args()
    if args.command in ("prepare-test-extension", "evaluate-test-extension"):
        from .extension import prepare_extension, evaluate_extension
        emit(prepare_extension(args.total) if args.command == "prepare-test-extension" else evaluate_extension(args.total))
    elif args.command == "env-check":
        from .modeling import environment
        from .storage import write_json
        result = environment()
        write_json("reports/environment.json", result)
        emit(result)
    elif args.command == "prepare":
        from .data import prepare as prepare_data
        emit(prepare_data(source=args.source, sample=args.sample))
    elif args.command == "validate":
        from .data import validate_dataset
        emit(validate_dataset())
    elif args.command == "audit":
        from .data import export_audit
        emit(export_audit())
    elif args.command == "filter-data":
        from .modeling import filter_data
        emit(filter_data(args.model, args.max_length))
    elif args.command in ("smoke", "train", "run-experiment"):
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.rank:
            config["rank"] = args.rank
        if args.command == "run-experiment":
            from .experiment import run_experiment
            emit(run_experiment(config))
        else:
            from .modeling import train, verify_saved_adapter
            result = train(config, smoke=args.command == "smoke")
            if args.command == "smoke":
                result = verify_saved_adapter(result)
            emit(result)
    elif args.command == "evaluate":
        if args.system == "rules":
            from .evaluation import evaluate_rules
            from .modeling import active_data
            folder, _ = active_data()
            emit(evaluate_rules(split=args.split, output=args.output, dataset_folder=folder))
        else:
            from .modeling import evaluate_model
            emit(evaluate_model(split=args.split, adapter=args.adapter, output=args.output, limit=args.limit))
    elif args.command == "predict":
        from contextlib import redirect_stdout
        import sys
        from .data import make_record, sessionize
        from .evaluation import parse_prediction
        from .modeling import infer_rows
        sessions, skipped = sessionize(Path(args.file).read_text(encoding="utf-8"))
        rows = [make_record(lines) for lines in sessions]
        if not rows:
            parser.error("No supported OpenSSH log lines found in the input file")
        with redirect_stdout(sys.stderr):
            predictions, resources = infer_rows(args.model, rows, args.adapter)
        for row, prediction in zip(rows, predictions, strict=True):
            answer, is_json, errors = parse_prediction(prediction["raw"], row["lines"])
            prediction.update(answer=answer, valid_json=is_json, schema_valid=not errors, validation_errors=errors)
        emit({"predictions": predictions, "resources": resources, "unparsed_lines": skipped})
    elif args.command == "report":
        from .reporting import make_report
        emit(make_report())
