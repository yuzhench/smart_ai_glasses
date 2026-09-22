from pathlib import Path
import argparse
import json
import os
import random
import time

import pandas as pd
from tqdm import tqdm
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[2]


def load_local_env():
    """Load simple KEY=VALUE entries from the repository .env file.

    An already-exported environment variable wins. Keeping this dependency
    free makes the grader behave like the Luna inference script without
    requiring python-dotenv.
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


load_local_env()

parser = argparse.ArgumentParser(description="Grade WearVQA model predictions")
parser.add_argument(
    "--input",
    default="routing/benchmarks/results/wearvqa_e2b_full_raw.csv",
    help="Raw prediction CSV (default: Galaxy E2B full run)",
)
parser.add_argument(
    "--output-prefix",
    default="routing/benchmarks/results/wearvqa_e2b_full",
    help="Prefix for _graded.jsonl and _graded.csv",
)
parser.add_argument(
    "--judge-model",
    default=os.environ.get("WEARVQA_JUDGE_MODEL", "gpt-5.6-luna"),
)
parser.add_argument(
    "--reasoning-effort",
    default=os.environ.get("WEARVQA_JUDGE_REASONING", "low"),
    choices=("low", "medium", "high"),
    help="Reasoning effort for the judge model",
)
parser.add_argument(
    "--max-output-tokens",
    type=int,
    default=256,
    help="Maximum judge output tokens",
)
parser.add_argument(
    "--sample-size",
    type=int,
    help="Judge only a deterministic pilot sample",
)
parser.add_argument("--seed", type=int, default=40)
args = parser.parse_args()


def project_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def comparable(value):
    return "" if pd.isna(value) else str(value)


def usage_value(usage, field):
    return getattr(usage, field, None) if usage is not None else None


INPUT = project_path(args.input)
prefix = project_path(args.output_prefix)
OUT_JSONL = prefix.with_name(prefix.name + "_graded.jsonl")
OUT_CSV = prefix.with_name(prefix.name + "_graded.csv")
OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
MODEL = args.judge_model
REASONING_EFFORT = args.reasoning_effort
MAX_OUTPUT_TOKENS = args.max_output_tokens

if MAX_OUTPUT_TOKENS < 1:
    parser.error("--max-output-tokens must be positive")

client = OpenAI()


def make_prompt(question, gt, pred):
    return f"""
You are grading the answer of a vision-language model.

Determine whether the MODEL ANSWER correctly answers the QUESTION,
using the REFERENCE ANSWER as the intended ground truth.

Important grading rules:
- Do NOT require exact wording.
- Paraphrases are correct.
- Equivalent numbers/units are correct.
- Extra harmless detail is allowed.
- If the model gives the wrong object, number, text, relation,
  activity, purpose, or conclusion, mark INCORRECT.
- The reference answer may be more verbose than necessary.
- If the model answer could reasonably be correct but the reference
  alone is insufficient to decide, mark AMBIGUOUS.
- Do not reward vague answers that fail to provide the requested fact.

QUESTION:
{question}

REFERENCE ANSWER:
{gt}

MODEL ANSWER:
{pred}

Return exactly one line of JSON in this format:
{{"label":"CORRECT","reason":"short reason"}}

