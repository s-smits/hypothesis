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

from node_dag.dna import ATOMS_PER_BASE, CODON_TABLE


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


class Codon(BaseModel):
    """One codon and the amino acid it codes for.

    Args:
        codon: Three bases.
        amino_acid: One-letter amino acid code, or ``*`` for stop.
    """

    codon: str
    amino_acid: str


class Dna(Entity):
    """A coding DNA sequence: upper-case A, C, G, T, a whole number of codons.

    Args:
        sequence: The bases, 5' to 3'.
    """

    kind: Literal["dna"] = "dna"

    @computed_field
    @property
    def display(self) -> str:
        """The codons, separated by spaces: ``ATG GCT CTG``."""
        return " ".join(c.codon for c in self.codons())

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        if len(v) % 3 or set(v) - set(ATOMS_PER_BASE):
            raise ValueError(f"Not whole codons of A, C, G, T: {v!r}")
        return v

    def codons(self) -> list[Codon]:
        """The sequence split into codons, each with its amino acid."""
        return [
            Codon(codon=c, amino_acid=CODON_TABLE[c])
            for c in (self.sequence[i : i + 3] for i in range(0, len(self.sequence), 3))
        ]

    def protein(self) -> str:
        """The amino acids as a one-letter string."""
        return "".join(c.amino_acid for c in self.codons())

    def atom_count(self) -> int:
        """Total atoms in the DNA strand."""
        return sum(ATOMS_PER_BASE[b] for b in self.sequence)


class Score(BaseModel):
    """A number that rates an entity. A scoring node returns one per score name.

    Args:
        value: The score.
    """

    kind: Literal["score"] = "score"
    value: float


# Add a new entity type to both.
Value = Annotated[Dna | AminoAcidSequence, Discriminator("kind")]
TYPES: dict[str, type[Entity]] = {"dna": Dna, "amino_acid_sequence": AminoAcidSequence}


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
