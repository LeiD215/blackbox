"""Read-only deterministic fixture/filesystem readback adapters."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .receipt import IdentityBinding, Observation
from .dependency_pin import gate_semantic_entrypoint


class ReadbackError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _observation(
    binding: IdentityBinding,
    *, algorithm: str,
    identity: str,
    read_path: str,
    source_id: str,
    verifier: str,
    independent_source: bool,
    transport_ok: bool = True,
    observed_at: str | None = None,
    provenance: str = "local-deterministic-fixture",
) -> Observation:
    return Observation(
        target=binding.target,
        subject=binding.subject,
        artifact_identity=binding.artifact_identity,
        input_scope=binding.input_scope,
        algorithm=algorithm,
        observed_identity=identity,
        read_path=read_path,
        source_id=source_id,
        verifier=verifier,
        executor=binding.executor,
        independent_source=independent_source,
        transport_ok=transport_ok,
        observed_at=observed_at or _utc_now(),
        provenance=provenance,
    )


@dataclass(frozen=True)
class FixtureReadbackAdapter:
    fixtures: dict[str, bytes | str | None]
    verifier: str
    source_id: str = "fixture:independent"
    independent_source: bool = True
    available: bool = True

    def read(self, binding: IdentityBinding, *, observed_at: str | None = None) -> Observation:
        gate_semantic_entrypoint()
        if not self.available or binding.target not in self.fixtures:
            return _observation(
                binding, algorithm="text", identity="unavailable",
                read_path=f"fixture://{binding.target}", source_id=self.source_id,
                verifier=self.verifier, independent_source=self.independent_source,
                transport_ok=False, observed_at=observed_at,
            )
        value = self.fixtures[binding.target]
        if value is None:
            algorithm, identity = "absent", "absent"
        elif isinstance(value, bytes):
            algorithm = "sha256"
            identity = "sha256:" + hashlib.sha256(value).hexdigest()
        elif isinstance(value, str):
            algorithm, identity = "text", value
        else:
            raise ReadbackError("fixture value must be bytes, str, or None")
        return _observation(
            binding, algorithm=algorithm, identity=identity,
            read_path=f"fixture://{binding.target}", source_id=self.source_id,
            verifier=self.verifier, independent_source=self.independent_source,
            observed_at=observed_at,
        )


@dataclass(frozen=True)
class FilesystemReadbackAdapter:
    root: Path
    verifier: str
    source_id: str = "filesystem:independent"
    independent_source: bool = True

    def read(
        self,
        binding: IdentityBinding,
        relative_path: str,
        *,
        observed_at: str | None = None,
    ) -> Observation:
        gate_semantic_entrypoint()
        path = PurePosixPath(relative_path.replace("\\", "/"))
        if (
            not relative_path or path.is_absolute() or relative_path.startswith("-")
            or any(part in ("", ".", "..") for part in path.parts)
            or ":" in relative_path
        ):
            raise ReadbackError("PATH-ESCAPE")
        root = self.root.resolve(strict=True)
        candidate = root.joinpath(*path.parts)
        current = root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise ReadbackError("SYMLINK-REFUSED")
        resolved = candidate.resolve(strict=True)
        if root != resolved and root not in resolved.parents:
            raise ReadbackError("PATH-ESCAPE")
        if not resolved.is_file():
            raise ReadbackError("NOT-A-REGULAR-FILE")
        before = os.stat(resolved)
        data = resolved.read_bytes()
        after = os.stat(resolved)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ReadbackError("READ-MUTATION-DETECTED")
        identity = "sha256:" + hashlib.sha256(data).hexdigest()
        return _observation(
            binding, algorithm="sha256", identity=identity,
            read_path=f"file://fixture-root/{path.as_posix()}",
            source_id=self.source_id, verifier=self.verifier,
            independent_source=self.independent_source,
            observed_at=observed_at, provenance="local-read-only-filesystem",
        )
