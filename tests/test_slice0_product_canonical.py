from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from blackbox_vnext.canonical import canonical_bytes, sha256_canonical, verify_content_hash
from blackbox_vnext.slice1.store import WriteError, write_event

class Slice0ProductCanonicalTests(unittest.TestCase):
 def test_canonical_bytes_are_deterministic_and_array_order_is_preserved(self):
  self.assertEqual(canonical_bytes({"b":[2,1],"a":{"z":1,"y":2}}),b'{"a":{"y":2,"z":1},"b":[2,1]}')
 def test_hash_recomputes_over_content_hash_omitted_input(self):
  event={"event_id":"evt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","x":1}; event["content_hash"]=sha256_canonical(event)
  self.assertTrue(verify_content_hash(event)); event["x"]=2; self.assertFalse(verify_content_hash(event))
 def test_utf16_golden_vector_is_stable(self):
  self.assertEqual(sha256_canonical({"\U0001f600":1,"\ufffd":2}),"sha256:fd8b688bfa8b71822975ab3519e20b09e43b67d382a9f32831bfa384df21a82d")
 def test_invalid_duplicate_and_collision_are_rejected_without_rewrite(self):
  raw='{"schema_version":"0.3.2","event_id":"evt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","event_id":"evt-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(WriteError): write_event(raw,events_dir=td)
   event={"schema_version":"0.3.2","event_id":"evt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","actor":"human:owner","type":"TASK","subtype":"dispatch","receipt_class":"claim","subject":"task:a","recorded_at":"2026-09-03T00:00:00Z","prior_refs":{"parent":[],"supports":[]},"extensions":{"dispatch":{"assignee":"executor:a","surface":"project:reference"}}}
   event["content_hash"]=sha256_canonical(event); write_event(event,events_dir=td)
   before=(Path(td)/(event["event_id"]+".json")).read_bytes(); event["subject"]="task:changed"; event["content_hash"]=sha256_canonical({k:v for k,v in event.items() if k!="content_hash"})
   with self.assertRaises(WriteError): write_event(event,events_dir=td)
   self.assertEqual((Path(td)/(event["event_id"]+".json")).read_bytes(),before)
 def test_correction_appends_new_canonical_history_without_rewriting_prior_event(self):
  with tempfile.TemporaryDirectory() as td:
   dispatch={"schema_version":"0.3.2","event_id":"evt-11111111111111111111111111111111","actor":"human:owner","type":"TASK","subtype":"dispatch","receipt_class":"claim","subject":"task:corrected","recorded_at":"2026-09-03T00:00:00Z","prior_refs":{"parent":[],"supports":[]},"extensions":{"dispatch":{"assignee":"executor:a","surface":"project:reference"}}}
   dispatch["content_hash"]=sha256_canonical(dispatch); write_event(dispatch,events_dir=td)
   original=Path(td)/(dispatch["event_id"]+".json"); before=original.read_bytes()
   correction={"schema_version":"0.3.2","event_id":"evt-22222222222222222222222222222222","actor":"human:owner","type":"CHANGE","subtype":"correction","receipt_class":"claim","subject":"task:corrected","recorded_at":"2026-09-03T00:00:01Z","prior_refs":{"parent":[dispatch["event_id"]],"supports":[]},"extensions":{}}
   correction["content_hash"]=sha256_canonical(correction); write_event(correction,events_dir=td)
   self.assertEqual(original.read_bytes(),before)
   self.assertTrue((Path(td)/(correction["event_id"]+".json")).is_file())

if __name__ == "__main__": unittest.main()
