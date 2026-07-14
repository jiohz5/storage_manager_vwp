from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from pathlib import PurePosixPath


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "storage_manager_vwp-source.tar.gz"
CHECKSUM = OUTPUT.with_suffix(OUTPUT.suffix + ".sha256")
RUNTIME_FILES = [
    "app.py",
    "capacity_watch.py",
    "nightly_scan.py",
    "health_check.py",
    "storage_notifier.py",
    "runtime_check.py",
    "verify_environment.py",
    "run.csh",
    "setup_cron.csh",
    "README.md",
    "REVIEW.md",
    "FEATURE_ROADMAP.md",
    "VWP_ACCEPTANCE.md",
]
REQUIRED_SOURCE_MODULES = ("admin_auth.py", "search_index.py")
FORBIDDEN_ARCHIVE_DIRECTORIES = {
    "__pycache__",
    "data",
    "notifications",
    "reports",
}
FORBIDDEN_ARCHIVE_NAMES = {
    "accounts.json",
    "location.json",
    "runtime_diagnostics.json",
}
FORBIDDEN_ARCHIVE_SUFFIXES = (
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".lock",
    ".log",
)


def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.name.endswith(".csh") else 0o644
    return info


def validate_source_archive(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}

    unsafe = []
    for name in sorted(names):
        archive_path = PurePosixPath(name)
        lowered_parts = {part.lower() for part in archive_path.parts}
        basename = archive_path.name.lower()
        if (
            lowered_parts & FORBIDDEN_ARCHIVE_DIRECTORIES
            or basename in FORBIDDEN_ARCHIVE_NAMES
            or basename.endswith(FORBIDDEN_ARCHIVE_SUFFIXES)
        ):
            unsafe.append(name)
    if unsafe:
        raise ValueError(f"Runtime data found in source archive: {', '.join(unsafe)}")

    missing = [
        name
        for name in REQUIRED_SOURCE_MODULES
        if f"storage_manager_vwp/storage_manager/{name}" not in names
    ]
    if missing:
        raise ValueError(f"Required source module missing: {', '.join(missing)}")


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
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            archive.add(
                path,
                arcname=f"storage_manager_vwp/tests/{path.name}",
                filter=tar_filter,
            )
    validate_source_archive(OUTPUT)
    digest = hashlib.sha256()
    with OUTPUT.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    CHECKSUM.write_text(f"{digest.hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(f"Created {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print(f"Created {CHECKSUM}")


if __name__ == "__main__":
    main()
