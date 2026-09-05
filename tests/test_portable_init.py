from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from blackbox_vnext.cli import main
from blackbox_vnext.slice1.observe import compute_profile_identity

DATA=Path(__file__).resolve().parents[1]/"src"/"blackbox_vnext"/"data"
class PortableInitTests(unittest.TestCase):
 def test_init_materializes_exact_profile_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); self.assertEqual(main(["init","--root",str(root)]),0)
   bb=root/".blackbox"
   for name in ("events","checkpoints","projections","recovery"): self.assertTrue((bb/name).is_dir())
   self.assertEqual((bb/"FORMAT").read_bytes(),(DATA/"FORMAT.md").read_bytes())
   self.assertEqual((bb/"authority/bootstrap.json").read_bytes(),(DATA/"bootstrap.json").read_bytes())
   for resource in DATA.glob("*.json"):
    target=bb/(Path("authority/bootstrap.json") if resource.name=="bootstrap.json" else Path("schema")/resource.name)
    self.assertEqual(target.read_bytes(),resource.read_bytes())
   import json
   profile=json.loads((bb/"schema/observation-profile.json").read_text(encoding="utf-8"))
   self.assertEqual(profile["profile_identity_sha256"],compute_profile_identity(profile))
   self.assertEqual(main(["init","--root",str(root)]),0)
 def test_governed_resource_drift_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); self.assertEqual(main(["init","--root",str(root)]),0)
   fmt=root/".blackbox/FORMAT"; fmt.write_text("drift",encoding="utf-8")
   self.assertEqual(main(["init","--root",str(root)]),2); self.assertEqual(fmt.read_text(encoding="utf-8"),"drift")
 def test_bootstrap_and_profile_drift_are_not_overwritten(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); self.assertEqual(main(["init","--root",str(root)]),0)
   bootstrap=root/".blackbox/authority/bootstrap.json"; bootstrap.write_text("drift",encoding="utf-8")
   self.assertEqual(main(["init","--root",str(root)]),2); self.assertEqual(bootstrap.read_text(encoding="utf-8"),"drift")
   bootstrap.write_bytes((DATA/"bootstrap.json").read_bytes())
   profile=root/".blackbox/schema/observation-profile.json"; profile.write_text("drift",encoding="utf-8")
   self.assertEqual(main(["init","--root",str(root)]),2); self.assertEqual(profile.read_text(encoding="utf-8"),"drift")

if __name__ == "__main__": unittest.main()
