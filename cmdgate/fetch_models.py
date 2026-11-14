"""Download the gate's trained models, or publish them to the model repo.

    uv run --extra ml python -m cmdgate.fetch_models              # Kev adapter, n-gram models, Kev's base model
    uv run --extra ml python -m cmdgate.fetch_models --all        # also the encoders (about 5.8 GB)
    uv run --extra ml python -m cmdgate.fetch_models --publish <account>/<repo>

Weights are never committed. They live in one Hugging Face model repo whose
files sit at the paths they have in this checkout (`models/...`,
`runs/<run>/checkpoint/...`), so a download lands them in place. The repo id
and the exact revision are recorded in `cmdgate/models.json`, which is
committed: every checkout fetches the same bytes. A private repo needs
`hf auth login` first.

Kev serves an adapter on top of a base model. The base is fetched from its own
public repo at the revision the adapter was trained on and kept in the Hugging
Face cache, where Kev looks for it, so the server starts with the network off.

The cut-offs measured for these weights are in `cmdgate/policy.json`
(`python -m cmdgate.policy --check` compares them with what was downloaded).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MANIFEST = Path(__file__).with_name("models.json")
KEV_RUN = Path("runs/cmdrisk-08b-cap16-e1/checkpoint")
KEV_BASE = ("Qwen/Qwen3.5-0.8B-Base", "dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68")
LIGHT_FILES = ("models/ngram.joblib", "models/ngram_plus.joblib")
ENCODERS = ("encoder", "enc-mbl-cap8", "enc-qwenc-cap8", "enc-qwenc-cap8-s1", "enc-qwenc-cap8-s2", "enc-qwen-cap8")

CARD = """---
license: apache-2.0
---
# Command gate models

Models that read one shell command an AI coding agent wants to run and return the probability that a person
should approve it first. Files sit at the paths the project's checkout expects; fetch them with
`python -m cmdgate.fetch_models`.

- `runs/cmdrisk-08b-cap16-e1/checkpoint/`: a Kev adapter and head on Qwen/Qwen3.5-0.8B-Base (Apache-2.0),
  fine-tuned with [Kev](https://github.com/jaredpalmer/kev) (Apache-2.0).
- `models/enc-*`, `models/encoder`: text classifiers fine-tuned from Qwen/Qwen2.5-Coder-0.5B, Qwen/Qwen2.5-0.5B
  and answerdotai/ModernBERT (all Apache-2.0).
- `models/ngram.joblib`, `models/ngram_plus.joblib`: character n-gram logistic regressions.

Training data: [CLI Command Risk](https://huggingface.co/datasets/yannelli/cli-command-risk) v1.5 by Ryan Yannelli,
CC BY 4.0. Its labels were written and audited by language models.
"""


def patterns(everything: bool) -> list[str]:
    wanted = [f"{KEV_RUN.as_posix()}/*", *LIGHT_FILES]
    if everything:
        wanted += [f"models/{name}/*" for name in ENCODERS]
    return wanted


def publish(repo_id: str, private: bool) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, repo_type="model", private=private, exist_ok=True)
    api.upload_file(path_or_fileobj=CARD.encode(), path_in_repo="README.md", repo_id=repo_id)
    for path in LIGHT_FILES:
        api.upload_file(path_or_fileobj=path, path_in_repo=path, repo_id=repo_id)
    for folder in (KEV_RUN, *(Path("models") / name for name in ENCODERS)):
        if not folder.is_dir():
            raise SystemExit(f"{folder} is missing; nothing was recorded")
        print(f"uploading {folder}", flush=True)
        api.upload_folder(folder_path=str(folder), path_in_repo=folder.as_posix(), repo_id=repo_id)
    revision = api.model_info(repo_id).sha
    MANIFEST.write_text(json.dumps({"repo_id": repo_id, "revision": revision}, indent=2) + "\n")
    print(f"published {repo_id} at {revision}; recorded in {MANIFEST}")


def fetch(everything: bool, base: bool) -> None:
    from huggingface_hub import snapshot_download

    if not MANIFEST.exists():
        raise SystemExit(f"{MANIFEST} is missing: the models have not been published yet")
    source = json.loads(MANIFEST.read_text())
    snapshot_download(source["repo_id"], revision=source["revision"], allow_patterns=patterns(everything),
                      local_dir=".")
    print(f"fetched {source['repo_id']} at {source['revision'][:12]}")
    if base:
        snapshot_download(KEV_BASE[0], revision=KEV_BASE[1])
        print(f"cached Kev's base model {KEV_BASE[0]} at {KEV_BASE[1][:12]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="also fetch the encoders (about 5.8 GB)")
    parser.add_argument("--no-base", action="store_true", help="skip Kev's base model")
    parser.add_argument("--publish", metavar="REPO_ID", help="upload the local models to this model repo instead")
    parser.add_argument("--public", action="store_true", help="with --publish: create the repo public")
    args = parser.parse_args()
    if args.publish:
        publish(args.publish, private=not args.public)
    else:
        fetch(args.all, base=not args.no_base)


if __name__ == "__main__":
    main()
