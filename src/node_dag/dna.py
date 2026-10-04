from collections.abc import Collection
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


def codon_set(v: tuple[str, ...]) -> tuple[str, ...]:
    """Upper-case and de-duplicate codons, in order. Raises ValueError on a non-codon."""
    if bad := [c for c in v if c.upper() not in CODON_TABLE]:
        raise ValueError(f"Not a codon: {bad}")
    return tuple(dict.fromkeys(c.upper() for c in v))


def codons(sequence: str) -> list[str]:
    """The in-frame codons of a coding sequence; the last is partial if unaligned."""
    return [sequence[i : i + 3] for i in range(0, len(sequence), 3)]


_COMPLEMENT = str.maketrans("ACGTU", "TGCAA")


def reverse_complement(sequence: str) -> str:
    """The reverse complement of a DNA or RNA sequence."""
    return sequence.translate(_COMPLEMENT)[::-1]


def gc_fraction(sequence: str) -> float:
    """The fraction of G and C bases, 0 for an empty sequence."""
    if not sequence:
        return 0.0
    return sum(b in "GC" for b in sequence) / len(sequence)


def gc_window_fractions(sequence: str, window: int) -> list[float]:
    """The GC fraction of every ``window``-base window, at every start position.

    A sequence shorter than the window counts as a single window.
    """
    width = min(window, len(sequence))
    if width == 0:
        return [0.0]
    counts = [0]
    for b in sequence:
        counts.append(counts[-1] + (b in "GC"))
    return [
        (counts[i + width] - counts[i]) / width
        for i in range(len(sequence) - width + 1)
    ]


def motif_hits(sequence: str, motifs: Collection[str]) -> list[tuple[int, int]]:
    """The ``(start, end)`` of every occurrence of every motif, sorted by start.

    Overlapping occurrences each count. An empty motif list gives no hits.
    """
    hits = [
        (i, i + len(m))
        for m in motifs
        for i in range(len(sequence) - len(m) + 1)
        if sequence.startswith(m, i)
    ]
    return sorted(hits)


def check_motifs(motifs: tuple[str, ...]) -> tuple[str, ...]:
    """Reject an empty motif list or a motif that is not upper-case DNA."""
    if not motifs:
        raise ValueError("No motifs given.")
    for m in motifs:
        if len(m) < 2 or set(m) - set("ACGT"):
            raise ValueError(f"Not an upper-case DNA motif of at least 2 bases: {m!r}")
    return motifs


def check_codon_weights(table: dict[str, float]) -> dict[str, float]:
    """Reject a codon weight table with a non-codon key or a negative weight."""
    for codon, weight in table.items():
        if codon not in CODON_TABLE:
            raise ValueError(f"Not an upper-case DNA codon: {codon!r}")
        if weight < 0:
            raise ValueError(f"Negative weight for {codon}: {weight}")
    return table
