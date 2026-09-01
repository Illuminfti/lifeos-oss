"""One explicit public ontology. Folders are storage indexes, never meaning."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class TypeSpec:
    id: str
    root: str
    id_prefix: str
    kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    id: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    object_kind: str
    temporal: bool
    cardinality: str
    inverse: str | None = None
    symmetric: bool = False
    contradiction_policy: str = "coexist"
    allowed_qualifiers: tuple[str, ...] = ()


class OntologyError(ValueError):
    pass


class Ontology:
    def __init__(self, types_doc: dict[str, Any], predicates_doc: dict[str, Any]):
        self.version = str(types_doc["version"])
        self.types = {
            type_id: TypeSpec(
                id=type_id,
                root=str(spec["root"]),
                id_prefix=str(spec["id_prefix"]),
                kinds=tuple(spec.get("kinds", [])),
            )
            for type_id, spec in types_doc["types"].items()
        }
        self.predicates = {
            predicate_id: PredicateSpec(
                id=predicate_id,
                domain=tuple(spec.get("domain", ["*"])),
                range=tuple(spec.get("range", [])),
                object_kind=str(spec["object_kind"]),
                temporal=bool(spec.get("temporal", False)),
                cardinality=str(spec.get("cardinality", "many")),
                inverse=spec.get("inverse"),
                symmetric=bool(spec.get("symmetric", False)),
                contradiction_policy=str(spec.get("contradiction_policy", "coexist")),
                allowed_qualifiers=tuple(spec.get("allowed_qualifiers", [])),
            )
            for predicate_id, spec in predicates_doc["predicates"].items()
        }

    @classmethod
    def default(cls) -> "Ontology":
        package = resources.files("lifeos.resources")
        with package.joinpath("ontology.yaml").open("r", encoding="utf-8") as handle:
            types_doc = yaml.safe_load(handle)
        with package.joinpath("predicates.yaml").open("r", encoding="utf-8") as handle:
            predicates_doc = yaml.safe_load(handle)
        return cls(types_doc, predicates_doc)

    @classmethod
    def from_paths(cls, ontology_path: Path, predicates_path: Path) -> "Ontology":
        with Path(ontology_path).open("r", encoding="utf-8") as handle:
            types_doc = yaml.safe_load(handle)
        with Path(predicates_path).open("r", encoding="utf-8") as handle:
            predicates_doc = yaml.safe_load(handle)
        return cls(types_doc, predicates_doc)

    def validate_type(self, type_id: str, kind: str | None = None) -> TypeSpec:
        spec = self.types.get(type_id)
        if spec is None:
            raise OntologyError(f"unknown canonical type: {type_id}")
        if kind is not None and spec.kinds and kind not in spec.kinds:
            raise OntologyError(f"invalid kind {kind!r} for type {type_id!r}")
        return spec

    def validate_claim(
        self,
        *,
        predicate_id: str,
        subject_type: str,
        object_kind: str,
        object_type: str | None = None,
        qualifiers: dict[str, Any] | None = None,
    ) -> PredicateSpec:
        self.validate_type(subject_type)
        spec = self.predicates.get(predicate_id)
        if spec is None:
            raise OntologyError(f"unknown predicate: {predicate_id}")
        if "*" not in spec.domain and subject_type not in spec.domain:
            raise OntologyError(
                f"predicate {predicate_id!r} does not accept subject type {subject_type!r}"
            )
        if object_kind != spec.object_kind:
            raise OntologyError(
                f"predicate {predicate_id!r} requires {spec.object_kind}, got {object_kind}"
            )
        if object_kind == "entity_ref":
            if object_type is None:
                raise OntologyError("entity_ref claims require object_type")
            self.validate_type(object_type)
            if object_type not in spec.range:
                raise OntologyError(
                    f"predicate {predicate_id!r} does not accept object type {object_type!r}"
                )
        elif object_type is not None and object_type not in spec.range:
            raise OntologyError(
                f"predicate {predicate_id!r} does not accept datatype {object_type!r}"
            )
        unknown_qualifiers = set((qualifiers or {})) - set(spec.allowed_qualifiers)
        if spec.allowed_qualifiers and unknown_qualifiers:
            raise OntologyError(
                f"predicate {predicate_id!r} has unsupported qualifiers: "
                + ", ".join(sorted(unknown_qualifiers))
            )
        return spec

    @property
    def spawnable_types(self) -> tuple[str, ...]:
        return tuple(self.types)
