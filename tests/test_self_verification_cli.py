from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
from blackbox_vnext import CanonicalRequirement, canonical, pre_release_check
from blackbox_vnext.cli import main

class SelfVerificationCliTests(unittest.TestCase):
 def _event(self):
  event={"schema_version":"0.3.2","event_id":"evt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","type":"VERIFICATION","subtype":"self-verification","receipt_class":"self-verification","actor":"agent:executor","subject":"task:example","recorded_at":"2026-09-03T00:00:00Z","prior_refs":{"parent":[],"supports":[]},"extensions":{},"body":"explicit self evidence"}
  event["content_hash"]=canonical.sha256_canonical(event); return event
 def test_explicit_canonical_self_verification_is_written_but_not_independent(self):
  event=self._event()
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); source=root/"self.json"; source.write_text(json.dumps(event),encoding="utf-8")
   self.assertEqual(main(["init","--root",str(root)]),0)
   self.assertEqual(main(["self-verify","--root",str(root),"--event",str(source)]),0)
   self.assertTrue((root/".blackbox/events"/f"{event['event_id']}.json").is_file())
  requirement=CanonicalRequirement("effect","scope","target","task:example","artifact","scope","text:green","text","agent:executor","claim","read","source","verifier","release",True,True,True)
  self.assertEqual(pre_release_check((requirement,),(event,)).verdict,"NON-GREEN")
 def test_missing_or_wrong_profile_writes_nothing(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); self.assertEqual(main(["init","--root",str(root)]),0)
   self.assertEqual(main(["self-verify","--root",str(root),"--event",str(root/"missing.json")]),2)
   self.assertFalse(list((root/".blackbox/events").glob("*.json")))
 def test_wrong_profile_is_rejected_without_write_and_hash_is_deterministic(self):
  event=self._event(); self.assertEqual(event["content_hash"],canonical.sha256_canonical({k:v for k,v in event.items() if k!="content_hash"}))
  event["receipt_class"]="independent-verification"
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); source=root/"wrong.json"; source.write_text(json.dumps(event),encoding="utf-8")
   self.assertEqual(main(["init","--root",str(root)]),0)
   self.assertEqual(main(["self-verify","--root",str(root),"--event",str(source)]),2)
   self.assertFalse(list((root/".blackbox/events").glob("*.json")))

if __name__ == "__main__": unittest.main()
