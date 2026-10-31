"""Downloads a Chatterbox model (Nano or Turbo), the human voice, from Resemble AI's own page on
Hugging Face, and checks every file before Jarvis uses it. Run by macos/install_voice.sh with the
voice environment's Python:

    python download_model.py --repo ResembleAI/chatterbox-nano --dir .../voice-engine/model-nano

- It downloads one fixed version (commit) of the model. The first run picks the newest and
  writes it to source.json; later runs keep that version until you delete source.json.
- Only the file types Chatterbox needs: weights (.safetensors), its built-in voice (.pt, read
  with weights_only, so it can only hold numbers), and the tokenizer's text files.
- Every file is compared with the checksum Hugging Face lists for it (SHA-256 for the large
  files, the git hash for the small ones), and Hugging Face's own malware scan must not have
  flagged any of them.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import sys
from pathlib import Path

REPOS = {  # the weights each model needs; besides them only its voice (conds.pt) and tokenizer text files
    "ResembleAI/chatterbox-turbo": ("ve.safetensors", "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors"),
    "ResembleAI/chatterbox-nano": ("ve.safetensors", "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors"),
}
SMALL = ("conds.pt", "*.json", "*.txt", "*.model")
BAD_SCANS = ("unsafe", "suspicious", "infected")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_blob(path):
    """The hash git gives a file, which Hugging Face lists for the small (non-LFS) files."""
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def patterns(repo):
    return [*REPOS[repo], *SMALL]


def wanted(path, repo):
    name = path.rsplit("/", 1)[-1]
    return "/" not in path and not path.startswith(".") and any(fnmatch.fnmatch(name, p) for p in patterns(repo))


def expected_hash(entry):
    """("sha256" or "git", hash) for a file listed by the Hub."""
    lfs = getattr(entry, "lfs", None)
    if lfs:
        value = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        if value:
            return "sha256", value
    return "git", entry.blob_id


def scan_status(entry):
    security = getattr(entry, "security", None)
    if not security:
        return "not scanned"
    return str(security.get("status") if isinstance(security, dict) else getattr(security, "status", "")) or "?"


def verify(folder, entries):
    """Problems with the downloaded files: [] when every file matches its listed checksum."""
    problems = []
    for entry in entries:
        path = folder / entry.path
        kind, expected = expected_hash(entry)
        if not path.is_file():
            problems.append(f"{entry.path} is missing")
            continue
        actual = sha256(path) if kind == "sha256" else git_blob(path)
        if actual != expected:
            problems.append(f"{entry.path} doesn't match its checksum")
    return problems


def main(argv=None, api=None, download=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="ResembleAI/chatterbox-turbo", choices=sorted(REPOS))
    parser.add_argument("--dir", required=True, help="where the model goes")
    args = parser.parse_args(argv)
    repo = args.repo
    folder = Path(args.dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if api is None or download is None:
        from huggingface_hub import HfApi, snapshot_download

        api, download = api or HfApi(), download or snapshot_download

    record = folder / "source.json"
    try:
        chosen = json.loads(record.read_text(encoding="utf-8"))
        if chosen["repo"] != repo:
            raise ValueError("another model")
        revision = chosen["revision"]
        print(f"Using the version of {repo} chosen before: {revision}")
    except (OSError, ValueError, KeyError, TypeError):
        revision = api.model_info(repo).sha
        print(f"Newest version of {repo}: {revision}")

    entries = [e for e in api.list_repo_tree(repo, revision=revision, recursive=True, expand=True)
               if hasattr(e, "blob_id") and wanted(e.path, repo)]
    names = {e.path for e in entries}
    missing = [name for name in REPOS[repo] if name not in names]
    if missing:
        print(f"ERROR: the model page doesn't have {', '.join(missing)}; it may have changed. Nothing was installed.")
        return 1
    flagged = [f"{e.path} ({scan_status(e)})" for e in entries if scan_status(e) in BAD_SCANS]
    if flagged:
        print(f"ERROR: Hugging Face's malware scan flagged: {', '.join(flagged)}. Nothing was installed.")
        return 1
    size = sum(e.size for e in entries)
    print(f"{len(entries)} files, {size / 1e9:.1f} GB. Hugging Face's malware scan: "
          + ", ".join(sorted({scan_status(e) for e in entries})))

    download(repo, revision=revision, local_dir=str(folder), allow_patterns=sorted(names))
    problems = verify(folder, entries)
    if problems:
        for name in {e.path for e in entries}:
            if any(p.startswith(name + " ") for p in problems):
                (folder / name).unlink(missing_ok=True)  # never leave a file that failed its check
        print("ERROR: " + "; ".join(problems) + ". Run the installer again to download them again.")
        return 1
    record.write_text(json.dumps({"repo": repo, "revision": revision,
                                  "files": {e.path: expected_hash(e)[1] for e in entries}}, indent=2),
                      encoding="utf-8")
    if "conds.pt" not in names:
        print("Note: this version has no built-in voice; choose a recording in Jarvis's settings.")
    print(f"All {len(entries)} files match their checksums.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
