"""Unit tests for the license policy logic: python -m unittest discover -s scripts/licenses"""

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import license_scan as scan  # noqa: E402

POLICY = tomllib.loads((Path(__file__).resolve().parent / "policy.toml").read_text())


def package(name, license_expr, ecosystem="npm"):
    return {"ecosystem": ecosystem, "name": name, "version": "1.0.0", "declared": license_expr or "",
            "license": license_expr, "source": "", "apps": ["web"]}


class NormalizeTest(unittest.TestCase):
    def test_free_text_and_old_cargo_style(self):
        self.assertEqual(scan.normalize("MIT License"), "MIT")
        self.assertEqual(scan.normalize("The Apache Software License, Version 2.0"), "Apache-2.0")
        self.assertEqual(scan.normalize("MIT/Apache-2.0"), "MIT OR Apache-2.0")
        self.assertEqual(scan.normalize("(MIT OR CC0-1.0)"), "MIT OR CC0-1.0")
        self.assertEqual(scan.normalize("mit or apache-2.0"), "MIT OR Apache-2.0")
        self.assertEqual(scan.normalize("http://www.apache.org/licenses/LICENSE-2.0.txt"), "Apache-2.0")

    def test_unrecognised_text_is_none(self):
        self.assertIsNone(scan.normalize("SEE LICENSE IN LICENSE.md"))
        self.assertIsNone(scan.normalize("Dual License"))
        self.assertIsNone(scan.normalize("UNLICENSED"))
        self.assertIsNone(scan.normalize(""))


class ClassifyTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(scan.classify("MIT", POLICY), "allowed")
        self.assertEqual(scan.classify("MPL-2.0", POLICY), "allowed")
        self.assertEqual(scan.classify("LGPL-3.0-or-later", POLICY), "review")
        self.assertEqual(scan.classify("GPL-3.0-only", POLICY), "denied")
        self.assertEqual(scan.classify("AGPL-3.0-or-later", POLICY), "denied")
        self.assertEqual(scan.classify("SSPL-1.0", POLICY), "denied")
        self.assertEqual(scan.classify("BUSL-1.1", POLICY), "denied")
        self.assertEqual(scan.classify("GPL-2.0+", POLICY), "denied")
        self.assertEqual(scan.classify("WTFPL", POLICY), "unknown")
        self.assertEqual(scan.classify(None, POLICY), "unknown")
        self.assertEqual(scan.classify("mit", POLICY), "allowed")
        self.assertEqual(scan.classify("not ( valid", POLICY), "unknown")

    def test_or_takes_the_best_branch_and_and_the_worst(self):
        self.assertEqual(scan.classify("MIT OR GPL-3.0-only", POLICY), "allowed")
        self.assertEqual(scan.classify("MIT AND GPL-3.0-only", POLICY), "denied")
        self.assertEqual(scan.classify("Apache-2.0 AND LGPL-3.0-or-later", POLICY), "review")
        self.assertEqual(scan.classify("(MIT OR Apache-2.0) AND Unicode-3.0", POLICY), "allowed")

    def test_with_exceptions(self):
        self.assertEqual(scan.classify("Apache-2.0 WITH LLVM-exception", POLICY), "allowed")
        self.assertEqual(scan.classify("GPL-2.0-only WITH Classpath-exception-2.0", POLICY), "review")


class EvaluateTest(unittest.TestCase):
    def test_violation_exception_override_and_stale_entries(self):
        packages = [package("ok", "MIT"), package("lgpl", "LGPL-3.0-only"), package("gpl", "GPL-3.0-only"),
                    package("none", None)]
        overrides = [{"ecosystem": "npm", "package": "none", "detected": "", "license": "BSD-3-Clause",
                      "evidence": "https://example.invalid", "reason": "read the LICENSE"},
                     {"ecosystem": "npm", "package": "gone", "detected": "", "license": "MIT",
                      "evidence": "https://example.invalid", "reason": "x"}]
        exceptions = [{"ecosystem": "npm", "package": "lgpl", "license": "LGPL-3.0-only", "reason": "dynamic",
                       "reviewed_by": "owner", "reviewed_on": "2026-10-18"},
                      {"ecosystem": "cargo", "package": "other", "license": "GPL-3.0-only", "reason": "x",
                       "reviewed_by": "owner", "reviewed_on": "2026-10-18"}]
        results, stale = scan.evaluate(packages, POLICY, overrides, exceptions, {"npm"})
        by_name = {r["name"]: r for r in results}
        self.assertFalse(by_name["ok"]["violation"])
        self.assertFalse(by_name["lgpl"]["violation"])
        self.assertTrue(by_name["gpl"]["violation"])
        self.assertEqual(by_name["none"]["effective"], "BSD-3-Clause")
        self.assertFalse(by_name["none"]["violation"])
        # The unused npm override is stale; the cargo exception is not judged without a cargo scan.
        self.assertEqual(len(stale), 1)
        self.assertIn("gone", stale[0])

    def test_exception_must_match_the_reported_license(self):
        exceptions = [{"ecosystem": "npm", "package": "lgpl", "license": "LGPL-2.1-only", "reason": "x",
                       "reviewed_by": "owner", "reviewed_on": "2026-10-18"}]
        results, stale = scan.evaluate([package("lgpl", "LGPL-3.0-only")], POLICY, [], exceptions, {"npm"})
        self.assertTrue(results[0]["violation"])
        self.assertEqual(len(stale), 1)


