import hashlib
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    Discriminator,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    computed_field,
    field_validator,
    model_serializer,
)

from node_dag.dna import ATOMS_PER_BASE


class Entity(BaseModel):
    """Something a DAG passes around in lists: a sequence with a unique ``id``.

    The id is a hash of the kind and the sequence, so the same sequence always has the
    same id. That keeps results cacheable, and identical entities merge into one.

    Args:
        sequence: The residues, 5' to 3' for DNA.
    """

    kind: str = "entity"
    sequence: str

    @computed_field
    @property
    def id(self) -> str:
        """A short, stable hash of the kind and the sequence."""
        return hashlib.sha256(f"{self.kind}:{self.sequence}".encode()).hexdigest()[:12]

    @computed_field
    @property
    def display(self) -> str:
        """The sequence as people read it. Subclasses lay it out for their kind."""
        return self.sequence

    def __str__(self) -> str:
        """The display string."""
        return self.display

    @model_serializer(mode="wrap")
    def _serialize(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        data = handler(self)
        # A config hash is made from what a config is, not from how its sequences are
        # shown, so that changing a display string never changes a hash.
        if info.context and info.context.get("hashing"):
            data.pop("display", None)
        return data


class AminoAcidSequence(Entity):
    """A protein sequence: one-letter amino acid codes.

    Args:
        sequence: The amino acids, or ``*`` for stop codons.
    """

    kind: Literal["amino_acid_sequence"] = "amino_acid_sequence"

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        valid_chars = set("ACDEFGHIKLMNPQRSTVWY*")
        if set(v) - valid_chars:
            raise ValueError(f"Not valid amino acid codes: {v!r}")
        return v


class NucleicAcid(Entity):
    """DNA or RNA. A node that takes either types its port with this."""

    kind: str = "nucleic_acid"


class Dna(NucleicAcid):
    """A DNA sequence: upper-case A, C, G, T.

    Args:
        sequence: The bases, 5' to 3'.
    """

    kind: Literal["dna"] = "dna"

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        if set(v) - set(ATOMS_PER_BASE):
            raise ValueError(f"Not A, C, G, T: {v!r}")
        return v

    def atom_count(self) -> int:
        """Total atoms in the DNA strand."""
        return sum(ATOMS_PER_BASE[b] for b in self.sequence)


class Rna(NucleicAcid):
    """An RNA sequence: upper-case A, C, G, U.

    Args:
        sequence: The bases, 5' to 3'.
    """

    kind: Literal["rna"] = "rna"

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        if set(v) - set("ACGU"):
            raise ValueError(f"Not A, C, G, U: {v!r}")
        return v


class Score(BaseModel):
    """A number that rates an entity. A scoring node returns one per score name.

    Args:
        value: The score.
    """

    kind: Literal["score"] = "score"
    value: float


class ProteinStructure(Entity):
    """A protein sequence and its 3D macromolecular structure (e.g. PDB format).

    Args:
        sequence: The amino acids, or ``*`` for stop codons.
        structure: The macromolecular 3D structure, e.g. as a PDB string.
    """

    kind: Literal["protein_structure"] = "protein_structure"
    structure: str

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        valid_chars = set("ACDEFGHIKLMNPQRSTVWY*")
        if set(v) - valid_chars:
            raise ValueError(f"Not valid amino acid codes: {v!r}")
        return v

    @computed_field
    @property
    def id(self) -> str:
        """A short, stable hash of the kind, sequence, and structure."""
        return hashlib.sha256(
            f"{self.kind}:{self.sequence}:{self.structure}".encode()
        ).hexdigest()[:12]


# Add a new entity type to both.
Value = Annotated[
    Dna | Rna | AminoAcidSequence | ProteinStructure, Discriminator("kind")
]
TYPES: dict[str, type[Entity]] = {
    "dna": Dna,
    "rna": Rna,
    "amino_acid_sequence": AminoAcidSequence,
    "protein_structure": ProteinStructure,
}


class Table(BaseModel):
    """What flows along a DAG edge: entities and the scores they have so far.

    Args:
        items: The entities, in order, with no two sharing an id.
        scores: Maps a score column, ``<node name>__<config hash>__<score name>``, to
            the score of each entity id.
    """

    items: list[Value] = []
    scores: dict[str, dict[str, float]] = {}

    @classmethod
    def of(
        cls, items: list[Value], scores: dict[str, dict[str, float]] | None = None
    ) -> Self:
        """A table of ``items`` without repeats, keeping only their scores.

        A table with no items has no score columns.
        """
        unique = list({i.id: i for i in items}.values())
        ids = {i.id for i in unique}
        kept = {
            col: {k: v for k, v in by_id.items() if k in ids}
            for col, by_id in (scores or {}).items()
        }
        return cls(items=unique, scores={c: s for c, s in kept.items() if s})
