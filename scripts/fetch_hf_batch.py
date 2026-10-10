"""Rebuild a batch dir from a Hugging Face Dataset written by `societysim publish`.

  python scripts/fetch_hf_batch.py hf:owner/name[:batch_id] [--root runs]

Downloads data/jsonl/*.jsonl (logs and *.labels.jsonl) into <root>/<batch_id>/ and prints
that path. batch_id defaults to the dataset name. Public datasets need no token; a private
one uses HF_TOKEN from the environment. Never prints the token.
"""

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path


def parse_spec(spec: str) -> tuple[str, str]:
    """hf:owner/name[:batch_id] -> (repo_id, batch_id)."""
    body = spec.removeprefix("hf:")
    repo_id, _, batch = body.partition(":")
    if repo_id.count("/") != 1 or not all(repo_id.split("/")):
        raise ValueError(f"expected hf:<owner/name>[:<batch_id>], got {spec!r}")
    batch = batch or repo_id.split("/")[1]
    if "/" in batch or batch in (".", ".."):
        raise ValueError(f"bad batch id {batch!r}")
    return repo_id, batch


def fetch_batch(
    repo_id: str,
    batch_id: str,
    root: Path,
    token: str | None = None,
    download: Callable[..., str] | None = None,
) -> Path:
    """Copy the dataset's JSONL logs and labels into root/batch_id. Returns that dir."""
    if download is None:
        from huggingface_hub import snapshot_download as download
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(
            download(
                repo_id=repo_id,
                repo_type="dataset",
                allow_patterns=["data/jsonl/*.jsonl"],
                local_dir=tmp,
                token=token,
            )
        )
        files = sorted((snap / "data" / "jsonl").glob("*.jsonl"))
        if not any(not f.name.endswith(".labels.jsonl") for f in files):
            raise ValueError(f"no game logs under data/jsonl/ in {repo_id}")
        out = root / batch_id
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(f, out / f.name)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", help="hf:<owner/name>[:<batch_id>]")
    ap.add_argument("--root", type=Path, default=Path("runs"))
    args = ap.parse_args(argv)
    try:
        repo_id, batch = parse_spec(args.spec)
        print(fetch_batch(repo_id, batch, args.root, os.environ.get("HF_TOKEN") or None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
