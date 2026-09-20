"""python -m bench <run-config.json> [--validate] [--backends PATH] [--set KEY=VALUE ...]

Run from the project root (StreamMeCo-consolidation/):

    python -m bench bench/configs/runs/jake_path2_gemini.json
    python -m bench bench/configs/runs/jake_path1_qwen.json --validate
    python -m bench bench/configs/runs/jake_path2_gemini.json \
        --set memory_backend=qwen-vllm --set consolidation_backend=gpt-consol
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _overrides(pairs):
    overrides = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        if not value:
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        overrides[key] = value
    return overrides


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bench", description=__doc__)
    parser.add_argument("run_config")
    parser.add_argument("--backends", default=None,
                        help="override bench/configs/backends.json")
    parser.add_argument("--validate", action="store_true",
                        help="resolve config and report; do not run")
    parser.add_argument("--set", dest="overrides", action="append", metavar="KEY=VALUE",
                        help="override a run-config key (path, memory_backend, ...)")
    args = parser.parse_args(argv)

    for entry in (ROOT, ROOT / "StreamMeCo"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))

    from bench import config as bench_config
    config = bench_config.load_run(Path(args.run_config).resolve(), args.backends,
                                   overrides=_overrides(args.overrides))

    # Pristine modules read configs/*.json relative to the CWD.
    os.chdir(ROOT / "StreamMeCo")

    if args.validate:
        print(json.dumps(bench_config.summarize(config), indent=2))
        if not config["dataset"]["clips"]:
            print("WARNING: dataset resolved to 0 clips", file=sys.stderr)
        return

    from bench import runner
    runner.run(config)


if __name__ == "__main__":
    main()
