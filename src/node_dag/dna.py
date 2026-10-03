from itertools import product

BASES = "TCAG"

# The standard genetic code, codons ordered by BASES: TTT, TTC, TTA, TTG, TCT, ...
_AMINO_ACIDS = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"

CODON_TABLE: dict[str, str] = {
    "".join(codon): aa for codon, aa in zip(product(BASES, repeat=3), _AMINO_ACIDS)
}

# Synonymous codons for each amino acid (``*`` is stop).
SYNONYMS: dict[str, tuple[str, ...]] = {
    aa: tuple(c for c, a in CODON_TABLE.items() if a == aa) for aa in set(_AMINO_ACIDS)
}

# Atoms in one nucleotide residue of a DNA chain: the deoxynucleotide monophosphate
# less a water, so C10H12N5O5P is 33 atoms.
ATOMS_PER_BASE: dict[str, int] = {"A": 33, "C": 31, "G": 34, "T": 33}


def codon_set(v: tuple[str, ...]) -> tuple[str, ...]:
    """Upper-case and de-duplicate codons, in order. Raises ValueError on a non-codon."""
    if bad := [c for c in v if c.upper() not in CODON_TABLE]:
        raise ValueError(f"Not a codon: {bad}")
    return tuple(dict.fromkeys(c.upper() for c in v))


def codons(sequence: str) -> list[str]:
    """The in-frame codons of ``sequence`` from its first base. A partial one at the end is left out."""
    return [sequence[i : i + 3] for i in range(0, len(sequence) - 2, 3)]
