"""Download the raw parquet shards of the M1 datasets into data/raw/<tag>/.

Only parquet files are fetched. Retries handle the connection resets we see against
the Hub. Re-running is a no-op for files already present.

    uv run --python 3.12 --with huggingface_hub python scripts/download_data.py [--only swe_agent,openhands]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from huggingface_hub import snapshot_download

DATASETS = {
    "swe_agent": "nebius/SWE-agent-trajectories",
    "openhands": "nebius/SWE-rebench-openhands-trajectories",
}
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def fetch(tag: str, repo_id: str, attempts: int = 6) -> Path:
    dest = RAW / tag
    dest.mkdir(parents=True, exist_ok=True)
    for i in range(attempts):
        try:
            path = snapshot_download(repo_id, repo_type="dataset", allow_patterns=["*.parquet"], local_dir=dest)
            files = sorted(Path(path).rglob("*.parquet"))
            size = sum(f.stat().st_size for f in files)
            print(f"{tag}: {len(files)} parquet files, {size / 1e9:.2f} GB at {path}")
            return Path(path)
        except Exception as e:  # network resets are routine here
            wait = 5 * (i + 1)
            print(f"{tag}: attempt {i + 1} failed ({type(e).__name__}: {str(e)[:120]}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"{tag}: download failed after {attempts} attempts")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated tags, default all")
    args = ap.parse_args()
    tags = [t for t in args.only.split(",") if t] or list(DATASETS)
    for tag in tags:
        fetch(tag, DATASETS[tag])


if __name__ == "__main__":
    main()
