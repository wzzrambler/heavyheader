"""Pin and fetch the third-party project used by the competition experiment.

Why a separate script instead of "just clone it":

* **A benchmark that cannot be re-fetched is not evidence.**  The exact tarball,
  tag and SHA-256 are recorded here, and the digest is verified before the tree
  is used.  A silently different upstream snapshot would invalidate every
  number in ``results/``.
* **No third-party Python packages.**  ``urllib`` + ``tarfile`` are enough, so
  the experiment runs in a bare container the same way the tool does.

The test-data repository is cloned via git because upstream publishes no
archive for it; its commit id is recorded in the manifest after cloning so the
corpus is pinned by commit rather than by branch name.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

__all__ = ["MANIFEST", "fetch", "main"]

WORK_DIR = Path(__file__).resolve().parent / "_work"

#: The pinned upstream snapshot.  Bump both fields together or not at all.
PINNED = {
    "name": "nlohmann/json",
    "tag": "v3.11.3",
    "url": "https://codeload.github.com/nlohmann/json/tar.gz/refs/tags/v3.11.3",
    "sha256": "0d8ef5af7f9794e3263480193c491549b2ba6cc74bb018906202ada498a79406",
    "archive": "json-v3.11.3.tar.gz",
    #: Test corpus: upstream keeps it in a separate repo, pinned by tag here.
    "test_data_url": "https://github.com/nlohmann/json_test_data.git",
    "test_data_tag": "v3.1.0",
}

MANIFEST = WORK_DIR / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    if dest.exists() and _sha256(dest) == PINNED["sha256"]:
        print(f"[fetch] archive already present and verified: {dest.name}")
        return
    print(f"[fetch] downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=180) as response, tmp.open("wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    actual = _sha256(tmp)
    if actual != PINNED["sha256"]:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"[fetch] SHA-256 mismatch for {url}\n  expected {PINNED['sha256']}\n"
            f"  actual   {actual}\n"
            "Refusing to use an unverified snapshot: every number in results/ "
            "would be attributable to an unknown tree."
        )
    tmp.replace(dest)
    print(f"[fetch] verified SHA-256 {actual[:16]}…")


def _extract(archive: Path, dest: Path) -> None:
    if (dest / "include" / "nlohmann" / "json.hpp").exists():
        print(f"[fetch] source tree already extracted: {dest}")
        return
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # strip-components=1: upstream tarballs wrap everything in one directory.
        for member in tar.getmembers():
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            member.name = parts[1]
            tar.extract(member, dest)
    print(f"[fetch] extracted to {dest}")


def _clone_test_data(dest: Path) -> str:
    if (dest / "README.md").exists():
        commit = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        print(f"[fetch] test data already present at {commit[:12]}")
        return commit
    print(f"[fetch] cloning test data (tag {PINNED['test_data_tag']}, ~365 MB)")
    subprocess.run(
        [
            "git",
            "clone",
            "-c",
            "advice.detachedHead=false",
            "--branch",
            PINNED["test_data_tag"],
            "--quiet",
            "--depth",
            "1",
            PINNED["test_data_url"],
            str(dest),
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def fetch() -> dict:
    """Ensure the pinned source tree and corpus exist; return the manifest."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    archive = WORK_DIR / PINNED["archive"]
    _download(PINNED["url"], archive)
    source = WORK_DIR / "json"
    _extract(archive, source)
    data_commit = _clone_test_data(WORK_DIR / "json_test_data")

    manifest = {
        "project": PINNED["name"],
        "tag": PINNED["tag"],
        "archive_sha256": PINNED["sha256"],
        "source_dir": str(source),
        "test_data_tag": PINNED["test_data_tag"],
        "test_data_commit": data_commit,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    manifest = fetch()
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
