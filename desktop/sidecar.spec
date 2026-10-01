# PyInstaller spec for the AI Tester engine (the Tauri app's sidecar).
# Build with: uv run python scripts/build_desktop.py
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP = ROOT / "backend" / "app"

datas = [
    (str(APP / "default-config.toml"), "app"),
    # Alembic loads migration scripts from files at runtime.
    (str(APP / "db" / "migrations"), "app/db/migrations"),
]
datas += collect_data_files("playwright")  # includes the Playwright driver

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite", "sqlalchemy.dialects.sqlite.pysqlite"]
)

a = Analysis([str(APP / "__main__.py")], pathex=[str(ROOT / "backend")], datas=datas,
             hiddenimports=hiddenimports, excludes=["pytest", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ai-tester-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="ai-tester-engine")
