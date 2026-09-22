"""Run the same WearVQA image/question pairs as the Galaxy E2B benchmark.

Run from the repository root after prepare_wearvqa_full.py. Results are
checkpointed after every question; a later run resumes successful samples.
"""

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import random
import statistics
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "routing/benchmarks/wearvqa_full/manifest.csv"
DEFAULT_PREFIX = ROOT / "routing/benchmarks/results/wearvqa_luna_full"
MODEL = "gpt-5.6-luna"
DETAIL = "original"
REASONING_EFFORT = "medium"
PROMPT_VERSION = "e2b_full_v1"

# The first nine fields match wearvqa_e2b_full_raw.csv exactly.
FIELDS = [
    "sample_id", "image", "question", "ground_truth", "category",
    "domain", "prediction", "latency_s", "error", "model",
    "image_detail", "reasoning_effort", "prompt_version", "image_sha256",
    "attempts", "input_tokens", "output_tokens", "response_id",
]
REQUIRED = {"sample_id", "image", "question", "ground_truth", "category", "domain"}
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
STOP_STATUS = {401, 403, 404, 429}


def project_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_local_env():
    """Load only simple KEY=VALUE entries from the repo-local .env.

    An already-exported environment variable wins. This avoids adding a
    runtime dependency just to read the one local secret used by this script.
    """
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def build_prompt(question):
    # Deliberately identical to run_wearvqa_e2b_full.py.
    return (
        f"{question}\n"
        "Answer the question directly and concisely. "
        "Do not add unnecessary explanation."
    )


def load_manifest(path):
    if not path.is_file():
        raise FileNotFoundError(
            f"Manifest not found: {path}. Run prepare_wearvqa_full.py first."
        )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Manifest is missing columns: {sorted(missing)}")
        rows = list(reader)

    seen = set()
    for row in rows:
        sample_id = row["sample_id"].strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f"Missing or duplicate sample_id: {sample_id!r}")
        if not row["question"].strip():
            raise ValueError(f"Empty question for sample_id {sample_id}")
        seen.add(sample_id)
    return rows


def select_rows(rows, sample_size, seed, sample_ids_from=None):
    if sample_ids_from is not None:
        with sample_ids_from.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if "sample_id" not in (reader.fieldnames or []):
                raise ValueError(f"Selection CSV has no sample_id column: {sample_ids_from}")
            ids = [row["sample_id"].strip() for row in reader]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Selection CSV has no IDs or has duplicate sample_id values")
        selected_ids = set(ids)
        missing = selected_ids - {row["sample_id"] for row in rows}
        if missing:
            raise ValueError(f"Selection IDs absent from full manifest: {sorted(missing)[:10]}")
        return [row for row in rows if row["sample_id"] in selected_ids]
    if sample_size is None:
        return rows
    if sample_size < 1 or sample_size > len(rows):
        raise ValueError(f"--sample-size must be between 1 and {len(rows)}")
    selected_ids = {
        row["sample_id"] for row in random.Random(seed).sample(rows, sample_size)
    }
    return [row for row in rows if row["sample_id"] in selected_ids]


def image_bytes(row):
    path = project_path(row["image"])
    if not path.is_file():
        raise FileNotFoundError(f"Image missing for sample_id {row['sample_id']}: {path}")
    return path.read_bytes()


def read_journal(path):
    latest = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                latest[str(record["sample_id"])] = record
            except (ValueError, KeyError) as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return latest


def check_journal(rows, latest):
    by_id = {row["sample_id"]: row for row in rows}
    for sample_id, record in latest.items():
        row = by_id.get(sample_id)
        if row is None:
            raise ValueError(f"Journal has sample_id {sample_id} absent from manifest")
        for key, expected in (
            ("image", row["image"]),
            ("question", row["question"]),
            ("ground_truth", row["ground_truth"]),
            ("model", MODEL),
            ("image_detail", DETAIL),
            ("reasoning_effort", REASONING_EFFORT),
            ("prompt_version", PROMPT_VERSION),
        ):
            if str(record.get(key)) != str(expected):
                raise ValueError(
                    f"Journal sample_id {sample_id} has different {key}; "
                    "use a new --output-prefix for a different run"
                )
        digest = hashlib.sha256(image_bytes(row)).hexdigest()
        if record.get("image_sha256") != digest:
            raise ValueError(
                f"Image changed for journal sample_id {sample_id}; "
                "use a new --output-prefix"
            )


