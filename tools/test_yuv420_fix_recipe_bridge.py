#!/usr/bin/env python3
"""Read-only regression of exact historical/current reference builder recipe pin.

Checks reference-005 versus already prepared build-002 manifests, with
deliberately corrupted in-memory copies as negative controls. Never edits
existing manifests, binaries, installed drivers, EFI or NVRAM.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
PATH = HERE.parent / "build/nvrm615fix/build.py"
SPEC = importlib.util.spec_from_file_location("fix004_recipe_bridge", PATH)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
HOME = Path.home()


class RecipeBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference = HOME / "nvrm-builds/reference-005/inputs.json"
        before = HOME / "nvrm-builds/yuv420-fix-004-build-002/baseline-inputs.json"
        cls.old = json.loads(reference.read_text())
        cls.new = json.loads(before.read_text())

    def test_only_historical_metadata_extension_is_allowed(self):
        BUILD.validate_reference_recipe_pair(self.old, self.new)

    def test_modified_reference_recipe_rejected(self):
        old = copy.deepcopy(self.old)
        old["recipe_sha256"]["build.py"] = "0" * 64
        with self.assertRaises(RuntimeError):
            BUILD.validate_reference_recipe_pair(old, self.new)

    def test_modified_new_recipe_rejected(self):
        new = copy.deepcopy(self.new)
        new["recipe_sha256"]["build.py"] = "1" * 64
        with self.assertRaises(RuntimeError):
            BUILD.validate_reference_recipe_pair(self.old, new)

    def test_unexpected_recipe_file_rejected(self):
        new = copy.deepcopy(self.new)
        new["recipe_sha256"]["preflight.py"] = "2" * 64
        with self.assertRaises(RuntimeError):
            BUILD.validate_reference_recipe_pair(self.old, new)


if __name__ == "__main__":
    unittest.main(verbosity=2)
