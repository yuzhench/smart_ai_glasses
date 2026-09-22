"""Regrade Luna's non-CORRECT WearVQA answers with an image-aware Sol judge.

Run a fixed pilot with --sample-size 25, then omit that flag to continue the
same journal through all 999 regrade candidates. Existing CORRECT labels are
copied from the source CSV and never sent to the API.
"""

import argparse
import base64
import csv
import hashlib
import json
import mimetypes
import os
import random
import re
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "routing/benchmarks/results/wearvqa_luna_sol_full_graded.csv"
DEFAULT_MANIFEST = ROOT / "routing/benchmarks/wearvqa_full/manifest.csv"
DEFAULT_PREFIX = ROOT / "routing/benchmarks/results/wearvqa_luna_sol_image_aware"
MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "medium"
LABELS = {"CORRECT", "PARTIAL", "INCORRECT", "AMBIGUOUS"}
SOURCE_LABELS = {"CORRECT", "INCORRECT", "AMBIGUOUS"}
TARGET_LABELS = {"INCORRECT", "AMBIGUOUS"}

# The rules and four labels match grade_wearvqa_e2b_image_aware.py from the
# small-model pipeline. Only the SMALL/LARGE wording is changed.
PROMPT_TEMPLATE = """You are evaluating a LARGE vision-language model used in wearable AI glasses.

You can see the actual image.

Your goal is NOT to require an exact match to the reference answer.
Determine whether the LARGE model's answer is sufficiently correct
and useful for the person wearing the glasses.

Use BOTH the image and the reference answer.

The reference is guidance, but it may be overly specific or describe
a visually ambiguous attribute.

Labels:

CORRECT
- The core information requested is correct.
- Minor omissions and harmless wording differences are okay.

PARTIAL
- The answer is substantially useful and close enough for the user,
  but contains a minor perceptual difference, missing detail, or imprecision.
- This includes visually similar colors, nearby fine-grained categories,
  approximate positions, or omitted non-essential details.
- PARTIAL means the answer remains usable for the wearer.

INCORRECT
- The core requested information is clearly wrong.
- It gives a clearly wrong count, object, action, relation, text,
  conclusion, or answers the wrong aspect of the question.

AMBIGUOUS
- The image itself does not allow a confident decision.
- Both answers appear visually plausible.
- The reference may itself be questionable.

Important:
- Do not mark INCORRECT merely because wording differs.
- If red/pink, green/yellow, or similar attributes are genuinely
  hard to distinguish from the image, prefer PARTIAL or AMBIGUOUS.
- Fine-grained category confusion can be PARTIAL if the answer remains useful.
- Do not use PARTIAL to excuse a clearly wrong core answer.

QUESTION:
{question}

REFERENCE ANSWER:
{ground_truth}

LARGE MODEL ANSWER:
{prediction}

Return exactly one JSON object:

{{"label":"PARTIAL","reason":"short reason"}}

label must be exactly one of:
CORRECT
PARTIAL
INCORRECT
AMBIGUOUS"""
PROMPT_SHA256 = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

OUTPUT_FIELDS = [
    "sample_id", "image", "question", "ground_truth", "category", "domain",
    "prediction", "luna_latency_s", "source_judge_label", "source_judge_reason",
    "source_judge_model", "source_judge_reasoning_effort", "regrade_status",
    "judge_label", "judge_reason", "judge_error", "review_status", "review_reason",
    "usable", "judge_model",
    "judge_reasoning_effort", "judge_max_output_tokens", "judge_latency_s",
    "judge_input_tokens", "judge_output_tokens", "judge_total_tokens",
    "judge_cached_input_tokens", "judge_reasoning_tokens", "judge_response_id",
]


def project_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_local_env():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def read_csv(path, required):
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        rows = list(reader)
    ids = [row["sample_id"].strip() for row in rows]
    if not ids or any(not sid for sid in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"{path} has empty or duplicate sample_id values")
    return rows


