from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import load_config, project_path

URLS = [
    "https://data.nasa.gov/docs/legacy/CMAPSSData.zip",
    "https://zenodo.org/records/15346912/files/CMAPSSData.zip?download=1",
]
EXPECTED_MD5 = "79a22f36e80606c69d0e9e4da5bb2b7a"
EXPECTED_FILES = {f"{kind}_FD00{i}.txt" for i in range(1, 5) for kind in ("train", "test", "RUL")}


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "aiot-cmapss-course-project/0.1"})
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zip_file:
        for member in zip_file.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"Unsafe path in dataset archive: {member.filename}")
        zip_file.extractall(destination)


def main() -> None:
    config = load_config()
    data_dir = project_path(config["project"]["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    if EXPECTED_FILES.issubset({path.name for path in data_dir.iterdir()}):
        print(f"C-MAPSS is already available in {data_dir}")
        return

    archive = data_dir / "CMAPSSData.zip"
    partial = archive.with_suffix(".zip.part")
    errors: list[str] = []
    for url in URLS:
        try:
            print(f"Downloading C-MAPSS from {url}")
            download(url, partial)
            partial.replace(archive)
            break
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            errors.append(f"{url}: {exc}")
            partial.unlink(missing_ok=True)
    else:
        print("Could not download C-MAPSS:\n- " + "\n- ".join(errors), file=sys.stderr)
        raise SystemExit(1)

    checksum = md5sum(archive)
    if checksum != EXPECTED_MD5:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"Dataset checksum mismatch: expected {EXPECTED_MD5}, got {checksum}")

    safe_extract(archive, data_dir)
    missing = EXPECTED_FILES - {path.name for path in data_dir.iterdir()}
    if missing:
        raise RuntimeError(f"Dataset archive is missing expected files: {sorted(missing)}")
    print(f"Extracted verified C-MAPSS dataset to {data_dir}")


if __name__ == "__main__":
    main()
