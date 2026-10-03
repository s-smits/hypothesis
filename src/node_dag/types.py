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
        if set(v) - set("ACGT"):
            raise ValueError(f"Not A, C, G, T: {v!r}")
        return v


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
    """A protein sequence and its 3D macromolecular structure, as mmCIF.

    Args:
        sequence: The amino acids, or ``*`` for stop codons.
        structure: The macromolecular 3D structure, as an mmCIF string.
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


class ResidueContact(BaseModel):
    """Two residues on different chains, and how close they come.

    A residue is ``chain:res_name:res_num:insertion_code``, with the author chain,
    residue number and insertion code as a PDB file shows them, e.g. ``H:ALA:52:A``.
    A residue with no insertion code ends in ``:``, e.g. ``L:LYS:7:``.

    Args:
        residue_a: A residue on one side of the interface.
        residue_b: A residue on the other side.
        distance: How far apart they are, in ångströms.
    """

    residue_a: str
    residue_b: str
    distance: float


class ProteinContacts(Entity):
    """The contacts between the chains of one protein structure.

    Args:
        sequence: The structure's amino acids.
        structure_id: The id of the structure the contacts were found in.
        contacts: Each contact, ordered by distance, closest first.
    """

    kind: Literal["protein_contacts"] = "protein_contacts"
    structure_id: str
    contacts: list[ResidueContact]

    @computed_field
    @property
    def id(self) -> str:
        """A short, stable hash of the kind, structure id and contacts."""
        contacts = [(c.residue_a, c.residue_b, c.distance) for c in self.contacts]
        return hashlib.sha256(
            f"{self.kind}:{self.structure_id}:{contacts}".encode()
        ).hexdigest()[:12]

    @computed_field
    @property
    def display(self) -> str:
        """How many contacts, and the closest, e.g. ``3 contacts; closest ...``."""
        if not self.contacts:
            return "0 contacts"
        c = self.contacts[0]
        return (
            f"{len(self.contacts)} contacts; closest {c.residue_a}–{c.residue_b} "
            f"at {c.distance:.2f} Å"
        )


# Add a new entity type to both.
Value = Annotated[
    Dna | Rna | AminoAcidSequence | ProteinStructure | ProteinContacts,
    Discriminator("kind"),
]
TYPES: dict[str, type[Entity]] = {
    "dna": Dna,
    "rna": Rna,
    "amino_acid_sequence": AminoAcidSequence,
    "protein_structure": ProteinStructure,
    "protein_contacts": ProteinContacts,
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
