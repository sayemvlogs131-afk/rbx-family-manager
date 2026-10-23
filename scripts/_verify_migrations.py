"""One-off verification: migration on old/new/simulated-old databases."""
import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path

import aiosqlite

from database.migrations import LATEST_VERSION, migrate

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "rbx_family.db"


def snapshot(path: Path) -> dict:
    con = sqlite3.connect(path)
    try:
        return {
            "version": con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
            "has_col": bool(
                con.execute(
                    "SELECT 1 FROM pragma_table_info('guild_settings') WHERE name='autoreply_enabled'"
                ).fetchone()
            ),
            "has_entries": bool(
                con.execute("SELECT 1 FROM sqlite_master WHERE name='autoreply_entries'").fetchone()
            ),
            "has_tmp": bool(
                con.execute("SELECT 1 FROM sqlite_master WHERE name='_tmp_autoreply_check'").fetchone()
            ),
            "rows": con.execute("SELECT COUNT(*) FROM guild_settings").fetchone()[0],
        }
    finally:
        con.close()


async def run(path: Path) -> int:
    async with aiosqlite.connect(path) as db:
        return await migrate(db)


def check(label: str, path: Path, expect_rows: int | None = None) -> None:
    snap = snapshot(path)
    assert snap["version"] == LATEST_VERSION, (label, snap)
    assert snap["has_col"], (label, snap)
    assert snap["has_entries"], (label, snap)
    assert not snap["has_tmp"], (label, snap)
    if expect_rows is not None:
        assert snap["rows"] == expect_rows, (label, snap)
    print(f"OK  {label}: {snap}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mig_"))
    try:
        # 1. Real production DB copy (already has the column, missing table, leftover tmp table)
        real = tmp / "real.db"
        shutil.copy(REAL_DB, real)
        before_rows = snapshot(real)["rows"]
        assert asyncio.run(run(real)) == LATEST_VERSION
        assert asyncio.run(run(real)) == LATEST_VERSION  # idempotent second run
        check("real DB copy (twice)", real, expect_rows=before_rows)

        # 2. Fresh database
        fresh = tmp / "fresh.db"
        assert asyncio.run(run(fresh)) == LATEST_VERSION
        check("fresh DB", fresh, expect_rows=0)

        # 3. Simulated old DB: at version 1, column absent, tmp table leftover
        old = tmp / "old.db"
        shutil.copy(REAL_DB, old)
        con = sqlite3.connect(old)
        con.execute("ALTER TABLE guild_settings DROP COLUMN autoreply_enabled")
        con.execute("DROP TABLE IF EXISTS autoreply_entries")
        con.execute("DROP TABLE IF EXISTS _tmp_autoreply_check")
        con.execute("DELETE FROM schema_migrations WHERE version > 1")
        con.commit()
        con.close()
        assert asyncio.run(run(old)) == LATEST_VERSION
        check("simulated v1 DB (no column)", old)

        print("ALL MIGRATION CHECKS PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
