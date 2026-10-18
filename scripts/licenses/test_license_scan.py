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

    def test_sbom_lists_versions_and_purls(self):
        results, _ = scan.evaluate([package("@scope/pkg", "MIT")], POLICY, [], [], {"npm"})
        document = scan.sbom("web", results, "abc")
        self.assertEqual(document["bomFormat"], "CycloneDX")
        self.assertEqual(document["components"][0]["purl"], "pkg:npm/%40scope/pkg@1.0.0")
        self.assertEqual(document["components"][0]["licenses"], [{"expression": "MIT"}])
        self.assertEqual(scan.sbom("api", results, "abc")["components"], [])


if __name__ == "__main__":
    unittest.main()
