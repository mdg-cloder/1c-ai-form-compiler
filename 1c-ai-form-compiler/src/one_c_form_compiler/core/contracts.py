"""Versioned public data contracts. These are scaffolding, not a parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePath
from typing import Any, Mapping, TypeAlias

CONTRACT_SCHEMA_VERSION = "1.0"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BuildStatus(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Immutable source bytes plus an optional display name; bytes stay authoritative."""

    raw_bytes: bytes
    source_name: str | None = None
    declared_encoding: str | None = None
    has_bom: bool | None = None
    newline: str | None = None

    @property
    def sha256(self) -> str:
        return sha256(self.raw_bytes).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.raw_bytes)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    raw: bytes
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("token span must satisfy 0 <= start <= end")
        if self.end - self.start != len(self.raw):
            raise ValueError("token raw byte length must match its source span")


@dataclass(frozen=True, slots=True)
class Node:
    """Loss-aware syntax node; `raw` preserves unmodeled profile-specific content."""

    kind: str
    raw: bytes
    start: int
    end: int
    children: tuple["Node | Token", ...] = ()
    properties: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("node span must satisfy 0 <= start <= end")


@dataclass(frozen=True, slots=True)
class FormProfile:
    """Evidence-bounded serialization profile descriptor."""

    profile_id: str
    format_name: str
    element_decoder: str
    platform_version: str
    operating_system: str
    python_version: str
    evidence_ref: str
    verified: bool


@dataclass(frozen=True, slots=True)
class ControlRef:
    """Explicit stable target descriptor; it does not assume numeric/GUID IDs."""

    profile_id: str
    form_address: str
    key: str


@dataclass(frozen=True, slots=True)
class Control:
    control_type: str
    name: str | None
    identifier: JsonScalar
    parent_key: str | None
    geometry: Mapping[str, int | float | None]
    properties: Mapping[str, JsonValue]
    source_span: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class StylePassport:
    schema_version: str
    profile_id: str
    grid_and_layout: Mapping[str, JsonValue]
    element_dimensions: Mapping[str, JsonValue]
    visual_tokens: Mapping[str, JsonValue]
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    platform_version: str = "8.3.27"          # descriptive: the platform line the profile was verified with
    source: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddControl:
    node: Node
    operation: str = field(default="add", init=False)


@dataclass(frozen=True, slots=True)
class ModifyControl:
    target: ControlRef
    changes: Mapping[str, JsonValue]
    operation: str = field(default="modify", init=False)


@dataclass(frozen=True, slots=True)
class RemoveControl:
    target: ControlRef
    operation: str = field(default="remove", init=False)


PatchOperation: TypeAlias = AddControl | ModifyControl | RemoveControl


@dataclass(frozen=True, slots=True)
class PatchPlan:
    schema_version: str
    source_sha256: str
    form_address: str
    operations: tuple[PatchOperation, ...]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    source_name: str | None = None
    byte_offset: int | None = None
    line: int | None = None
    column: int | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BuildResult:
    status: BuildStatus
    output_path: PurePath | None
    output_sha256: str | None
    diagnostics: tuple[Diagnostic, ...] = ()
    run_id: str | None = None