label must be exactly one of:
CORRECT
INCORRECT
AMBIGUOUS
""".strip()


# ------------------------------------------------------------
# Resume
# ------------------------------------------------------------

completed = {}

if OUT_JSONL.exists():
    with OUT_JSONL.open(
        "r",
        encoding="utf-8",
    ) as f:
        for line in f:
            try:
                x = json.loads(line)
                if (
                    x.get("judge_label") in {"CORRECT", "INCORRECT", "AMBIGUOUS"}
                    or x.get("judge_error") == "model_prediction_unavailable"
                ):
                    completed[str(x["sample_id"])] = x
            except Exception:
                pass


df = pd.read_csv(INPUT)
if args.sample_size is not None:
    if args.sample_size < 1 or args.sample_size > len(df):
        parser.error(f"--sample-size must be between 1 and {len(df)}")
    # Stable sample for the pilot; keep the original input row order after
    # selecting IDs so the output is easy to inspect and resume.
    sample_indices = set(
        random.Random(args.seed).sample(range(len(df)), args.sample_size)
    )
    df = df.iloc[[i in sample_indices for i in range(len(df))]].copy()

print("=" * 80)
print("WEARVQA SEMANTIC GRADING")
print("=" * 80)
print("Model             :", MODEL)
print("Reasoning effort  :", REASONING_EFFORT)
print("Max output tokens :", MAX_OUTPUT_TOKENS)
print("Total             :", len(df))
print("Already completed :", len(completed))
print("Remaining         :", len(df) - len(completed))
print()


with OUT_JSONL.open(
    "a",
    encoding="utf-8",
) as fout:

    for _, row in tqdm(
        df.iterrows(),
        total=len(df),
        desc="Judging WearVQA",
    ):

        sid = str(row["sample_id"])

        previous = completed.get(sid)
        same_input = previous is not None and all(
            comparable(previous.get(key)) == comparable(row.get(key))
            for key in ("image", "question", "ground_truth", "prediction", "error")
        )
        # Older E2B grades did not record the judge model; its default was Luna.
        same_judge = previous is not None and (
            previous.get("judge_model") == MODEL
            and previous.get("judge_reasoning_effort") == REASONING_EFFORT
        )
        if same_input and same_judge:
            continue

        record = row.to_dict()
        record["judge_model"] = MODEL
        record["judge_reasoning_effort"] = REASONING_EFFORT
        record["judge_max_output_tokens"] = MAX_OUTPUT_TOKENS
        record["judge_latency_s"] = None
        record["judge_input_tokens"] = None
        record["judge_output_tokens"] = None
        record["judge_total_tokens"] = None
        record["judge_cached_input_tokens"] = None
        record["judge_reasoning_tokens"] = None

        question = str(row["question"])
        gt = str(row["ground_truth"])
        pred = str(row["prediction"])

        model_error = row.get("error")
        if (
            (pd.notna(model_error) and str(model_error).strip())
            or not pred.strip()
            or pred == "nan"
        ):
            record["judge_label"] = None
            record["judge_reason"] = None
            record["judge_error"] = "model_prediction_unavailable"
        else:
            started = time.perf_counter()
            try:
                response = client.responses.create(
                    model=MODEL,
                    input=make_prompt(
                        question,
                        gt,
                        pred,
                    ),
                    reasoning={"effort": REASONING_EFFORT},
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    store=False,
                )

                usage = getattr(response, "usage", None)
                input_details = getattr(usage, "input_tokens_details", None)
                output_details = getattr(usage, "output_tokens_details", None)
                record["judge_latency_s"] = time.perf_counter() - started
                record["judge_input_tokens"] = usage_value(usage, "input_tokens")
                record["judge_output_tokens"] = usage_value(usage, "output_tokens")
                record["judge_total_tokens"] = usage_value(usage, "total_tokens")
                record["judge_cached_input_tokens"] = usage_value(
                    input_details, "cached_tokens"
                )
                record["judge_reasoning_tokens"] = usage_value(
                    output_details, "reasoning_tokens"
                )

                text = response.output_text.strip()

                # tolerate accidental markdown fences
                text = (
                    text
                    .replace("```json", "")
                    .replace("```", "")
                    .strip()
                )

                parsed = json.loads(text)

                label = str(
                    parsed["label"]
                ).upper()

                if label not in {
                    "CORRECT",
                    "INCORRECT",
                    "AMBIGUOUS",
                }:
                    raise ValueError(
                        f"Unexpected label: {label}"
                    )

                record["judge_label"] = label
                record["judge_reason"] = str(
                    parsed.get("reason", "")
                )

                record["judge_error"] = None

            except Exception as e:
                record["judge_latency_s"] = time.perf_counter() - started
                record["judge_label"] = None
                record["judge_reason"] = None
                record["judge_error"] = (
                    f"{type(e).__name__}: {e}"
                )

        fout.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
            + "\n"
        )

        fout.flush()

        completed[sid] = record

        # gentle rate limiting
        time.sleep(0.05)


# ------------------------------------------------------------
# Rebuild CSV
# ------------------------------------------------------------

rows = []

with OUT_JSONL.open(
    "r",
    encoding="utf-8",
) as f:
    for line in f:
        try:
            rows.append(
                json.loads(line)
            )
        except Exception:
            pass


out = pd.DataFrame(rows)

out = (
    out
    .drop_duplicates(
        subset=["sample_id"],
        keep="last",
    )
    .reset_index(drop=True)
)

out.to_csv(
    OUT_CSV,
    index=False,
)


# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

print()
print("=" * 80)
print("WEARVQA GRADING SUMMARY")
print("=" * 80)

print("Cases:", len(out))

print()
print("Labels:")
print(
    out["judge_label"]
    .value_counts(
        dropna=False
    )
    .to_string()
)

valid = out[
    out["judge_label"].isin(
        ["CORRECT", "INCORRECT"]
    )
].copy()

if len(valid):
    acc = (
        valid["judge_label"]
        == "CORRECT"
    ).mean()

    print()
    print(
        f"E2B accuracy "
        f"(excluding ambiguous): "
        f"{acc*100:.2f}%"
    )

    print()
    print("Accuracy by category:")

    valid["correct"] = (
        valid["judge_label"]
        == "CORRECT"
    ).astype(int)

    table = (
        valid
        .groupby("category")
        ["correct"]
        .agg(["count", "mean"])
        .sort_values("mean")
    )

    table["mean"] *= 100

    print(table.to_string())


print()
print("Saved:")
print(OUT_CSV)
