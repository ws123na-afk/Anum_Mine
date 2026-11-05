#!/usr/bin/env python3
"""Fail if sharp or libvips (LGPL-3.0-or-later) ends up in a shipped bundle (docs/licenses.md).

`sharp` and its `@img/sharp-*` libvips binaries are in the web app's production closure
only through kokoro-js -> @huggingface/transformers, and are Node-only. The reviewed
exception in scripts/licenses/exceptions.toml relies on them never shipping, so CI runs:

  assert_not_bundled.py --dist apps/web/dist --tauri-config apps/desktop/src-tauri/tauri.conf.json

--dist (repeatable): no file whose name or contents shows sharp or libvips (native addons,
shared libraries, or the packages' own module paths).
--tauri-config: the desktop bundle ships only the web dist and the Rust binary; it must not
add resources or sidecar binaries that pull sharp, libvips or node_modules in.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME = re.compile(r"(sharp|vips)[^/\\]*\.(node|so(\.\d+)*|dylib|dll|wasm|js|mjs|cjs)$|\.node$", re.I)
# Module paths and identifiers of the packages themselves; plain words such as "Sharpe ratio"
# or "csharp" do not match.
CONTENT = re.compile(
    rb"@img/sharp|sharp-libvips|libvips|node_modules/sharp|require\(\s*[\"']sharp[\"']\s*\)|"
    rb"from\s*[\"']sharp[\"']|import\(\s*[\"']sharp[\"']\s*\)"
)
FORBIDDEN_IN_CONFIG = re.compile(r"sharp|vips|node_modules", re.I)


def check_dist(directory: Path) -> list[str]:
    if not directory.is_dir():
        return [f"{directory}: not a directory (build the app first)"]
    problems = []
    files = [p for p in directory.rglob("*") if p.is_file()]
    if not files:
        return [f"{directory}: empty (build the app first)"]
    for path in files:
        relative = path.relative_to(directory)
        if NAME.search(path.name):
            problems.append(f"{directory}/{relative}: file name looks like sharp, libvips or a native addon")
            continue
        match = CONTENT.search(path.read_bytes())
        if match:
            problems.append(f"{directory}/{relative}: contains {match.group(0).decode(errors='replace')!r}")
    print(f"[licenses] {directory}: {len(files)} files checked for sharp and libvips")
    return problems


def check_tauri(config_path: Path) -> list[str]:
    config = json.loads(config_path.read_text())
    problems = []
    bundle = config.get("bundle", {})
    for key in ("resources", "externalBin"):
        value = bundle.get(key)
        if value and FORBIDDEN_IN_CONFIG.search(json.dumps(value)):
            problems.append(f"{config_path}: bundle.{key} would ship {value!r}")
    dist = config.get("build", {}).get("frontendDist", "")
    if FORBIDDEN_IN_CONFIG.search(str(dist)):
        problems.append(f"{config_path}: build.frontendDist points at {dist!r}")
    print(f"[licenses] {config_path}: bundle ships frontendDist {dist!r}, resources {bundle.get('resources')!r}, "
          f"externalBin {bundle.get('externalBin')!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", action="append", default=[], type=Path)
    parser.add_argument("--tauri-config", type=Path)
    args = parser.parse_args()
    if not args.dist and not args.tauri_config:
        parser.error("nothing to check")
    problems: list[str] = []
    for directory in args.dist:
        problems += check_dist(directory)
    if args.tauri_config:
        problems += check_tauri(args.tauri_config)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print("::error::sharp or libvips (LGPL-3.0-or-later) would ship. The license exception for them no "
              "longer holds: see docs/licenses.md.")
        return 1
    print("[licenses] no sharp or libvips in the shipped bundles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
