from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, field_validator

from node_dag.dna import ATOMS_PER_BASE, CODON_TABLE


class AminoAcidSequence(BaseModel):
    """A protein sequence: one-letter amino acid codes.

    Args:
        sequence: The amino acids, or ``*`` for stop codons.
    """

    kind: Literal["amino_acid_sequence"] = "amino_acid_sequence"
    sequence: str

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


class Dna(BaseModel):
    """A coding DNA sequence: upper-case A, C, G, T, a whole number of codons.

    Args:
        sequence: The bases, 5' to 3'.
    """

    kind: Literal["dna"] = "dna"
    sequence: str

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


class Rna(BaseModel):
    """A messenger RNA sequence: upper-case A, C, G, U, a whole number of codons.

    The coding strand, read 5' to 3', so it is a coding DNA sequence with U for T. It is
    its own kind, not a subtype of a shared nucleic acid: a port is typed with exactly
    one kind, so a node that wants DNA takes ``Dna`` and one that wants RNA takes this.

    Args:
        sequence: The bases, 5' to 3'.
    """

    kind: Literal["rna"] = "rna"
    sequence: str

    @field_validator("sequence")
    @classmethod
    def _check(cls, v: str) -> str:
        if len(v) % 3 or set(v) - set("ACGU"):
            raise ValueError(f"Not whole codons of A, C, G, U: {v!r}")
        return v

    def codons(self) -> list[Codon]:
        """The sequence split into codons, each with its amino acid."""
        # CODON_TABLE is keyed by DNA codons. The genetic code is the same code once U
        # is read as T, so each codon is looked up back-transcribed rather than through
        # a second table that could drift from the first.
        return [
            Codon(codon=c, amino_acid=CODON_TABLE[c.replace("U", "T")])
            for c in (self.sequence[i : i + 3] for i in range(0, len(self.sequence), 3))
        ]

    def protein(self) -> str:
        """The amino acids as a one-letter string."""
        return "".join(c.amino_acid for c in self.codons())


class Score(BaseModel):
    """A number that rates something.

    Args:
        value: The score.
    """

    kind: Literal["score"] = "score"
    value: float


Value = Annotated[Dna | Rna | AminoAcidSequence | Score, Discriminator("kind")]
TYPES: dict[str, type[BaseModel]] = {
    "dna": Dna,
    "rna": Rna,
    "amino_acid_sequence": AminoAcidSequence,
    "score": Score,
}