def load_inputs(source_path, manifest_path):
    required = {
        "sample_id", "question", "ground_truth", "prediction", "category",
        "domain", "judge_label", "judge_reason", "judge_model",
    }
    source = read_csv(source_path, required)
    manifest = read_csv(
        manifest_path, {"sample_id", "image", "question", "ground_truth"}
    )
    images = {row["sample_id"]: row for row in manifest}
    for row in source:
        sid = row["sample_id"]
        if row["judge_label"] not in SOURCE_LABELS:
            raise ValueError(f"Unexpected source judge_label for {sid}: {row['judge_label']!r}")
        manifest_row = images.get(sid)
        if manifest_row is None:
            raise ValueError(f"sample_id {sid} absent from manifest")
        for field in ("question", "ground_truth"):
            if row[field] != manifest_row[field]:
                raise ValueError(f"sample_id {sid} has different {field} in manifest")
        if row.get("image") and row["image"] != manifest_row["image"]:
            raise ValueError(f"sample_id {sid} has different image in manifest")
    return source, images


def candidate_rows(source):
    return [row for row in source if row["judge_label"] in TARGET_LABELS]


def select_candidates(candidates, sample_size, seed):
    if sample_size is None:
        return candidates
    if not 1 <= sample_size <= len(candidates):
        raise ValueError(f"--sample-size must be 1..{len(candidates)}")
    selected = set(random.Random(seed).sample(range(len(candidates)), sample_size))
    return [row for index, row in enumerate(candidates) if index in selected]


def image_payload(manifest_row):
    path = project_path(manifest_row["image"])
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return payload, mime


def source_signature(row):
    content = [row[field] for field in (
        "sample_id", "question", "ground_truth", "prediction", "judge_label"
    )]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def read_journal(path):
    latest = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                latest[str(record["sample_id"])] = record
            except (ValueError, KeyError) as exc:
                raise ValueError(f"Invalid journal line {path}:{number}") from exc
    return latest


def verify_journal(latest, candidates, images):
    by_id = {row["sample_id"]: row for row in candidates}
    for sid, record in latest.items():
        row = by_id.get(sid)
        if row is None:
            raise ValueError(f"Journal sample_id {sid} is not a regrade candidate")
        if record.get("source_signature") != source_signature(row):
            raise ValueError(f"Source row changed for sample_id {sid}; use another output prefix")
        if record.get("prompt_sha256") != PROMPT_SHA256:
            raise ValueError(f"Prompt changed for sample_id {sid}; use another output prefix")
        if record.get("judge_model") != MODEL or record.get("judge_reasoning_effort") != REASONING_EFFORT:
            raise ValueError(f"Judge config changed for sample_id {sid}; use another output prefix")
        payload, _ = image_payload(images[sid])
        if record.get("image_sha256") != hashlib.sha256(payload).hexdigest():
            raise ValueError(f"Image changed for sample_id {sid}; use another output prefix")


def make_prompt(row):
    return PROMPT_TEMPLATE.format(
        question=row["question"], ground_truth=row["ground_truth"],
        prediction=row["prediction"]
    )


def parse_label(output_text):
    text = (output_text or "").strip()
    if text.startswith("```json"):
        text = text[7:].strip()
    elif text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    parsed = json.loads(text)
    label = str(parsed["label"]).upper()
    if label not in LABELS:
        raise ValueError(f"Unexpected judge label: {label}")
    return label, str(parsed.get("reason", ""))


def get_usage(parent, name):
    return getattr(parent, name, None) if parent is not None else None


