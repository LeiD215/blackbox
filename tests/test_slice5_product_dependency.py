from __future__ import annotations
import shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from blackbox_vnext import dependency_pin, gate
from blackbox_vnext.receipt import IdentityBinding, Observation, verify_independent

class Slice5ProductDependencyTests(unittest.TestCase):
 def test_shipped_profile_passes_without_git(self):
  self.assertTrue(dependency_pin.verify_dependency().ok)
 def _drift(self, relative: str):
  with tempfile.TemporaryDirectory() as td:
   copied=Path(td)/"blackbox_vnext"; shutil.copytree(dependency_pin.PACKAGE,copied)
   path=copied/relative; path.write_bytes(path.read_bytes()+b"\n# drift\n")
   with patch.object(dependency_pin,"PACKAGE",copied):
    self.assertFalse(dependency_pin.verify_dependency().ok)
    with self.assertRaises(dependency_pin.DependencyDrift): dependency_pin.gate_semantic_entrypoint()
 def test_root_canonical_drift_fails_closed(self): self._drift("canonical.py")
 def test_slice4_dispatch_drift_fails_closed(self): self._drift("slice4/dispatch.py")
 def test_missing_api_fails_closed(self):
  profile=dict(dependency_pin.API_PROFILE); profile["canonical.py"]=("missing_symbol",)
  with patch.object(dependency_pin,"API_PROFILE",profile): self.assertFalse(dependency_pin.verify_dependency().ok)
 def test_real_receipt_and_gate_are_blocked_before_green(self):
  binding=IdentityBinding("a","task:a","artifact:a","scope","text:ok","text","executor","claim")
  observation=Observation("a","task:a","artifact:a","scope","text","ok","read","source","reviewer","executor",True,True,"2026-09-03T00:00:00Z","test")
  with patch.dict(dependency_pin.FILE_SHA256,{"canonical.py":"0"*64}):
   with self.assertRaises(dependency_pin.DependencyDrift): verify_independent(binding,observation,now="2026-09-03T00:00:00Z")
   with self.assertRaises(dependency_pin.DependencyDrift): gate.pre_release_check((),())

if __name__ == "__main__": unittest.main()