class VersionPinTest(unittest.TestCase):
    def test_patterns(self):
        self.assertTrue(scan.version_matches(["3.3.6"], "3.3.6"))
        self.assertFalse(scan.version_matches(["3.3.6"], "3.3.7"))
        self.assertTrue(scan.version_matches(["3.3.*"], "3.3.7"))
        self.assertFalse(scan.version_matches(["3.3.*"], "3.4.0"))
        self.assertFalse(scan.version_matches(["3.3.*"], "3.30.0"))
        self.assertTrue(scan.version_matches(None, "9.9.9"))

    def test_exception_outside_its_versions_is_reviewed_again(self):
        exceptions = [{"ecosystem": "npm", "package": "lgpl", "versions": ["1.0.*"], "license": "LGPL-3.0-only",
                       "reason": "x", "reviewed_by": "owner", "reviewed_on": "2026-11-04"}]
        results, stale = scan.evaluate([package("lgpl", "LGPL-3.0-only")], POLICY, [], exceptions, {"npm"})
        self.assertFalse(results[0]["violation"])
        self.assertEqual(stale, [])
        bumped = {**package("lgpl", "LGPL-3.0-only"), "version": "2.0.0"}
        results, stale = scan.evaluate([bumped], POLICY, [], exceptions, {"npm"})
        self.assertTrue(results[0]["violation"])
        self.assertEqual(len(stale), 1)


BUNDLED = [{"ecosystem": "python", "package": "wheel-with-libs", "versions": ["1.0.0"], "reviewed_on": "2026-11-04",
            "license_texts": ["licenses/third-party/Zlib.txt"],
            "libraries": [{"name": "zlib", "license": "Zlib", "copyright": "Jean-loup Gailly and Mark Adler",
                           "source": "https://zlib.net", "notice": "credit zlib"}]}]


class AcceptedNoticeTest(unittest.TestCase):
    def test_build_only_exception_says_it_is_not_shipped(self):
        exceptions = [{"ecosystem": "npm", "package": "lgpl", "versions": ["1.0.0"], "license": "LGPL-3.0-only",
                       "reason": "build only", "reviewed_by": "owner", "reviewed_on": "2026-11-04", "shipped": False}]
        results, _ = scan.evaluate([package("lgpl", "LGPL-3.0-only")], POLICY, [], exceptions, {"npm"})
        text = scan.notices(results)
        self.assertIn("- Used in: not shipped (build-time only; in the lockfile closure of web)", text)
        self.assertIn("- Approved versions: 1.0.0", text)


class BundledTest(unittest.TestCase):
    def test_bundled_libraries_are_listed_with_their_notices(self):
        results, _ = scan.evaluate([package("wheel-with-libs", "MIT", "python")], POLICY, [], [], {"python"})
        self.assertEqual(scan.attach_bundled(results, BUNDLED, {"python"}), [])
        text = scan.notices(results)
        self.assertIn(scan.BUNDLED_TITLE, text)
        self.assertIn("### wheel-with-libs 1.0.0 (Python packages (PyPI))", text)
        self.assertIn("| zlib | Zlib | Jean-loup Gailly and Mark Adler | <https://zlib.net> |", text)
        self.assertIn("- zlib: credit zlib", text)

    def test_a_new_version_needs_a_new_review(self):
        bumped = {**package("wheel-with-libs", "MIT", "python"), "version": "1.1.0"}
        results, _ = scan.evaluate([bumped], POLICY, [], [], {"python"})
        stale = scan.attach_bundled(results, BUNDLED, {"python"})
        self.assertEqual(len(stale), 1)
        self.assertNotIn(scan.BUNDLED_TITLE, scan.notices(results))
        # Not judged when python was not scanned.
        self.assertEqual(scan.attach_bundled(results, BUNDLED, {"npm"}), [])

    def test_local_scan_keeps_bundled_entries_of_unscanned_ecosystems(self):
        results, _ = scan.evaluate([package("wheel-with-libs", "MIT", "python"), package("left-pad", "MIT")],
                                   POLICY, [], [], {"python", "npm"})
        scan.attach_bundled(results, BUNDLED, {"python"})
        committed = scan.notices(results)
        npm_only, _ = scan.evaluate([package("left-pad", "MIT")], POLICY, [], [], {"npm"})
        scan.attach_bundled(npm_only, BUNDLED, {"npm"})
        self.assertEqual(scan.notices(npm_only, committed, {"python", "cargo", "dart", "gradle"}), committed)


