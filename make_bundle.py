from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "storage_manager_vwp-source.tar.gz"
CHECKSUM = OUTPUT.with_suffix(OUTPUT.suffix + ".sha256")
RUNTIME_FILES = [
    "app.py",
    "nightly_scan.py",
    "health_check.py",
    "verify_environment.py",
    "run.csh",
    "setup_cron.csh",
    "README.md",
    "REVIEW.md",
    "FEATURE_ROADMAP.md",
    "VWP_ACCEPTANCE.md",
]
def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.name.endswith(".csh") else 0o644
    return info


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUTPUT, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative in RUNTIME_FILES:
            archive.add(
                ROOT / relative,
                arcname=f"storage_manager_vwp/{relative}",
                filter=tar_filter,
            )
        for path in sorted((ROOT / "storage_manager").glob("*.py")):
            archive.add(
                path,
                arcname=f"storage_manager_vwp/storage_manager/{path.name}",
                filter=tar_filter,
            )
    digest = hashlib.sha256()
    with OUTPUT.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    CHECKSUM.write_text(f"{digest.hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(f"Created {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print(f"Created {CHECKSUM}")


if __name__ == "__main__":
    main()
