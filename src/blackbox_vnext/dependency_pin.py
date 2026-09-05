"""Slice 5's no-Git, product-local upstream dependency boundary."""
from __future__ import annotations
import ast, hashlib
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
# Slice1/Slice2/Slice4 accepted identities are provenance only; these are pins
# for the product files actually imported by Slice5 receipt/gate semantics.
PROVENANCE = {"slice1_commit": "87482a53b74f28b499bdba5d9a22719302a69d7d", "slice2_commit": "dea630dc7823cb5e98cd3b924ad66ea53cfeb9c8", "slice4_commit": "9b2de2d5fd85cd7ef24b36587f60f4200577239a"}
FILE_SHA256 = {
 "canonical.py":"4c75f9978bac607fe10816f04e504e2d11a997d1834ac027fa1b17e44d3827ef", "validity.py":"7a1859d9eabdb5d1c4b73fb0049c6abea2af87e807e7a3dc58848407f6a7f04a",
 "slice4/__init__.py":"047688416d3897f3bd33fcba04866298f34f8df2a6940bb68e0bed688607815b", "slice4/_slice1_path.py":"9a7c97414cba4478c855a6297c0f22afcd2ddb3ac8da31bfdd0b0082462a5892", "slice4/authority_profile.py":"b5d001f1d1ece15fd404e1ad75414470f954bf05825ef9ded26f9a0c34bb549f", "slice4/corpus_gate.py":"51673bd418e659382b50f648022d48b070a0ccafebb2cf793e4e7a4ef70f9853", "slice4/dependency_pin.py":"ebf828293c7f807d2c70e2c724d302f97227398d1398d8a0d08ece327aa3ebde", "slice4/dispatch.py":"42fa990eb9e020f7440363f3be735a0b56e97c06535dee488a48d224b73251c3", "slice4/state.py":"2f7fba150babeeeef3f116bef2b557572cd17210eccf27c14e1e5810e5a317b9", "slice4/trusted_corpus.py":"304a7a206aafc9e4991ce4b2ac2da460962d540efee341b8f31490d66434091b"}
API_PROFILE = {"canonical.py": ("canonical_bytes", "sha256_canonical", "verify_content_hash"), "validity.py": ("is_event_shape_ok", "lexical_event_id_ok"), "slice4/dispatch.py": ("DispatchDecision", "validate_dispatch", "is_gate_authorization"), "slice4/dependency_pin.py": ("gate_semantic_entrypoint",)}
@dataclass(frozen=True)
class DependencyResult: ok: bool; mismatches: tuple[str, ...]
class DependencyDrift(RuntimeError): pass
def _symbols(path: Path) -> set[str]:
 return {n.name for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
def verify_dependency() -> DependencyResult:
 bad=[]
 for rel, expected in FILE_SHA256.items():
  path=PACKAGE/rel
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected: bad.append(rel)
 for rel, required in API_PROFILE.items():
  path=PACKAGE/rel; found=_symbols(path) if path.is_file() else set()
  bad.extend(f"{rel}:{name}" for name in required if name not in found)
 return DependencyResult(not bad, tuple(bad))
def gate_semantic_entrypoint() -> DependencyResult:
 result=verify_dependency()
 if not result.ok: raise DependencyDrift(",".join(result.mismatches))
 return result