class DetectTextTest(unittest.TestCase):
    def test_common_texts(self):
        mit = "Permission is hereby granted, free of charge, to any person obtaining\na copy of this software"
        self.assertEqual(scan.detect_text(mit), "MIT")
        bsd3 = ("Redistribution and use in source and binary forms, with or without modification...\n"
                "Neither the name of Google Inc. nor the names of its contributors may be used")
        self.assertEqual(scan.detect_text(bsd3), "BSD-3-Clause")
        self.assertEqual(scan.detect_text("Apache License\nVersion 2.0, January 2004"), "Apache-2.0")
        lgpl = "GNU LESSER GENERAL PUBLIC LICENSE\n Version 3, 29 June 2007 ... the GNU General Public License"
        self.assertEqual(scan.detect_text(lgpl), "LGPL-3.0-only")
        self.assertEqual(scan.detect_text("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"), "GPL-3.0-only")
        self.assertIsNone(scan.detect_text("All rights reserved."))


class OutputTest(unittest.TestCase):
    def test_notices_group_by_component_without_versions(self):
        results, _ = scan.evaluate([package("left-pad", "MIT"), {**package("left-pad", "MIT"), "version": "2.0.0",
                                                                  "apps": ["desktop"]}], POLICY, [], [], {"npm"})
        text = scan.notices(results)
        self.assertIn("| left-pad | MIT | desktop, web |", text)
        self.assertNotIn("1.0.0", text)

    def test_local_scan_keeps_committed_sections_it_could_not_scan(self):
        full, _ = scan.evaluate([package("left-pad", "MIT"), package("http", "BSD-3-Clause", "dart"),
                                 package("okio", "Apache-2.0", "gradle")], POLICY, [], [], {"npm", "dart", "gradle"})
        committed = scan.notices(full)
        # A machine without flutter and Gradle scans npm only; npm changed since the commit.
        partial, _ = scan.evaluate([package("left-pad", "MIT"), package("is-even", "MIT")], POLICY, [], [], {"npm"})
        text = scan.notices(partial, committed, {"dart", "gradle", "python", "cargo"})
        self.assertIn("| is-even | MIT | web |", text)
        self.assertIn("| http | BSD-3-Clause | web |", text)
        self.assertIn("| okio | Apache-2.0 | web |", text)
        self.assertIn("## Dart and Flutter packages (pub.dev)", text)
        self.assertIn("## Android libraries (Maven)", text)
        self.assertLess(text.index("## Dart"), text.index("## Android"))
        # With nothing missing the result is the same as a plain generation.
        self.assertEqual(scan.notices(full, committed, set()), committed)
        # Keeping every section of an unchanged scan reproduces the committed file exactly.
        npm_only, _ = scan.evaluate([package("left-pad", "MIT")], POLICY, [], [], {"npm"})
        self.assertEqual(scan.notices(npm_only, committed, {"dart", "gradle", "python", "cargo"}), committed)

    def test_partial_scan_keeps_the_full_scan_order_of_exception_blocks(self):
        # Review finding on #367: with npm skipped, regenerated python blocks came before the
        # kept npm blocks, while a full scan sorts npm first.
        def exception(ecosystem, name, license_expr):
            return {"ecosystem": ecosystem, "package": name, "versions": ["1.0.0"], "license": license_expr,
                    "reason": "x", "reviewed_by": "owner", "reviewed_on": "2026-11-04"}
        exceptions = [exception("python", "psycopg", "LGPL-3.0-only"), exception("npm", "sharp-libvips", "LGPL-3.0-or-later"),
                      exception("python", "aaa", "LGPL-3.0-only")]
        everything = [package("psycopg", "LGPL-3.0-only", "python"), package("aaa", "LGPL-3.0-only", "python"),
                      package("sharp-libvips", "LGPL-3.0-or-later"), package("left-pad", "MIT")]
        bundled = [{**BUNDLED[0], "package": "psycopg"}, {**BUNDLED[0], "package": "zzz", "ecosystem": "npm"}]
        everything.append(package("zzz", "MIT"))
        full, _ = scan.evaluate(everything, POLICY, [], exceptions, {"python", "npm"})
        scan.attach_bundled(full, bundled, {"python", "npm"})
        committed = scan.notices(full)
        self.assertLess(committed.index("### sharp-libvips"), committed.index("### aaa"))
        self.assertLess(committed.index("### zzz 1.0.0"), committed.index("### psycopg 1.0.0"))
        python_only = [p for p in everything if p["ecosystem"] == "python"]
        partial, _ = scan.evaluate(python_only, POLICY, [], exceptions, {"python"})
        scan.attach_bundled(partial, bundled, {"python"})
        # Skip npm: the result equals the full scan (no stale notices, nothing the next full scan rejects).
        self.assertEqual(scan.notices(partial, committed, {"npm", "cargo", "dart", "gradle"}), committed)
        npm_only = [p for p in everything if p["ecosystem"] == "npm"]
        partial, _ = scan.evaluate(npm_only, POLICY, [], exceptions, {"npm"})
        scan.attach_bundled(partial, bundled, {"npm"})
        self.assertEqual(scan.notices(partial, committed, {"python", "cargo", "dart", "gradle"}), committed)

    def test_local_scan_keeps_accepted_exceptions_of_unscanned_ecosystems(self):
        exception = {"ecosystem": "python", "package": "psycopg", "license": "LGPL-3.0-only",
                     "reason": "replaceable driver", "reviewed_by": "owner", "reviewed_on": "2026-11-04"}
        full, _ = scan.evaluate([package("psycopg", "LGPL-3.0-only", "python"), package("left-pad", "MIT")],
                                POLICY, [], [exception], {"python", "npm"})
        committed = scan.notices(full)
        self.assertIn("### psycopg (Python packages (PyPI))", committed)
        npm_only, _ = scan.evaluate([package("left-pad", "MIT")], POLICY, [], [], {"npm"})
        self.assertEqual(scan.notices(npm_only, committed, {"python", "cargo", "dart", "gradle"}), committed)

    def test_sbom_lists_versions_and_purls(self):
        results, _ = scan.evaluate([package("@scope/pkg", "MIT")], POLICY, [], [], {"npm"})
        document = scan.sbom("web", results, "abc")
        self.assertEqual(document["bomFormat"], "CycloneDX")
        self.assertEqual(document["components"][0]["purl"], "pkg:npm/%40scope/pkg@1.0.0")
        self.assertEqual(document["components"][0]["licenses"], [{"expression": "MIT"}])
        self.assertEqual(scan.sbom("api", results, "abc")["components"], [])


