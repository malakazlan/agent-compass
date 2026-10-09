"""Publish the HF cards and the benchmark files.

- model card  -> azlanmalikai/agent-compass-2b  (README.md and v1/README.md)
- data card   -> azlanmalikai/agent-compass-data (README.md)
- bench card + test files -> azlanmalikai/agent-compass-bench (README.md, test.jsonl, test_tau2.jsonl)

Visibility is not changed here. Every call retries because HF connections reset often from this box.

    uv run --python 3.12 --with huggingface_hub python scripts/publish_cards.py [--bench-files]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "docs" / "cards"
USER = "azlanmalikai"


def retry(fn, what: str, tries: int = 8):
    for i in range(tries):
        try:
            out = fn()
            print(f"ok   {what}", flush=True)
            return out
        except Exception as e:  # noqa: BLE001
            print(f"retry {what}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            time.sleep(5 * (i + 1))
    print(f"FAILED {what}", flush=True)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-files", action="store_true", help="also upload test.jsonl and test_tau2.jsonl to the bench repo")
    ap.add_argument("--staging", type=Path, default=ROOT / ".scratch" / "bench_upload")
    a = ap.parse_args()
    api = HfApi()

    model_card = (CARDS / "model_card_2b.md").read_bytes()
    for path in ("README.md", "v1/README.md"):
        retry(lambda p=path: api.upload_file(path_or_fileobj=model_card, path_in_repo=p, repo_id=f"{USER}/agent-compass-2b",
                                             repo_type="model", commit_message="Model card"), f"model card {path}")
    retry(lambda: api.upload_file(path_or_fileobj=str(CARDS / "dataset_card_data.md"), path_in_repo="README.md",
                                  repo_id=f"{USER}/agent-compass-data", repo_type="dataset", commit_message="Dataset card"), "data card")
    retry(lambda: api.upload_file(path_or_fileobj=str(CARDS / "dataset_card_bench.md"), path_in_repo="README.md",
                                  repo_id=f"{USER}/agent-compass-bench", repo_type="dataset", commit_message="Benchmark card"), "bench card")

    if a.bench_files:
        a.staging.mkdir(parents=True, exist_ok=True)
        for name in ("test.jsonl", "test_tau2.jsonl"):
            dst = a.staging / name
            if not dst.exists():
                shutil.copyfile(ROOT / "data" / "v1" / name, dst)
        # resumable: re-running continues where it stopped
        retry(lambda: api.upload_large_folder(repo_id=f"{USER}/agent-compass-bench", repo_type="dataset", folder_path=str(a.staging),
                                              allow_patterns=["*.jsonl"], print_report=False), "bench files", tries=20)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
