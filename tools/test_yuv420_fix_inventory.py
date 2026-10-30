#!/usr/bin/env python3
"""Regression tests for fix-004 source inventories with generated build files.

No network, signing, build, driver installation, EFI or NVRAM changes.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "build/nvrm615fix/build.py"
SPEC = importlib.util.spec_from_file_location("fix004_inventory_runner", RUNNER)
RUNNER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER_MODULE)


class SourceTreeInventoryTest(unittest.TestCase):
    def test_generated_output_files_are_ignored(self):
        with tempfile.TemporaryDirectory(prefix="fix004-inventory-") as tmp:
            root = Path(tmp)
            source = root / "src/nvidia-modeset/kapi/src/nvkms-kapi.c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"known source bytes\n")
            wanted = {source.relative_to(root).as_posix():
                      RUNNER_MODULE.BASE.sha(source)}
            self.assertEqual(RUNNER_MODULE.source_tree_hashes(root), wanted)
            generated = root / "src/nvidia-modeset/_out/Darwin_x86_64/nvkms-kapi.o"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"temporary object\x00")
            self.assertEqual(RUNNER_MODULE.source_tree_hashes(root), wanted)
            generated.write_bytes(b"other temporary object\x01")
            self.assertEqual(RUNNER_MODULE.source_tree_hashes(root), wanted)
            rogue = root / "src/nvidia-modeset/EXTRA_SOURCE.c"
            rogue.write_bytes(b"unexpected source")
            self.assertNotEqual(RUNNER_MODULE.source_tree_hashes(root), wanted)
            rogue.unlink()
            source.write_bytes(b"tampered original source\n")
            self.assertNotEqual(RUNNER_MODULE.source_tree_hashes(root), wanted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
