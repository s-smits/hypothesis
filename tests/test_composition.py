"""The two checks that come from reading, rather than from the obvious framing.

The builder asked for a global GC window, which is the natural thing to ask for and
not what the literature says breaks a recoding. GC matters locally, and the sharper
rule is about the first six codons and barely about GC at all.
"""

import pytest

from node_dag.nodes.decisions.gc_in_range.config import GcInRangeConfig
from node_dag.nodes.decisions.gc_in_range.function import GcInRange, gc_fraction
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.nodes.tools.start_region_composition.config import (
    StartRegionCompositionConfig,
)
from node_dag.nodes.tools.start_region_composition.function import (
    StartRegionComposition,
)
from node_dag.types import Dna

GENE = Dna(sequence="ATGTCGTCAGCTTAA")  # MSSA*, 40% GC


def _gc(sequence: str, low: float, high: float, window: int = 0) -> bool:
    node = GcInRange(GcInRangeConfig(low=low, high=high, window=window))
    return node.run(sequence=Dna(sequence=sequence))


def _start(sequence: str, codons: int = 6) -> float:
    node = StartRegionComposition(StartRegionCompositionConfig(codons=codons))
    return node.run(sequence=Dna(sequence=sequence)).value


def test_gc_fraction_counts_g_and_c():
    assert gc_fraction("GCGC") == 1.0
    assert gc_fraction("ATAT") == 0.0
    assert gc_fraction("ATGC") == 0.5
    assert gc_fraction("") == 0.0


def test_a_sequence_in_range_passes_and_one_outside_does_not():
    assert _gc(GENE.sequence, 0.3, 0.7) is True  # 40%
    assert _gc(GENE.sequence, 0.5, 0.7) is False


def test_recoding_moves_gc_enough_to_change_the_answer():
    """Synonymous codons differ in their third base, so a recoding cannot miss GC."""
    recoded = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "TCA"))).run(sequence=GENE)
    assert recoded.protein() == GENE.protein()
    assert gc_fraction(GENE.sequence) > gc_fraction(recoded.sequence)
    assert _gc(GENE.sequence, 0.38, 0.7) is True
    assert _gc(recoded.sequence, 0.38, 0.7) is False


def test_a_window_catches_a_patch_the_whole_sequence_hides():
    """The reason window exists: the average is fine and the sequence is not.

    Half A/T and half G/C averages 50%, which every global check waves through, while
    the GC-rich half is exactly what defeats synthesis.
    """
    patchy = "ATATATATATAT" + "GCGCGCGCGCGC"
    assert gc_fraction(patchy) == 0.5
    assert _gc(patchy, 0.3, 0.7) is True, "the global figure hides it"
    assert _gc(patchy, 0.3, 0.7, window=6) is False, "the window should catch it"


def test_a_window_longer_than_the_sequence_checks_the_whole_thing():
    assert _gc(GENE.sequence, 0.3, 0.7, window=600) is True


def test_low_above_high_is_rejected():
    with pytest.raises(ValueError, match="above high"):
        GcInRangeConfig(low=0.8, high=0.2)


def test_an_a_rich_start_scores_above_a_g_rich_one():
    """The published rule: maximise A, minimise G over the first six codons."""
    assert _start("ATGAAAGCAAAAGCAAAA") > 0
    assert _start("ATGGGCGGCGGCGGCGGC") < 0
    assert _start("ATGAAAGCAAAAGCAAAA") > _start("ATGGGCGGCGGCGGCGGC")


def test_only_the_first_codons_are_read():
    """A change past the window must not move the score, or it is not this rule."""
    head = "ATGAAAGCAAAAGCAAAA"
    assert _start(head + "GGGGGGGGG") == _start(head + "AAAAAAAAA")


def test_a_synonymous_swap_in_the_second_codon_changes_the_score():
    """Why this is worth checking at all.

    Both sequences code for the same protein and hold no target codon, so every check
    the loop had before this one passes them equally. The published effect of that
    difference is up to ten-fold on how much protein comes out.
    """
    a_start = Dna(sequence="ATGAAAGCTTAA")  # MKA*
    g_start = Dna(sequence="ATGAAGGCTTAA")  # MKA* too: AAA and AAG are both lysine
    assert a_start.protein() == g_start.protein()
    assert _start(a_start.sequence) > _start(g_start.sequence)
