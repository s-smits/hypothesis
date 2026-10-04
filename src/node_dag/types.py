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


class FastaFile(Entity):
    """A FASTA file: its whole text, so its records can be read as entities.

    ``sequence`` is the file's contents, so the id hashes the file itself: a file
    saved under ``results/files/`` is named by exactly this id, and a ``FASTAFile
    <id>`` in a goal means the file saved under that id.

    Args:
        sequence: The file's text: ``>`` header lines and sequences.
        name: The file's name, for display. Not part of the id.
    """

    kind: Literal["fasta_file"] = "fasta_file"
    name: str = ""

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        if not any(line.startswith(">") for line in v.splitlines()):
            raise ValueError(f"Not a FASTA file, no >header line: {v[:40]!r}")
        return v

    @computed_field
    @property
    def display(self) -> str:
        """The file's name, or what it is."""
        return self.name or "a FASTA file"


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


class StructureAlignment(Entity):
    """How one protein structure superposes on another, as TM-align measured it.

    The two structures need not share a length or a sequence: TM-align finds the
    alignment itself, from the Cα traces, so ``aligned_length`` is how many residue
    pairs it ended up using and is usually shorter than either chain.

    ``rmsd`` covers those aligned pairs only, under the superposition that maximises
    the TM-score rather than the one that minimises the RMSD. A pair of structures
    that align over a short, well-fitting fragment can therefore show a lower
    ``rmsd`` than one that aligns over the whole fold, so an ``rmsd`` is only
    comparable alongside its ``aligned_length``. The TM-scores already account for
    length: they run from 0 to 1, and are normalised by the query's and the
    reference's residue count respectively, so the two differ whenever the chains do.

    Args:
        sequence: The query structure's amino acids.
        structure_id: The id of the query structure, the one that was superposed.
        reference_id: The id of the structure it was superposed on.
        rmsd: The deviation over the aligned residue pairs, in ångströms.
        tm_score_query: TM-score normalised by the query's residue count.
        tm_score_reference: TM-score normalised by the reference's residue count.
        aligned_length: How many residue pairs the alignment used.
        seq_identity: The fraction of those pairs holding the same amino acid.
    """

    kind: Literal["structure_alignment"] = "structure_alignment"
    structure_id: str
    reference_id: str
    rmsd: float
    tm_score_query: float
    tm_score_reference: float
    aligned_length: int
    seq_identity: float

    @computed_field
    @property
    def id(self) -> str:
        """A short, stable hash of the kind, both structure ids and the measurements.

        The measurements are part of it because the same pair of structures aligned
        under a different configuration, such as on another chain, is a different
        result, and two results that shared an id would merge into one.
        """
        measured = (
            self.rmsd,
            self.tm_score_query,
            self.tm_score_reference,
            self.aligned_length,
            self.seq_identity,
        )
        return hashlib.sha256(
            f"{self.kind}:{self.structure_id}:{self.reference_id}:{measured}".encode()
        ).hexdigest()[:12]

    @computed_field
    @property
    def display(self) -> str:
        """The RMSD, how many residues it covers, and the reference TM-score."""
        return (
            f"RMSD {self.rmsd:.2f} Å over {self.aligned_length} residues; "
            f"TM-score {self.tm_score_reference:.3f}"
        )


# Add a new entity type to both.
Value = Annotated[
    Dna
    | Rna
    | AminoAcidSequence
    | ProteinStructure
    | FastaFile
    | ProteinContacts
    | StructureAlignment,
    Discriminator("kind"),
]
TYPES: dict[str, type[Entity]] = {
    "dna": Dna,
    "rna": Rna,
    "amino_acid_sequence": AminoAcidSequence,
    "protein_structure": ProteinStructure,
    "fasta_file": FastaFile,
    "protein_contacts": ProteinContacts,
    "structure_alignment": StructureAlignment,
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