def human_review_reason(label, reason):
    """Flag unresolved answers and obvious image/reference conflicts.

    The judge's four-class label remains intact. This text check is deliberately
    conservative; human review may still find conflicts that it misses.
    """
    if label not in LABELS:
        return ""
    text = (reason or "").lower()
    reference_conflict = re.search(
        r"\b(?:reference|ground truth)\b.{0,100}\b"
        r"(?:wrong|incorrect|inaccurate|revers\w*|contradict\w*|"
        r"conflict\w*|questionable|mislabeled)\b",
        text,
    )
    model_supported = re.search(
        r"\b(?:model|answer)\b.{0,100}\b"
        r"(?:correct\w*|accurate\w*|matches|supported)\b",
        text,
    )
    if reference_conflict:
        return "label_reason_conflict" if label == "INCORRECT" and model_supported else "reference_image_conflict"
    if label == "AMBIGUOUS":
        return "ambiguous_image_or_reference"
    return ""


def judge_one(client, row, manifest_row, max_output_tokens, timeout):
    payload, mime = image_payload(manifest_row)
    record = {
        "sample_id": row["sample_id"],
        "source_signature": source_signature(row),
        "image_sha256": hashlib.sha256(payload).hexdigest(),
        "prompt_sha256": PROMPT_SHA256,
        "judge_model": MODEL,
        "judge_reasoning_effort": REASONING_EFFORT,
        "judge_max_output_tokens": max_output_tokens,
        "judge_label": None,
        "judge_reason": None,
        "judge_error": None,
    }
    encoded = base64.b64encode(payload).decode("ascii")
    started = time.perf_counter()
    try:
        response = client.responses.create(
            model=MODEL,
            reasoning={"effort": REASONING_EFFORT},
            max_output_tokens=max_output_tokens,
            store=False,
            timeout=timeout,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": make_prompt(row)},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"},
            ]}],
        )
        usage = getattr(response, "usage", None)
        input_details = get_usage(usage, "input_tokens_details")
        output_details = get_usage(usage, "output_tokens_details")
        record.update({
            "judge_input_tokens": get_usage(usage, "input_tokens"),
            "judge_output_tokens": get_usage(usage, "output_tokens"),
            "judge_total_tokens": get_usage(usage, "total_tokens"),
            "judge_cached_input_tokens": get_usage(input_details, "cached_tokens"),
            "judge_reasoning_tokens": get_usage(output_details, "reasoning_tokens"),
            "judge_response_id": getattr(response, "id", None),
        })
        if getattr(response, "status", None) == "incomplete":
            raise ValueError(f"Judge response incomplete: {response.incomplete_details}")
        record["judge_label"], record["judge_reason"] = parse_label(response.output_text)
    except Exception as exc:
        record["judge_error"] = f"{type(exc).__name__}: {exc}"
    record["judge_latency_s"] = time.perf_counter() - started
    return record


def merged_rows(source, latest):
    output = []
    for row in source:
        sid = row["sample_id"]
        prior = row["judge_label"]
        merged = {
            "sample_id": sid,
            "image": row.get("image", ""),
            "question": row["question"],
            "ground_truth": row["ground_truth"],
            "category": row["category"],
            "domain": row["domain"],
            "prediction": row["prediction"],
            "luna_latency_s": row.get("latency_s", ""),
            "source_judge_label": prior,
            "source_judge_reason": row.get("judge_reason", ""),
            "source_judge_model": row.get("judge_model", ""),
            "source_judge_reasoning_effort": row.get("judge_reasoning_effort", ""),
        }
        if prior == "CORRECT":
            merged.update({
                "regrade_status": "CARRIED_CORRECT",
                "judge_label": "CORRECT",
                "judge_reason": row.get("judge_reason", ""),
                "judge_error": "",
                "judge_model": row.get("judge_model", ""),
                "judge_reasoning_effort": row.get("judge_reasoning_effort", ""),
                "judge_max_output_tokens": row.get("judge_max_output_tokens", ""),
                "judge_latency_s": row.get("judge_latency_s", ""),
                "judge_input_tokens": row.get("judge_input_tokens", ""),
                "judge_output_tokens": row.get("judge_output_tokens", ""),
                "judge_total_tokens": row.get("judge_total_tokens", ""),
                "judge_cached_input_tokens": row.get("judge_cached_input_tokens", ""),
                "judge_reasoning_tokens": row.get("judge_reasoning_tokens", ""),
                "judge_response_id": "",
            })
        elif sid in latest:
            attempt = latest[sid]
            merged.update({key: attempt.get(key, "") for key in OUTPUT_FIELDS if key.startswith("judge_")})
            merged["regrade_status"] = "REGRADED" if attempt.get("judge_label") in LABELS else "ERROR"
        else:
            merged.update({"regrade_status": "PENDING", "judge_label": "", "judge_reason": "", "judge_error": ""})
        label = merged.get("judge_label")
        review_reason = human_review_reason(label, merged.get("judge_reason")) if merged["regrade_status"] == "REGRADED" else ""
        merged["review_reason"] = review_reason
        merged["review_status"] = "NEEDS_HUMAN_REVIEW" if review_reason else ""
        merged["usable"] = (
            "" if review_reason else
            "1" if label in {"CORRECT", "PARTIAL"} else
            "0" if label == "INCORRECT" else ""
        )
        output.append(merged)
    return output


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in OUTPUT_FIELDS})
    temporary.replace(path)