if __name__ == "__main__":
    unittest.main()


class NativeInventoryTest(unittest.TestCase):
    """docs/licenses.md#native-inventories: Chromium's own components and the Flutter plugins' Android libraries."""

    CREDITS = (
        "--------------------\nThe Chromium Project\n--------------------\n"
        "Redistribution and use in source and binary forms, with or without modification ... "
        "Neither the name of Google LLC nor the names of its contributors may be used\n\n"
        "--------------------\nicu_locale\n--------------------\nUNICODE LICENSE V3\n\n"
        "--------------------\nsqlite\n--------------------\nThe author disclaims copyright to this source code.\n\n"
        "--------------------\nexample-lgpl\n--------------------\nGNU LESSER GENERAL PUBLIC LICENSE\n Version 2.1, February 1999\n"
    )

    def test_chromium_credits_split_into_components_with_their_licenses(self):
        found = {c["name"]: c["license"] for c in scan.chromium_components(self.CREDITS)}
        self.assertEqual(found, {"The Chromium Project": "BSD-3-Clause", "icu_locale": "Unicode-3.0",
                                 "sqlite": "blessing", "example-lgpl": "LGPL-2.1-only"})

    def test_anything_not_allowed_needs_an_owner_decision(self):
        rows = [{"name": n, "license": lic, "version": "1243"} for n, lic in
                (("chromium", "BSD-3-Clause"), ("lib", "LGPL-2.1-only"), ("odd", None), ("pipe|name", "MIT"))]
        text, attention = scan.native_inventory("T", "I", rows, POLICY)
        self.assertEqual([(r["name"], r["status"]) for r in attention], [("lib", "review"), ("odd", "unknown")])
        self.assertIn("| pipe/name | 1243 | MIT | allowed |", text)
        self.assertIn("Totals: 4 components; allowed 2, review 1, denied 0, not recognised 1.", text)

    def test_gradle_init_output_is_read_once_per_coordinate(self):
        output = ("noise\nANUM-DEPENDENCY com.google.firebase:firebase-messaging:24.1.0\n"
                  "ANUM-DEPENDENCY androidx.core:core:1.13.1\nANUM-DEPENDENCY androidx.core:core:1.13.1\n")
        self.assertEqual(scan.gradle_coordinates(output), ["androidx.core:core:1.13.1", "com.google.firebase:firebase-messaging:24.1.0"])