def write_csv(path, rows, latest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            record = latest.get(row["sample_id"])
            if record is not None:
                writer.writerow({field: record.get(field) for field in FIELDS})


def retryable(error):
    status = getattr(error, "status_code", None)
    return status in RETRYABLE_STATUS or type(error).__name__ in {
        "APIConnectionError", "APITimeoutError"
    }


def ask_model(client, payload, prompt, timeout, max_retries, max_output_tokens):
    started = time.perf_counter()
    encoded = base64.b64encode(payload).decode("ascii")
    content = [
        {"type": "input_text", "text": prompt},
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{encoded}",
            "detail": DETAIL,
        },
    ]
    for attempt in range(1, max_retries + 2):
        try:
            response = client.responses.create(
                model=MODEL,
                input=[{"role": "user", "content": content}],
                reasoning={"effort": REASONING_EFFORT},
                max_output_tokens=max_output_tokens,
                store=False,
                timeout=timeout,
            )
            if getattr(response, "status", None) == "incomplete":
                raise ValueError(f"Model response incomplete: {response.incomplete_details}")
            answer = (response.output_text or "").strip()
            if not answer:
                raise ValueError("Model returned no text answer")
            usage = getattr(response, "usage", None)
            return {
                "prediction": answer,
                "latency_s": time.perf_counter() - started,
                "error": None,
                "attempts": attempt,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "response_id": getattr(response, "id", None),
            }, False
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if attempt <= max_retries and retryable(exc):
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            return {
                "prediction": "",
                "latency_s": None,
                "error": f"{type(exc).__name__}: {exc}",
                "attempts": attempt,
                "input_tokens": None,
                "output_tokens": None,
                "response_id": None,
            }, status in STOP_STATUS


def summarize(rows, latest, jsonl_path, csv_path):
    records = [latest[row["sample_id"]] for row in rows if row["sample_id"] in latest]
    successes = [r for r in records if not r.get("error")]
    latencies = sorted(float(r["latency_s"]) for r in successes)
    print(f"Results: {len(records)}/{len(rows)}; success: {len(successes)}; errors: {len(records) - len(successes)}")
    if latencies:
        p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)]
        print(
            f"Latency (successful calls): mean {statistics.mean(latencies):.2f}s, "
            f"median {statistics.median(latencies):.2f}s, P95 {p95:.2f}s"
        )
    print(f"JSONL: {jsonl_path}\nCSV:   {csv_path}")


def run(args, client=None):
    load_local_env()
    manifest = project_path(args.manifest)
    prefix = project_path(args.output_prefix)
    jsonl_path = prefix.with_name(prefix.name + "_raw.jsonl")
    csv_path = prefix.with_name(prefix.name + "_raw.csv")

    rows = load_manifest(manifest)
    selection_file = (
        project_path(args.sample_ids_from)
        if args.sample_ids_from is not None else None
    )
    selected = select_rows(rows, args.sample_size, args.seed, selection_file)
    latest = read_journal(jsonl_path)
    check_journal(rows, latest)
    for row in selected:
        image_bytes(row)  # Fail before any paid call if a selected image is missing.

    remaining = sum(
        1 for row in selected
        if row["sample_id"] not in latest or latest[row["sample_id"]].get("error")
    )
    print(f"Model: {MODEL}; image detail: {DETAIL}; reasoning: {REASONING_EFFORT}")
    print(f"Manifest: {manifest}; rows: {len(rows)}; selected: {len(selected)}; API calls remaining: {remaining}")
    if args.dry_run:
        print("Dry run: no API call or output file was written.")
        return

    if remaining and client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Put it in the repo .env file or export it in the shell."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the OpenAI SDK: python -m pip install openai") from exc
        client = OpenAI(max_retries=0, timeout=args.timeout)

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    stop_reason = None
    try:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            for row in selected:
                sample_id = row["sample_id"]
                if sample_id in latest and not latest[sample_id].get("error"):
                    continue
                payload = image_bytes(row)
                record = {field: row.get(field, "") for field in FIELDS[:6]}
                record.update({
                    "model": MODEL,
                    "image_detail": DETAIL,
                    "reasoning_effort": REASONING_EFFORT,
                    "prompt_version": PROMPT_VERSION,
                    "image_sha256": hashlib.sha256(payload).hexdigest(),
                })
                answer, stop = ask_model(
                    client, payload, build_prompt(row["question"]),
                    args.timeout, args.max_retries, args.max_output_tokens,
                )
                record.update(answer)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                latest[sample_id] = record
                print(f"{sample_id}: {'ERROR ' + record['error'] if record['error'] else 'OK'}", flush=True)
                if stop:
                    stop_reason = f"Stopping after API status {record['error']}"
                    break
    finally:
        write_csv(csv_path, rows, latest)
        summarize(selected, latest, jsonl_path, csv_path)
    if stop_reason:
        raise RuntimeError(stop_reason)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-prefix", default=str(DEFAULT_PREFIX))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--sample-size", type=int, help="Deterministic random pilot size")
    selection.add_argument("--sample-ids-from", help="CSV of sample_id values, e.g. the existing mini-50 manifest")
    parser.add_argument("--seed", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=2048,
        help="Maximum combined visible/reasoning output tokens per request",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without calling the API")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.max_retries < 0 or args.max_output_tokens < 1:
        parser.error(
            "--timeout must be positive, --max-retries nonnegative, "
            "and --max-output-tokens positive"
        )
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