def summarize(source, selected, latest, output, jsonl_path):
    targets = candidate_rows(source)
    completed = sum(latest.get(row["sample_id"], {}).get("judge_label") in LABELS for row in targets)
    errors = sum(row["sample_id"] in latest and latest[row["sample_id"]].get("judge_label") not in LABELS for row in targets)
    print(f"Regrade candidates: {len(targets)}; completed: {completed}; errors: {errors}; pending: {len(targets) - completed - errors}")
    review_ids = [row["sample_id"] for row in output if row["review_status"] == "NEEDS_HUMAN_REVIEW"]
    print(f"Needs human review: {len(review_ids)}" + (f"; sample_ids: {', '.join(review_ids[:30])}" if review_ids else ""))
    pilot_labels = Counter(
        latest[row["sample_id"]]["judge_label"] for row in selected
        if row["sample_id"] in latest and latest[row["sample_id"]].get("judge_label") in LABELS
    )
    print(f"Selected sample labels: {dict(pilot_labels)}")
    selected_ids = {row["sample_id"] for row in selected}
    attempts = []
    if jsonl_path.exists():
        with jsonl_path.open(encoding="utf-8") as handle:
            attempts = [record for line in handle if line.strip()
                        if (record := json.loads(line))["sample_id"] in selected_ids]
    input_tokens = sum(record.get("judge_input_tokens") or 0 for record in attempts)
    output_tokens = sum(record.get("judge_output_tokens") or 0 for record in attempts)
    cached_tokens = sum(record.get("judge_cached_input_tokens") or 0 for record in attempts)
    latencies = sorted(record["judge_latency_s"] for record in attempts if record.get("judge_latency_s") is not None)
    print(f"Selected API usage across attempts: input {input_tokens:,} (cached {cached_tokens:,}); output {output_tokens:,}; attempts {len(attempts)}")
    cost = ((input_tokens - cached_tokens) * 4 + cached_tokens * .4 + output_tokens * 20) / 1_000_000
    print(f"Selected estimated API cost: ${cost:.4f} at $4/M input, $0.40/M cached, $20/M output")
    if latencies:
        import statistics
        from math import ceil
        print(f"Selected judge latency: mean {statistics.mean(latencies):.2f}s; median {statistics.median(latencies):.2f}s; P95 {latencies[ceil(.95 * len(latencies)) - 1]:.2f}s")
    if completed == len(targets):
        counts = Counter(row["judge_label"] for row in output)
        accepted = [row for row in output if not row["review_status"]]
        accepted_counts = Counter(row["judge_label"] for row in accepted)
        judgeable = accepted_counts["CORRECT"] + accepted_counts["PARTIAL"] + accepted_counts["INCORRECT"]
        print(f"Final raw labels (including review queue): {dict(counts)}")
        if judgeable:
            print(f"Usable rate (CORRECT + PARTIAL): {(accepted_counts['CORRECT'] + accepted_counts['PARTIAL']) / judgeable:.2%} over {judgeable} judgeable rows; human review excluded")
            print("Usable rate by category (AMBIGUOUS and human review excluded):")
            by_category = {}
            for row in accepted:
                if row["judge_label"] in {"CORRECT", "PARTIAL", "INCORRECT"}:
                    category = row["category"]
                    by_category.setdefault(category, [0, 0])
                    by_category[category][0] += row["judge_label"] in {"CORRECT", "PARTIAL"}
                    by_category[category][1] += 1
            for category, (usable, count) in sorted(
                by_category.items(), key=lambda item: item[1][0] / item[1][1]
            ):
                print(f"  {category}: {usable}/{count} ({usable / count:.2%})")


