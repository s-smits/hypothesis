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
