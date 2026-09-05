from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from blackbox_vnext import canonical
from blackbox_vnext.cli import _prior_authorized
from blackbox_vnext.gate import CanonicalRequirement
from blackbox_vnext.slice4 import dispatch

def req(subject, scope, executor):
 return CanonicalRequirement("effect:"+subject,scope,"target",subject,"artifact",scope,"text:ok","text",executor,"claim","read","source","verifier","release",False,True,True)
class Corpus:
 def __init__(self, events): self.events=events
 def event_by_id(self, event_id): return self.events.get(event_id)
class PriorAuthorizationBindingTests(unittest.TestCase):
 def _dispatch(self, event_id, subject, assignee, parents=()):
  event={"schema_version":"0.3.2","event_id":event_id,"type":"TASK","subtype":"dispatch","receipt_class":"claim","actor":"human:owner","subject":subject,"recorded_at":"2026-09-03T00:00:00Z","prior_refs":{"parent":list(parents),"supports":[]},"extensions":{"dispatch":{"assignee":assignee,"surface":"project:reference"}},"body":"historical binding fixture"}
  event["content_hash"]=canonical.sha256_canonical(event); return event
 def _write(self, directory, events):
  for event in events: (directory/(event["event_id"]+".json")).write_bytes(canonical.stored_bytes(event))
 def test_real_stale_and_sibling_conflict_are_not_prior_authorization(self):
  subject="task:history"; first=self._dispatch("evt-d10000000000000000000000000000d1",subject,"executor:a")
  sibling_a=self._dispatch("evt-d20000000000000000000000000000d2",subject,"executor:a",(first["event_id"],))
  sibling_b=self._dispatch("evt-d30000000000000000000000000000d3",subject,"executor:a",(first["event_id"],))
  stale=self._dispatch("evt-d40000000000000000000000000000d4",subject,"executor:a",(first["event_id"],))
  with tempfile.TemporaryDirectory() as td:
   directory=Path(td); self._write(directory,(first,sibling_a,sibling_b,stale))
   self.assertFalse(dispatch.validate_dispatch(sibling_a["event_id"],directory).authorized)
   self.assertFalse(dispatch.validate_dispatch(stale["event_id"],directory).authorized)
   self.assertFalse(_prior_authorized(req(subject,"project:reference","executor:a"),[sibling_a["event_id"]],directory))
 def test_real_current_corpus_removal_invalidates_prior_authorization(self):
  subject="task:removed"; event=self._dispatch("evt-e10000000000000000000000000000e1",subject,"executor:a")
  with tempfile.TemporaryDirectory() as td:
   directory=Path(td); self._write(directory,(event,))
   issued=dispatch.validate_dispatch(event["event_id"],directory)
   self.assertTrue(issued.authorized)
   self.assertTrue(_prior_authorized(req(subject,"project:reference","executor:a"),[event["event_id"]],directory))
   (directory/(event["event_id"]+".json")).unlink()
   self.assertFalse(_prior_authorized(req(subject,"project:reference","executor:a"),[event["event_id"]],directory))
 def test_real_slice4_corpus_authorizes_only_its_matching_requirement(self):
  ids=("evt-a10000000000000000000000000000a1","evt-b20000000000000000000000000000b2")
  events=[]
  for event_id, assignee in zip(ids,("executor:a","executor:b")):
   event={"schema_version":"0.3.2","event_id":event_id,"type":"TASK","subtype":"dispatch","receipt_class":"claim","actor":"human:owner","subject":"task:"+event_id,"recorded_at":"2026-09-03T00:00:00Z","prior_refs":{"parent":[],"supports":[]},"extensions":{"dispatch":{"assignee":assignee,"surface":"project:reference"}},"body":"real binding fixture"}; event["content_hash"]=canonical.sha256_canonical(event); events.append(event)
  with tempfile.TemporaryDirectory() as td:
   directory=Path(td)
   for event in events: (directory/(event["event_id"]+".json")).write_bytes(canonical.stored_bytes(event))
   self.assertTrue(_prior_authorized(req("task:"+ids[0],"project:reference","executor:a"),list(reversed(ids)),directory))
   self.assertFalse(_prior_authorized(req("task:"+ids[1],"project:reference","executor:a"),[ids[0]],directory))
   self.assertFalse(_prior_authorized(req("task:"+ids[0],"project:wrong","executor:a"),[ids[0]],directory))
   self.assertFalse(_prior_authorized(req("task:"+ids[0],"project:reference","executor:wrong"),[ids[0]],directory))
   duplicate=dict(events[0]); duplicate["event_id"]="evt-c30000000000000000000000000000c3"; duplicate["content_hash"]=canonical.sha256_canonical({k:v for k,v in duplicate.items() if k!="content_hash"})
   (directory/(duplicate["event_id"]+".json")).write_bytes(canonical.stored_bytes(duplicate))
   self.assertFalse(_prior_authorized(req("task:"+ids[0],"project:reference","executor:a"),[ids[0],duplicate["event_id"]],directory))
   self.assertFalse(_prior_authorized(req("task:"+ids[0],"project:reference","executor:a"),["evt-absent000000000000000000000000"],directory))
 def test_each_dispatch_authorizes_only_matching_requirement_and_order_is_irrelevant(self):
  events={"a":{"subject":"task:a","extensions":{"dispatch":{"surface":"project:reference","assignee":"executor:a"}}},"b":{"subject":"task:b","extensions":{"dispatch":{"surface":"project:reference","assignee":"executor:b"}}}}
  with patch("blackbox_vnext.slice4.trusted_corpus.load_trusted_corpus",return_value=Corpus(events)), patch("blackbox_vnext.slice4.dispatch.validate_dispatch",return_value=object()), patch("blackbox_vnext.cli.authorization_from_slice4",return_value=True):
   self.assertTrue(_prior_authorized(req("task:a","project:reference","executor:a"),["b","a"],Path("events")))
   self.assertTrue(_prior_authorized(req("task:b","project:reference","executor:b"),["a","b"],Path("events")))
   self.assertFalse(_prior_authorized(req("task:c","project:reference","executor:c"),["a"],Path("events")))
 def test_wrong_binding_or_ambiguous_dispatch_fails_closed(self):
  events={"a":{"subject":"task:a","extensions":{"dispatch":{"surface":"project:wrong","assignee":"executor:a"}}},"aa":{"subject":"task:a","extensions":{"dispatch":{"surface":"project:reference","assignee":"executor:a"}}},"ab":{"subject":"task:a","extensions":{"dispatch":{"surface":"project:reference","assignee":"executor:a"}}}}
  with patch("blackbox_vnext.slice4.trusted_corpus.load_trusted_corpus",return_value=Corpus(events)), patch("blackbox_vnext.slice4.dispatch.validate_dispatch",return_value=object()), patch("blackbox_vnext.cli.authorization_from_slice4",return_value=True):
   target=req("task:a","project:reference","executor:a")
   self.assertFalse(_prior_authorized(target,["a"],Path("events")))
   self.assertFalse(_prior_authorized(target,["aa","ab"],Path("events")))

if __name__ == "__main__": unittest.main()
