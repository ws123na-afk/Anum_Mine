#!/usr/bin/env python3
"""Third-party license scan, THIRD_PARTY_NOTICES.md and per-app CycloneDX SBOMs.

See docs/licenses.md. Two steps:

  collect <ecosystem> --out FILE    list what one ecosystem ships, with license metadata
  report --inputs FILE...           apply scripts/licenses/policy.toml and exceptions.toml,
                                    then write or check THIRD_PARTY_NOTICES.md and write SBOMs

Collectors read each package manager's own resolution and metadata, so the result does not
depend on the operating system that runs the scan:

  python  pip's resolver (`pip install --dry-run --report`) for services/api, for CPython
          3.13 on manylinux x86_64 (the API image); licenses from the core metadata pip reports.
  npm     pnpm-lock.yaml: the production closure of each app (all platform variants);
          licenses from the npm registry manifest of the exact locked version.
  cargo   `cargo metadata --locked` for apps/desktop/src-tauri, every target platform,
          normal and build dependencies; licenses from each crate's Cargo.toml.
  dart    `flutter pub deps --json` for apps/mobile (non-dev closure); licenses read from
          the LICENSE file of each resolved package in the pub cache.
  gradle  Gradle's resolved releaseRuntimeClasspath of apps/android; licenses from the
          Maven POMs (Gradle cache first, then Google Maven and Maven Central).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
POLICY_FILE = HERE / "policy.toml"
EXCEPTIONS_FILE = HERE / "exceptions.toml"
NOTICES_FILE = ROOT / "THIRD_PARTY_NOTICES.md"

ECOSYSTEMS = ("python", "npm", "cargo", "dart", "gradle")
# Which apps ship each ecosystem's packages. The desktop installer bundles the web build.
APPS = {
    "api": "services/api (FastAPI image)",
    "web": "apps/web (browser bundle and web image)",
    "desktop": "apps/desktop (Tauri installer: Rust shell plus the web bundle)",
    "mobile": "apps/mobile (Flutter app)",
    "android": "apps/android (Kotlin app)",
}
PURL_TYPE = {"python": "pypi", "npm": "npm", "cargo": "cargo", "dart": "pub", "gradle": "maven"}
RANK = {"allowed": 0, "review": 1, "unknown": 2, "denied": 3}

USER_AGENT = "anum-license-scan (+https://github.com/ws123na-afk/Anum_Mine)"


# --------------------------------------------------------------------------- helpers


def log(message: str) -> None:
    print(f"[licenses] {message}", file=sys.stderr)


def http_get(url: str, *, attempts: int = 4, timeout: int = 30) -> bytes | None:
    """GET a URL; None on 404. Retries other failures with backoff."""
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https only)
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            last = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {attempts} attempts: {last}")


def run(cmd: list[str], *, cwd: Path | None = None) -> str:
    log("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:])
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result.stdout


def record(ecosystem: str, name: str, version: str, declared: str | None, license_expr: str | None,
           source: str | None, apps: list[str]) -> dict:
    return {
        "ecosystem": ecosystem,
        "name": name,
        "version": version,
        "declared": (declared or "").strip(),
        "license": license_expr,
        "source": clean_url(source),
        "apps": sorted(set(apps)),
    }


def clean_url(url: str | None) -> str:
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"^git\+", "", url)
    url = re.sub(r"^git://", "https://", url)
    url = re.sub(r"^ssh://git@", "https://", url)
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^http://", "https://", url)
    if not url.startswith("https://"):
        return ""
    return url.rstrip("/")


# --------------------------------------------------------------------------- license names

# Free-text license names seen in package metadata, mapped to SPDX ids. Keys are lowercased
# with punctuation collapsed by _key().
ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    "mit licence": "MIT",
    "expat": "MIT",
    "mit no attribution": "MIT-0",
    "apache 2": "Apache-2.0",
    "apache 2 0": "Apache-2.0",
    "apache v2": "Apache-2.0",
    "apache license 2 0": "Apache-2.0",
    "apache license v2 0": "Apache-2.0",
    "apache license version 2 0": "Apache-2.0",
    "the apache license version 2 0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache software license 2 0": "Apache-2.0",
    "apache software license version 2 0": "Apache-2.0",
    "the apache software license version 2 0": "Apache-2.0",
    "asl 2 0": "Apache-2.0",
    "bsd 3 clause": "BSD-3-Clause",
    "3 clause bsd license": "BSD-3-Clause",
    "bsd 3 clause license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "modified bsd license": "BSD-3-Clause",
    "revised bsd license": "BSD-3-Clause",
    "the 3 clause bsd license": "BSD-3-Clause",
    "bsd 2 clause": "BSD-2-Clause",
    "2 clause bsd license": "BSD-2-Clause",
    "simplified bsd license": "BSD-2-Clause",
    "the 2 clause bsd license": "BSD-2-Clause",
    "isc": "ISC",
    "isc license": "ISC",
    "mozilla public license 2 0": "MPL-2.0",
    "mozilla public license version 2 0": "MPL-2.0",
    "mpl 2 0": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
    "psf license": "PSF-2.0",
    "the unlicense": "Unlicense",
    "unlicense": "Unlicense",
    "cc0": "CC0-1.0",
    "cc0 1 0": "CC0-1.0",
    "zlib license": "Zlib",
    "boost software license 1 0": "BSL-1.0",
    "eclipse public license 1 0": "EPL-1.0",
    "eclipse public license v1 0": "EPL-1.0",
    "eclipse public license 2 0": "EPL-2.0",
    "eclipse public license v2 0": "EPL-2.0",
    "gnu lesser general public license v3": "LGPL-3.0-only",
    "gnu general public license v3": "GPL-3.0-only",
    "gnu affero general public license v3": "AGPL-3.0-only",
}

# Trove classifiers (the part after "License :: OSI Approved :: "). Ambiguous ones such as
# "BSD License" are left out on purpose: they need an override with evidence.
CLASSIFIERS = {
    "MIT License": "MIT",
    "MIT No Attribution License (MIT-0)": "MIT-0",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
}

# License URLs found in Maven POMs instead of (or alongside) names.
URL_ALIASES = [
    (r"apache\.org/licenses/license-2\.0", "Apache-2.0"),
    (r"opensource\.org/licenses/apache-2\.0", "Apache-2.0"),
    (r"opensource\.org/licenses/mit", "MIT"),
    (r"opensource\.org/licenses/bsd-3-clause", "BSD-3-Clause"),
    (r"opensource\.org/licenses/bsd-2-clause", "BSD-2-Clause"),
    (r"mozilla\.org/mpl/2\.0", "MPL-2.0"),
    (r"eclipse\.org/legal/epl-v10", "EPL-1.0"),
    (r"eclipse\.org/legal/epl-2\.0", "EPL-2.0"),
]

# Canonical spelling of SPDX ids (ids are case-insensitive; npm metadata sometimes says "mit").
CANONICAL_IDS = {i.lower(): i for i in [
    *ALIASES.values(), *CLASSIFIERS.values(), "0BSD", "BlueOak-1.0.0", "CDLA-Permissive-2.0", "OpenSSL",
    "PostgreSQL", "Python-2.0", "Unicode-3.0", "Unicode-DFS-2016", "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-or-later", "GPL-2.0-or-later", "GPL-3.0-or-later", "AGPL-3.0-or-later", "SSPL-1.0", "BUSL-1.1",
]}

SPDX_TOKEN = re.compile(r"^(?:LicenseRef-[A-Za-z0-9.\-]+|[A-Za-z0-9][A-Za-z0-9.\-]*\+?)$")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize(text: str | None) -> str | None:
    """Turn a declared license string into an SPDX expression, or None if unrecognised."""
    if not text:
        return None
    text = text.strip()
    if not text or "\n" in text or len(text) > 200:
        return None
    alias = ALIASES.get(_key(text))
    if alias:
        return alias
    for pattern, spdx in URL_ALIASES:
        if re.search(pattern, text.lower()):
            return spdx
    candidate = re.sub(r"\s*/\s*", " OR ", text)  # old Cargo style "MIT/Apache-2.0"
    try:
        tokens = tokenize(candidate)
    except ValueError:
        return None
    for token in tokens:
        if token in ("(", ")", "AND", "OR", "WITH"):
            continue
        if not SPDX_TOKEN.match(token) or token.lower() in ("see", "license", "in", "unlicensed"):
            return None
    try:
        parse(tokens)
    except ValueError:
        return None
    tokens = [CANONICAL_IDS.get(t.lower(), t) for t in tokens]
    if tokens[0] == "(" and tokens[-1] == ")":
        try:
            parse(tokens[1:-1])
            tokens = tokens[1:-1]  # "(MIT OR CC0-1.0)" -> "MIT OR CC0-1.0"
        except ValueError:
            pass
    # Upper/lower-case operators from npm ("mit or apache-2.0") are normalised.
    return " ".join(tokens).replace("( ", "(").replace(" )", ")")


def tokenize(expression: str) -> list[str]:
    tokens = re.findall(r"\(|\)|[^\s()]+", expression)
    if not tokens:
        raise ValueError("empty expression")
    return [t.upper() if t.upper() in ("AND", "OR", "WITH") else t for t in tokens]


def parse(tokens: list[str]):
    """Parse SPDX tokens into a tree: ("OR"|"AND", [children]) or ("ID", id) or ("WITH", id, exc)."""
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        token = peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        position += 1
        return token

    def primary():
        token = take()
        if token == "(":
            node = disjunction()
            if take() != ")":
                raise ValueError("missing )")
            return node
        if token in (")", "AND", "OR", "WITH"):
            raise ValueError(f"unexpected {token}")
        if peek() == "WITH":
            take()
            exception = take()
            return ("WITH", token, exception)
        return ("ID", token)

    def conjunction():
        nodes = [primary()]
        while peek() == "AND":
            take()
            nodes.append(primary())
        return nodes[0] if len(nodes) == 1 else ("AND", nodes)

    def disjunction():
        nodes = [conjunction()]
        while peek() == "OR":
            take()
            nodes.append(conjunction())
        return nodes[0] if len(nodes) == 1 else ("OR", nodes)

    tree = disjunction()
    if position != len(tokens):
        raise ValueError(f"unexpected {tokens[position]}")
    return tree


def license_ids(expression: str) -> list[str]:
    ids: list[str] = []

    def walk(node):
        if node[0] in ("AND", "OR"):
            for child in node[1]:
                walk(child)
        elif node[0] == "WITH":
            ids.append(f"{node[1]} WITH {node[2]}")
        else:
            ids.append(node[1])

    walk(parse(tokenize(expression)))
    return ids


# --------------------------------------------------------------------------- license texts

# Distinctive phrases of full license texts (used where a package ships only a LICENSE file).
TEXT_SIGNATURES = [
    ("AGPL-3.0", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.I)),
    ("LGPL-3.0", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3", re.I)),
    ("LGPL-2.1", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 2\.1", re.I)),
    ("GPL-3.0", re.compile(r"(?<!LESSER )(?<!AFFERO )GNU GENERAL PUBLIC LICENSE\s+Version 3", re.I)),
    ("GPL-2.0", re.compile(r"(?<!LESSER )GNU GENERAL PUBLIC LICENSE\s+Version 2,", re.I)),
    ("MPL-2.0", re.compile(r"Mozilla Public License,? (?:Version|v\.?) 2\.0", re.I)),
    ("Apache-2.0", re.compile(r"Apache License[\s,]+Version 2\.0", re.I)),
    ("ISC", re.compile(r"Permission to use, copy, modify, and(?:/or)? distribute this software for any\s+purpose with or without fee is hereby granted", re.I)),
    ("MIT", re.compile(r"Permission is hereby granted, free of charge, to any person obtaining\s+a copy", re.I)),
    ("BSD-3-Clause", re.compile(r"Redistribution and use in source and binary forms.{0,1200}?(?:Neither the name|names of its\s+contributors may be used|name of the copyright holder nor)", re.I | re.S)),
    ("BSD-2-Clause", re.compile(r"Redistribution and use in source and binary forms", re.I)),
    ("Zlib", re.compile(r"This software is provided 'as-is', without any express or implied\s+warranty", re.I)),
    ("Unlicense", re.compile(r"This is free and unencumbered software released into the public domain", re.I)),
]

LICENSE_FILE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENSE-MIT", "COPYING", "license", "License")


def detect_text(text: str) -> str | None:
    """Best-effort SPDX expression for a license file; several distinct texts are joined with AND."""
    found: list[str] = []
    for spdx, pattern in TEXT_SIGNATURES:
        if pattern.search(text):
            if spdx == "BSD-2-Clause" and "BSD-3-Clause" in found:
                continue
            if spdx.startswith("GPL-") and any(f.startswith(("LGPL", "AGPL")) for f in found):
                # LGPL texts refer to the GPL; only a separate GPL text counts.
                if len(re.findall(r"GNU GENERAL PUBLIC LICENSE", text)) <= text.count("LESSER GENERAL PUBLIC LICENSE"):
                    continue
            found.append(spdx)
    found = [f + "-only" if re.match(r"^(A|L)?GPL-\d\.\d$", f) else f for f in found]
    if not found:
        return None
    return " AND ".join(dict.fromkeys(found))


def find_license_file(directory: Path) -> Path | None:
    for name in LICENSE_FILE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    for candidate in sorted(directory.glob("LICEN[CS]E*")):
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- collectors


def collect_python(_: argparse.Namespace) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        run([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
            "--dry-run", "--ignore-installed", "--report", str(report_path),
            "--python-version", "3.13", "--implementation", "cp",
            "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
            "--only-binary=:all:", str(ROOT / "services" / "api"),
        ])
        report = json.loads(report_path.read_text())
    records = []
    for item in report["install"]:
        meta = item["metadata"]
        if meta["name"] == "anum-api":
            continue
        declared = meta.get("license_expression") or meta.get("license") or ""
        license_expr = python_license(meta)
        urls = meta.get("project_url", []) or []
        source = meta.get("home_page") or ""
        for entry in urls:
            label, _, url = entry.partition(",")
            if label.strip().lower() in ("source", "source code", "repository", "code", "github", "homepage", "home"):
                source = url.strip()
                if label.strip().lower() in ("source", "source code", "repository", "code", "github"):
                    break
        if not declared:
            declared = "; ".join(c for c in meta.get("classifier", []) if c.startswith("License ::"))
        records.append(record("python", canonical_python(meta["name"]), meta["version"],
                              declared.splitlines()[0] if declared else "", license_expr, source, ["api"]))
    return records


def canonical_python(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_license(meta: dict) -> str | None:
    expression = meta.get("license_expression")
    if expression:
        return normalize(expression) or expression
    declared = (meta.get("license") or "").strip()
    normalized = normalize(declared)
    if normalized:
        return normalized
    classifiers = [c.removeprefix("License :: OSI Approved :: ").removeprefix("License :: ")
                   for c in meta.get("classifier", []) if c.startswith("License ::")]
    mapped = [CLASSIFIERS.get(c) for c in classifiers]
    if mapped and all(mapped):
        return " OR ".join(sorted(set(mapped)))  # several classifiers mean a choice
    if declared and "\n" in declared:
        return detect_text(declared)
    return None


def collect_npm(_: argparse.Namespace) -> list[dict]:
    import yaml  # PyYAML, installed by the CI job and by regenerate.sh

    lock = yaml.safe_load((ROOT / "pnpm-lock.yaml").read_text())
    if str(lock.get("lockfileVersion")) != "9.0":
        raise SystemExit(f"unsupported pnpm lockfile version {lock.get('lockfileVersion')}")
    snapshots = lock.get("snapshots", {})
    # Production dependencies of each workspace package that ships, and the apps it ships in.
    importer_apps = {"apps/web": ["web", "desktop"], "apps/desktop": ["desktop"], "packages/contracts": ["web", "desktop"]}
    found: dict[str, set[str]] = {}

    def key_for(name: str, version: str) -> str | None:
        version = str(version)
        if version.startswith(("link:", "file:", "workspace:")):
            return None
        base = version.split("(")[0]
        return f"{name}@{version}" if re.match(r"^\d", base) else version

    for importer, apps in importer_apps.items():
        deps = (lock["importers"].get(importer) or {})
        stack = []
        for section in ("dependencies", "optionalDependencies"):
            for name, spec in (deps.get(section) or {}).items():
                key = key_for(name, spec["version"])
                if key:
                    stack.append(key)
        while stack:
            key = stack.pop()
            if key in found and set(apps) <= found[key]:
                continue
            found.setdefault(key, set()).update(apps)
            snapshot = snapshots.get(key) or {}
            for section in ("dependencies", "optionalDependencies"):
                for name, version in (snapshot.get(section) or {}).items():
                    child = key_for(name, version)
                    if child:
                        stack.append(child)

    packages: dict[tuple[str, str], set[str]] = {}
    for key, apps in found.items():
        base = key.split("(")[0]
        name, _, version = base.rpartition("@")
        packages.setdefault((name, version), set()).update(apps)

    def fetch(item):
        (name, version), apps = item
        body = http_get(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/{version}")
        if body is None:
            raise SystemExit(f"npm registry has no manifest for {name}@{version}")
        manifest = json.loads(body)
        declared = manifest.get("license")
        if isinstance(declared, dict):
            declared = declared.get("type")
        if not declared and isinstance(manifest.get("licenses"), list):
            names = [entry.get("type") if isinstance(entry, dict) else str(entry) for entry in manifest["licenses"]]
            declared = " OR ".join(n for n in names if n)
        repository = manifest.get("repository")
        if isinstance(repository, dict):
            repository = repository.get("url")
        if isinstance(repository, str) and re.match(r"^[\w.-]+/[\w.-]+$", repository):
            repository = f"https://github.com/{repository}"
        source = repository or manifest.get("homepage") or f"https://www.npmjs.com/package/{name}"
        return record("npm", name, version, declared, normalize(declared), source, sorted(apps))

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        return list(pool.map(fetch, sorted(packages.items())))


def collect_cargo(_: argparse.Namespace) -> list[dict]:
    manifest = ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.toml"
    metadata = json.loads(run(["cargo", "metadata", "--format-version", "1", "--locked",
                               "--manifest-path", str(manifest)]))
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    shipped: set[str] = set()
    stack = [metadata["resolve"]["root"]]
    while stack:
        node_id = stack.pop()
        if node_id in shipped:
            continue
        shipped.add(node_id)
        for dep in nodes[node_id]["deps"]:
            if any(kind.get("kind") != "dev" for kind in dep["dep_kinds"]):
                stack.append(dep["pkg"])
    records = []
    for package_id in sorted(shipped):
        package = packages[package_id]
        if package.get("source") is None:  # the desktop crate itself
            continue
        declared = package.get("license")
        license_expr = normalize(declared)
        if not declared and package.get("license_file"):
            path = Path(package["manifest_path"]).parent / package["license_file"]
            declared = f"license file {package['license_file']}"
            license_expr = detect_text(path.read_text(errors="replace")) if path.is_file() else None
        source = package.get("repository") or package.get("homepage") or f"https://crates.io/crates/{package['name']}"
        records.append(record("cargo", package["name"], package["version"], declared, license_expr, source, ["desktop"]))
    return records


def collect_dart(_: argparse.Namespace) -> list[dict]:
    mobile = ROOT / "apps" / "mobile"
    flutter = shutil.which("flutter") or "flutter"
    deps = json.loads(run([flutter, "pub", "deps", "--json"], cwd=mobile))
    machine = json.loads(run([flutter, "--version", "--machine"]))
    flutter_root = Path(machine["flutterRoot"])
    pub_cache = Path(os.environ.get("PUB_CACHE") or Path.home() / ".pub-cache")
    by_name = {p["name"]: p for p in deps["packages"]}
    root = next(p for p in deps["packages"] if p["kind"] == "root")
    stack = list(root.get("directDependencies") or [d for d in root["dependencies"] if d not in root.get("devDependencies", [])])
    shipped: set[str] = set()
    while stack:
        name = stack.pop()
        if name in shipped:
            continue
        shipped.add(name)
        stack.extend(by_name[name].get("dependencies", []))
    records = []
    for name in sorted(shipped):
        package = by_name[name]
        version = package["version"]
        if package["source"] == "sdk":
            # Packages that come with the Flutter SDK are covered by the SDK's own license.
            license_file = flutter_root / "LICENSE"
            source = "https://github.com/flutter/flutter"
            declared = "Flutter SDK LICENSE"
        elif package["source"] == "hosted":
            directory = pub_cache / "hosted" / "pub.dev" / f"{name}-{version}"
            license_file = find_license_file(directory)
            source = f"https://pub.dev/packages/{name}"
            declared = f"{license_file.name}" if license_file else ""
            pubspec = directory / "pubspec.yaml"
            if pubspec.is_file():
                match = re.search(r"^repository:\s*(\S+)", pubspec.read_text(errors="replace"), re.M) or \
                    re.search(r"^homepage:\s*(\S+)", pubspec.read_text(errors="replace"), re.M)
                if match:
                    source = match.group(1).strip("'\"")
        else:
            raise SystemExit(f"dart package {name} comes from unsupported source {package['source']}")
        text = license_file.read_text(errors="replace") if license_file and license_file.is_file() else ""
        records.append(record("dart", name, version, declared, detect_text(text) if text else None, source, ["mobile"]))
    return records


GRADLE_INIT = """
allprojects {
    tasks.register("anumLicenseDependencies") {
        doLast {
            def configuration = project.configurations.findByName("releaseRuntimeClasspath")
            if (configuration == null) return
            configuration.incoming.resolutionResult.allComponents.each { component ->
                def id = component.id
                if (id instanceof org.gradle.api.artifacts.component.ModuleComponentIdentifier) {
                    println "ANUM-DEPENDENCY ${id.group}:${id.module}:${id.version}"
                }
            }
        }
    }
}
"""

MAVEN_REPOSITORIES = ("https://dl.google.com/dl/android/maven2", "https://repo1.maven.org/maven2")


def collect_gradle(args: argparse.Namespace) -> list[dict]:
    gradle = args.gradle or shutil.which("gradle") or "gradle"
    with tempfile.TemporaryDirectory() as tmp:
        init = Path(tmp) / "anum-licenses.gradle"
        init.write_text(GRADLE_INIT)
        output = run([gradle, "-p", str(ROOT / "apps" / "android"), "--quiet", "--no-configuration-cache",
                      "--init-script", str(init), ":app:anumLicenseDependencies"])
    coordinates = sorted({line.split(" ", 1)[1].strip() for line in output.splitlines() if line.startswith("ANUM-DEPENDENCY ")})
    if not coordinates:
        raise SystemExit("Gradle reported no release runtime dependencies for apps/android")
    gradle_cache = Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle") / "caches" / "modules-2" / "files-2.1"
    pom_cache: dict[str, ET.Element | None] = {}

    def pom(group: str, artifact: str, version: str) -> ET.Element | None:
        key = f"{group}:{artifact}:{version}"
        if key in pom_cache:
            return pom_cache[key]
        text = None
        for path in sorted((gradle_cache / group / artifact / version).glob("*/*.pom")):
            text = path.read_bytes()
            break
        if text is None:
            for repository in MAVEN_REPOSITORIES:
                text = http_get(f"{repository}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom")
                if text:
                    break
        # POMs come from the Gradle cache or the two HTTPS repositories above; expat never
        # fetches external entities.
        element = ET.fromstring(text) if text else None
        pom_cache[key] = element
        return element

    def child(element: ET.Element, name: str) -> ET.Element | None:
        for node in element:
            if node.tag.rsplit("}", 1)[-1] == name:
                return node
        return None

    def licenses_of(group: str, artifact: str, version: str, depth: int = 0):
        element = pom(group, artifact, version)
        if element is None:
            return [], ""
        url_node = child(element, "url")
        scm = child(element, "scm")
        source = (url_node.text if url_node is not None else "") or ""
        if scm is not None and child(scm, "url") is not None:
            source = child(scm, "url").text or source
        licenses_node = child(element, "licenses")
        found = []
        if licenses_node is not None:
            for license_node in licenses_node:
                name_node = child(license_node, "name")
                url = child(license_node, "url")
                found.append(((name_node.text or "").strip() if name_node is not None else "",
                              (url.text or "").strip() if url is not None else ""))
        if not found and depth < 6:
            parent = child(element, "parent")
            if parent is not None:
                values = {n: (child(parent, n).text if child(parent, n) is not None else "") for n in ("groupId", "artifactId", "version")}
                parent_found, parent_source = licenses_of(values["groupId"], values["artifactId"], values["version"], depth + 1)
                return parent_found, source or parent_source
        return found, source

    def build(coordinate: str) -> dict:
        group, artifact, version = coordinate.split(":")
        found, source = licenses_of(group, artifact, version)
        ids = []
        for name, url in found:
            ids.append(normalize(name) or normalize(url))
        declared = "; ".join(name or url for name, url in found)
        license_expr = " OR ".join(dict.fromkeys(ids)) if ids and all(ids) else None
        return record("gradle", f"{group}:{artifact}", version, declared, license_expr,
                      source or f"https://mvnrepository.com/artifact/{group}/{artifact}", ["android"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(build, coordinates))


COLLECTORS = {"python": collect_python, "npm": collect_npm, "cargo": collect_cargo, "dart": collect_dart, "gradle": collect_gradle}


def command_collect(args: argparse.Namespace) -> int:
    records = COLLECTORS[args.ecosystem](args)
    records.sort(key=lambda r: (r["name"].lower(), r["version"]))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"ecosystem": args.ecosystem, "packages": records}, indent=2) + "\n")
    log(f"{args.ecosystem}: {len(records)} packages -> {args.out}")
    return 0


# --------------------------------------------------------------------------- policy


def matches(pattern: str, spdx_id: str) -> bool:
    if pattern.endswith("*"):
        return spdx_id.startswith(pattern[:-1])
    return pattern == spdx_id


def classify(expression: str | None, policy: dict) -> str:
    if not expression:
        return "unknown"
    try:
        tree = parse(tokenize(expression))
    except ValueError:
        return "unknown"
    lists = policy["licenses"]
    with_rules = policy.get("with", {})

    def leaf(spdx_id: str) -> str:
        spdx_id = spdx_id.removesuffix("+").lower()
        for category in ("denied", "review", "allowed"):
            if any(matches(p.lower(), spdx_id) for p in lists.get(category, [])):
                return category
        return "unknown"

    def walk(node) -> str:
        if node[0] == "ID":
            return leaf(node[1])
        if node[0] == "WITH":
            return with_rules.get(f"{node[1]} WITH {node[2]}") or leaf(node[1])
        results = [walk(child) for child in node[1]]
        pick = min if node[0] == "OR" else max
        return pick(results, key=RANK.__getitem__)

    return walk(tree)


def load_exceptions() -> tuple[list[dict], list[dict]]:
    data = tomllib.loads(EXCEPTIONS_FILE.read_text()) if EXCEPTIONS_FILE.exists() else {}
    overrides = data.get("override", [])
    exceptions = data.get("exception", [])
    for entry in overrides:
        missing = [f for f in ("ecosystem", "package", "detected", "license", "evidence", "reason") if f not in entry]
        if missing:
            raise SystemExit(f"exceptions.toml: override for {entry.get('package')} is missing {missing}")
    for entry in exceptions:
        missing = [f for f in ("ecosystem", "package", "license", "reason", "reviewed_by", "reviewed_on") if not entry.get(f)]
        if missing:
            raise SystemExit(f"exceptions.toml: exception for {entry.get('package')} is missing {missing}")
    return overrides, exceptions


def evaluate(packages: list[dict], policy: dict, overrides: list[dict], exceptions: list[dict], scanned: set[str]):
    used_overrides: set[int] = set()
    used_exceptions: set[int] = set()
    results = []
    for package in packages:
        effective = package["license"]
        note = ""
        for index, entry in enumerate(overrides):
            if entry["ecosystem"] == package["ecosystem"] and entry["package"] == package["name"] \
                    and (entry["detected"] or None) == (package["license"] or None):
                effective = entry["license"]
                note = f"license corrected by review: {entry['reason']}"
                used_overrides.add(index)
                break
        status = classify(effective, policy)
        excepted = None
        if status != "allowed":
            for index, entry in enumerate(exceptions):
                if entry["ecosystem"] == package["ecosystem"] and entry["package"] == package["name"] \
                        and entry["license"] == effective:
                    excepted = entry
                    used_exceptions.add(index)
                    break
        results.append({**package, "effective": effective, "status": status, "note": note,
                        "exception": excepted, "violation": status != "allowed" and excepted is None})
    stale = [f"override {e['ecosystem']}:{e['package']} (detected {e['detected']!r}) matched nothing"
             for i, e in enumerate(overrides) if i not in used_overrides and e["ecosystem"] in scanned]
    stale += [f"exception {e['ecosystem']}:{e['package']} ({e['license']}) matched nothing"
              for i, e in enumerate(exceptions) if i not in used_exceptions and e["ecosystem"] in scanned]
    return results, stale


# --------------------------------------------------------------------------- outputs

ECOSYSTEM_TITLES = {
    "python": "Python packages (PyPI)",
    "npm": "JavaScript packages (npm)",
    "cargo": "Rust crates (crates.io)",
    "dart": "Dart and Flutter packages (pub.dev)",
    "gradle": "Android libraries (Maven)",
}


def notices(results: list[dict]) -> str:
    lines = [
        "# Third-party notices",
        "",
        "ANUM includes the open source components listed below. Each is the property of its",
        "authors and is used under the license shown. The full license text and copyright",
        "notice of each component ship with that component (its package in the API image,",
        "the installed app's dependency files, or the source linked here).",
        "",
        "This file is generated by `scripts/licenses/regenerate.sh` from the license scan in",
        "CI (docs/licenses.md); do not edit it by hand. It lists package names and licenses;",
        "exact versions are in the CycloneDX SBOM that CI builds for each app.",
        "",
        "Apps: " + ", ".join(f"`{k}` {v}" for k, v in APPS.items()) + ".",
        "",
    ]
    for ecosystem in ECOSYSTEMS:
        rows: dict[tuple[str, str], dict] = {}
        for result in results:
            if result["ecosystem"] != ecosystem:
                continue
            key = (result["name"], result["effective"] or "")
            entry = rows.setdefault(key, {"apps": set(), "source": result["source"]})
            entry["apps"].update(result["apps"])
            if not entry["source"]:
                entry["source"] = result["source"]
        if not rows:
            continue
        names = {name for name, _ in rows}
        lines += [f"## {ECOSYSTEM_TITLES[ecosystem]}", "", f"{len(names)} components.", "",
                  "| Component | License | Used in | Source |", "|---|---|---|---|"]
        for (name, license_expr), entry in sorted(rows.items(), key=lambda item: (item[0][0].lower(), item[0][1])):
            source = f"<{entry['source']}>" if entry["source"] else "-"
            lines.append(f"| {escape(name)} | {escape(license_expr) or 'unknown'} | {', '.join(sorted(entry['apps']))} | {source} |")
        lines.append("")
    # Components accepted under a reviewed exception (scripts/licenses/exceptions.toml), with
    # the notice the license asks for. Only exceptions that ship in some app are listed.
    accepted: dict[tuple[str, str], dict] = {}
    for result in results:
        if result["exception"]:
            entry = accepted.setdefault((result["ecosystem"], result["name"]),
                                        {"result": result, "apps": set()})
            entry["apps"].update(result["apps"])
    if accepted:
        lines += ["## Components used under a reviewed license exception", "",
                  "These components carry a weak copyleft license. Each was reviewed and accepted for",
                  "the reason given (docs/licenses.md).", ""]
        for (ecosystem, name), entry in sorted(accepted.items(), key=lambda item: (item[0][0], item[0][1].lower())):
            result, exception = entry["result"], entry["result"]["exception"]
            lines += [f"### {escape(name)} ({ECOSYSTEM_TITLES[ecosystem]})", "",
                      f"- License: {result['effective']}",
                      f"- Used in: {', '.join(sorted(entry['apps']))}",
                      f"- Source: <{exception.get('source_url') or result['source']}>",
                      f"- Why it is accepted: {exception['reason']}"]
            if exception.get("notice"):
                lines.append(f"- Notice: {exception['notice']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def escape(text: str) -> str:
    return text.replace("|", "\\|")


def purl(result: dict) -> str:
    kind = PURL_TYPE[result["ecosystem"]]
    name = result["name"]
    if result["ecosystem"] == "gradle":
        group, artifact = name.split(":", 1)
        return f"pkg:maven/{group}/{artifact}@{result['version']}"
    if result["ecosystem"] == "npm" and name.startswith("@"):
        scope, bare = name.split("/", 1)
        return f"pkg:npm/{urllib.parse.quote(scope)}/{bare}@{result['version']}"
    return f"pkg:{kind}/{name}@{result['version']}"


def sbom(app: str, results: list[dict], commit: str) -> dict:
    components = []
    seen = set()
    for result in sorted(results, key=lambda r: (r["ecosystem"], r["name"].lower(), r["version"])):
        if app not in result["apps"]:
            continue
        reference = purl(result)
        if reference in seen:
            continue
        seen.add(reference)
        component = {"type": "library", "bom-ref": reference, "name": result["name"], "version": result["version"],
                     "purl": reference, "scope": "required"}
        if result["ecosystem"] == "gradle":
            component["group"], component["name"] = result["name"].split(":", 1)
        if result["effective"]:
            component["licenses"] = [{"expression": result["effective"]}]
        if result["source"]:
            component["externalReferences"] = [{"type": "vcs" if urllib.parse.urlsplit(result["source"]).hostname in ("github.com", "gitlab.com") else "website",
                                                "url": result["source"]}]
        components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "tools": {"components": [{"type": "application", "name": "anum-license-scan",
                                      "description": "scripts/licenses/license_scan.py"}]},
            "component": {"type": "application", "bom-ref": f"anum-{app}", "name": f"anum-{app}",
                          "version": commit, "description": APPS[app]},
        },
        "components": components,
    }


def summary(results: list[dict], stale: list[str], scanned: list[str]) -> str:
    lines = ["## License scan", "", "| Ecosystem | Packages | Allowed | Excepted | Violations |", "|---|---|---|---|---|"]
    for ecosystem in scanned:
        subset = [r for r in results if r["ecosystem"] == ecosystem]
        lines.append(f"| {ecosystem} | {len(subset)} | {sum(r['status'] == 'allowed' for r in subset)} | "
                     f"{sum(bool(r['exception']) for r in subset)} | {sum(r['violation'] for r in subset)} |")
    violations = [r for r in results if r["violation"]]
    if violations:
        lines += ["", "### Violations (fix, replace, or add a reviewed exception)", "",
                  "| Ecosystem | Package | Version | License | Class | Apps |", "|---|---|---|---|---|---|"]
        for r in sorted(violations, key=lambda r: (r["ecosystem"], r["name"])):
            lines.append(f"| {r['ecosystem']} | {escape(r['name'])} | {r['version']} | "
                         f"{escape(r['effective'] or ('none detected; declared: ' + (r['declared'] or '-')))} | "
                         f"{r['status']} | {', '.join(r['apps'])} |")
    if stale:
        lines += ["", "### Stale entries in scripts/licenses/exceptions.toml", ""] + [f"- {s}" for s in stale]
    return "\n".join(lines) + "\n"


def command_report(args: argparse.Namespace) -> int:
    policy = tomllib.loads(POLICY_FILE.read_text())
    overrides, exceptions = load_exceptions()
    packages: list[dict] = []
    scanned: list[str] = []
    for path in args.inputs:
        data = json.loads(Path(path).read_text())
        scanned.append(data["ecosystem"])
        packages.extend(data["packages"])
    results, stale = evaluate(packages, policy, overrides, exceptions, set(scanned))
    text = summary(results, stale, scanned)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.results:
        Path(args.results).write_text(json.dumps(results, indent=2) + "\n")
    failed = bool(stale) or any(r["violation"] for r in results)

    complete = sorted(scanned) == sorted(ECOSYSTEMS)
    if args.write_notices or args.check_notices:
        if not complete:
            raise SystemExit(f"THIRD_PARTY_NOTICES.md needs every ecosystem ({', '.join(ECOSYSTEMS)}); got {', '.join(scanned)}")
        generated = notices(results)
        if args.write_notices:
            Path(args.write_notices).write_text(generated)
            log(f"wrote {args.write_notices}")
        if args.check_notices:
            current = NOTICES_FILE.read_text() if NOTICES_FILE.exists() else ""
            if current != generated:
                diff = list(difflib.unified_diff(current.splitlines(), generated.splitlines(),
                                                 "THIRD_PARTY_NOTICES.md (committed)", "THIRD_PARTY_NOTICES.md (scan)", lineterm=""))
                print("\n".join(diff[:200]))
                print("::error::THIRD_PARTY_NOTICES.md is out of date. Run scripts/licenses/regenerate.sh, or "
                      "commit the file from this run's `third-party-notices` artifact.")
                failed = True
            else:
                log("THIRD_PARTY_NOTICES.md is current")
    if args.sbom_dir:
        directory = Path(args.sbom_dir)
        directory.mkdir(parents=True, exist_ok=True)
        commit = os.environ.get("GITHUB_SHA") or "local"
        for app in APPS:
            document = sbom(app, results, commit)
            if not document["components"]:
                continue
            target = directory / f"sbom-{app}.cdx.json"
            target.write_text(json.dumps(document, indent=2) + "\n")
            log(f"SBOM {target} lists {len(document['components'])} components")
    if failed:
        print("::error::License policy check failed (see the table above and docs/licenses.md).")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="list one ecosystem's shipped packages")
    collect.add_argument("ecosystem", choices=ECOSYSTEMS)
    collect.add_argument("--out", required=True)
    collect.add_argument("--gradle", help="gradle executable (default: gradle on PATH)")
    report = sub.add_parser("report", help="apply the policy; write notices and SBOMs")
    report.add_argument("--inputs", nargs="+", required=True)
    report.add_argument("--results", help="write evaluated results as JSON")
    report.add_argument("--write-notices", metavar="FILE")
    report.add_argument("--check-notices", action="store_true")
    report.add_argument("--sbom-dir")
    args = parser.parse_args()
    return command_collect(args) if args.command == "collect" else command_report(args)


if __name__ == "__main__":
    sys.exit(main())