def run(args, client=None):
    source_path = project_path(args.input)
    manifest_path = project_path(args.manifest)
    prefix = project_path(args.output_prefix)
    jsonl_path = prefix.with_name(prefix.name + "_graded.jsonl")
    csv_path = prefix.with_name(prefix.name + "_graded.csv")
    if source_path == csv_path:
        raise ValueError("Output CSV must differ from source grading CSV")
    source, images = load_inputs(source_path, manifest_path)
    candidates = candidate_rows(source)
    selected = select_candidates(candidates, args.sample_size, args.seed)
    latest = read_journal(jsonl_path)
    verify_journal(latest, candidates, images)
    for row in selected:
        image_payload(images[row["sample_id"]])
    remaining = [row for row in selected if latest.get(row["sample_id"], {}).get("judge_label") not in LABELS]
    print(f"Model: {MODEL}; reasoning: {REASONING_EFFORT}; max_output_tokens: {args.max_output_tokens}")
    print(f"Source: {len(source)} rows; carried CORRECT: {len(source) - len(candidates)}; regrade candidates: {len(candidates)}")
    print(f"Selected: {len(selected)}; successful in journal: {len(selected) - len(remaining)}; API calls remaining: {len(remaining)}")
    if args.sample_size is not None:
        print("Selected sample_ids:", ", ".join(row["sample_id"] for row in selected))
    if args.dry_run:
        print("Dry run: no API calls or output files written.")
        return
    if remaining and client is None:
        load_local_env()
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is missing from environment and repo .env")
        from openai import OpenAI
        client = OpenAI(max_retries=0, timeout=args.timeout)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    stop = None
    try:
        with jsonl_path.open("a", encoding="utf-8") as handle:
            for row in remaining:
                record = judge_one(client, row, images[row["sample_id"]], args.max_output_tokens, args.timeout)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                latest[row["sample_id"]] = record
                label = record.get("judge_label") or "ERROR"
                reason = record.get("judge_error") or record.get("judge_reason") or ""
                print(f"{row['sample_id']}: {label} | {reason[:100]}", flush=True)
                if (record.get("judge_error") or "").startswith((
                    "APIConnectionError:", "APITimeoutError:", "AuthenticationError:",
                    "PermissionDeniedError:", "RateLimitError:",
                )):
                    stop = record["judge_error"]
                    break
    finally:
        output = merged_rows(source, latest)
        write_csv(csv_path, output)
        summarize(source, selected, latest, output, jsonl_path)
        print(f"JSONL: {jsonl_path}\nCSV: {csv_path}")
    if stop:
        raise RuntimeError(f"Stopped after API error: {stop}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-prefix", default=str(DEFAULT_PREFIX))
    parser.add_argument("--sample-size", type=int, help="Deterministic sample from INCORRECT and AMBIGUOUS rows")
    parser.add_argument("--seed", type=int, default=40)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.max_output_tokens < 1 or args.timeout <= 0:
        parser.error("--max-output-tokens and --timeout must be positive")
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
