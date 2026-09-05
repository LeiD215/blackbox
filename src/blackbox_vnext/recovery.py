"""Deterministic offline recovery over canonical event files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .reingest import CorpusReingestResult, V_INVALID, V_UNKNOWN, reingest_events_dir
from .subtypes import SubtypeRegistry


@dataclass(frozen=True)
class RecoveryResult:
    state: str
    corpus: CorpusReingestResult
    git_required: bool = False


def recover(root: Path, registry: SubtypeRegistry) -> RecoveryResult:
    """Rebuild from records alone; Git and caches are never authority."""
    corpus = reingest_events_dir(root, registry)
    blocked = corpus.corpus_blocked or any(
        item.status in (V_INVALID, V_UNKNOWN) for item in corpus.verdicts
    )
    state = "NON-GREEN" if blocked else "TRUSTED"
    return RecoveryResult(state=state, corpus=corpus)
