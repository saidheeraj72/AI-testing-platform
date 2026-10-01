"""Build the desktop app: React UI + PyInstaller engine + Tauri shell.

    uv run python scripts/build_desktop.py

Needs Node.js, and Rust (https://rustup.rs) for the Tauri shell.
Output: desktop/src-tauri/target/release/bundle/ (AI Tester.app and a .dmg on macOS).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    print(f"\n$ {' '.join(cmd)}   (in {cwd.relative_to(ROOT) or '.'})", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def main() -> int:
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    if not Path(cargo).exists():
        print("error: Rust is not installed. Install it from https://rustup.rs", file=sys.stderr)
        return 1
    env = {**os.environ, "PATH": f"{Path(cargo).parent}{os.pathsep}{os.environ['PATH']}"}
    # The desktop UI gets its API address and token from the shell at runtime, never at build time.
    ui_env = {k: v for k, v in env.items() if not k.startswith("VITE_API")}

    for folder in (ROOT / "frontend", ROOT / "desktop"):
        if not (folder / "node_modules").exists():
            run(["npm", "install"], folder, env)
    run(["npm", "run", "build"], ROOT / "frontend", ui_env)
    run([sys.executable, "-m", "PyInstaller", "desktop/sidecar.spec", "--noconfirm",
         "--distpath", "dist-sidecar", "--workpath", "build/sidecar"], ROOT, env)
    run(["npx", "tauri", "build"], ROOT / "desktop", env)

    bundle = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    if sys.platform == "darwin":
        # Without a developer certificate the bundle only carries the linker's ad-hoc signature, which
        # does not cover the bundled engine; macOS then refuses to open it. Sign the whole bundle ad hoc.
        app = bundle / "macos" / "AI Tester.app"
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)], ROOT, env)
        run(["codesign", "--verify", "--deep", "--strict", str(app)], ROOT, env)
        # A plain compressed disk image. (Tauri's styled DMG drives Finder by AppleScript, which
        # fails in non-interactive builds.)
        dmg = bundle / "dmg" / "AI Tester.dmg"
        dmg.parent.mkdir(exist_ok=True)
        run(["hdiutil", "create", "-volname", "AI Tester", "-srcfolder", str(bundle / "macos" / "AI Tester.app"),
             "-ov", "-format", "UDZO", str(dmg)], ROOT, env)
    print(f"\nDone. Bundles in {bundle.relative_to(ROOT)}:")
    for item in sorted(bundle.glob("*/*")):
        print(f"  {item.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
