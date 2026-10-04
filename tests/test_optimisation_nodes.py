import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from node_dag.dag import Dag
from node_dag.dna import (
    codons,
    gc_window_fractions,
    longest_inverted_repeat,
    longest_repeat,
    motif_hits,
    repeat_fraction,
    reverse_complement,
)
from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
from node_dag.nodes.tools.codon_adaptation.function import CodonAdaptation
from node_dag.nodes.tools.codon_optimise.config import CodonOptimiseConfig
from node_dag.nodes.tools.codon_optimise.function import CodonOptimise
from node_dag.nodes.tools.codon_pair_score.config import CodonPairScoreConfig
from node_dag.nodes.tools.codon_pair_score.function import CodonPairScore
from node_dag.nodes.tools.dinucleotide_bias.config import DinucleotideBiasConfig
from node_dag.nodes.tools.dinucleotide_bias.function import DinucleotideBias
from node_dag.nodes.tools.domesticate.config import DomesticateConfig
from node_dag.nodes.tools.domesticate.function import Domesticate
from node_dag.nodes.tools.gc_content.config import GcContentConfig
from node_dag.nodes.tools.gc_content.function import GcContent
from node_dag.nodes.tools.gc_target_recode.config import GcTargetRecodeConfig
from node_dag.nodes.tools.gc_target_recode.function import GcTargetRecode
from node_dag.nodes.tools.motif_count.config import MotifCountConfig
from node_dag.nodes.tools.motif_count.function import MotifCount
from node_dag.nodes.tools.mrna_5prime_mfe.config import Mrna5primeMfeConfig
from node_dag.nodes.tools.mrna_5prime_mfe.function import Mrna5primeMfe
from node_dag.nodes.tools.mrna_fold_energy.config import MrnaFoldEnergyConfig
from node_dag.nodes.tools.mrna_fold_energy.function import MrnaFoldEnergy
from node_dag.nodes.tools.protein_to_dna.config import ProteinToDnaConfig
from node_dag.nodes.tools.protein_to_dna.function import ProteinToDna
from node_dag.nodes.tools.repeat_score.config import RepeatScoreConfig
from node_dag.nodes.tools.repeat_score.function import RepeatScore
from node_dag.nodes.tools.resample_synonymous.config import ResampleSynonymousConfig
from node_dag.nodes.tools.resample_synonymous.function import ResampleSynonymous
from node_dag.types import AminoAcidSequence, Dna, Rna

REF = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *
# GCT is twice GCC's weight; the other alanine codons sit between.
WEIGHTS = {"ATG": 1.0, "GCT": 2.0, "GCC": 1.0, "GCA": 0.5, "GCG": 0.25}


def _protein(s: Dna) -> str:
    return str(Seq(s.sequence).translate())


# --- dna helpers -------------------------------------------------------


def test_reverse_complement_reads_the_opposite_strand():
    assert reverse_complement("ACGT") == "ACGT"
    assert reverse_complement("GGTCTC") == "GAGACC"
    assert reverse_complement("ACGU") == "ACGT"


def test_gc_window_fractions_slide_and_clip():
    assert gc_window_fractions("GGAT", 2) == [1.0, 0.5, 0.0]
    assert gc_window_fractions("AT", 4) == [0.0]  # Shorter than the window.
    assert gc_window_fractions("", 4) == [0.0]


def test_motif_hits_count_overlaps_in_order():
    assert motif_hits("AAAA", {"AA"}) == [(0, 2), (1, 3), (2, 4)]
    assert motif_hits("AAAA", {"TT"}) == []


def test_longest_repeat_finds_the_longest_substring_seen_twice():
    assert longest_repeat("ATGCATGC") == 4  # ATGC, at 0 and 4.
    assert longest_repeat("AAAA") == 3  # Occurrences may overlap.
    assert longest_repeat("ACGT") == 0  # Every base distinct.
    assert longest_repeat("") == 0


def test_longest_inverted_repeat_finds_the_longest_hairpin():
    assert longest_inverted_repeat("ACGT") == 4  # Its own reverse complement.
    assert longest_inverted_repeat("GGGGCCCC") == 8
    assert longest_inverted_repeat("AAAA") == 0  # TTTT is nowhere in it.
    assert longest_inverted_repeat("") == 0


def test_repeat_fraction_covers_the_repeated_positions_only():
    assert repeat_fraction("ATGCATGC", 4) == 1.0  # Both halves repeat.
    assert repeat_fraction("ATGCATGC", 5) == 0.0  # No 5-mer occurs twice.
    assert repeat_fraction("ATGCATGCTT", 4) == pytest.approx(0.8)
    assert repeat_fraction("ACG", 4) == 0.0  # Shorter than the window.
    assert repeat_fraction("", 4) == 0.0


# --- codon_optimise ----------------------------------------------------


def test_codon_optimise_most_frequent_picks_the_best_synonym():
    node = CodonOptimise(CodonOptimiseConfig(codon_weights=WEIGHTS))
    (out,) = node.run(sequence=[Dna(sequence="ATGGCC")])
    assert out == Dna(sequence="ATGGCT")  # GCT outweighs GCC.


def test_codon_optimise_least_frequent_picks_the_worst_synonym():
    node = CodonOptimise(
        CodonOptimiseConfig(codon_weights=WEIGHTS, strategy="least_frequent")
    )
    (out,) = node.run(sequence=[Dna(sequence="ATGGCT")])
    assert out == Dna(sequence="ATGGCG")


def test_codon_optimise_keeps_what_the_table_says_nothing_about():
    node = CodonOptimise(CodonOptimiseConfig(codon_weights={"ATG": 1.0}))
    # No alanine weight is given, so GCT stays even under most_frequent.
    assert node.run(sequence=[Dna(sequence="ATGGCT")]) == [Dna(sequence="ATGGCT")]


def test_codon_optimise_weighted_sample_is_seeded_per_sequence():
    table = {**WEIGHTS, "CTG": 1.0, "TTA": 1.0}
    config = CodonOptimiseConfig(codon_weights=table, strategy="weighted_sample")
    node = CodonOptimise(config)
    seq = Dna(sequence="ATGCTGTTA")
    assert node.run(sequence=[seq]) == node.run(sequence=[seq])
    other = Dna(sequence="ATGCTGCTG")
    assert node.run(sequence=[other, seq])[1] == node.run(sequence=[seq])[0]
    assert _protein(node.run(sequence=[seq])[0]) == _protein(seq)


def test_codon_optimise_rejects_a_bad_table():
    with pytest.raises(ValidationError, match="Not an upper-case DNA codon"):
        CodonOptimiseConfig(codon_weights={"atg": 1.0})
    with pytest.raises(ValidationError, match="Negative weight"):
        CodonOptimiseConfig(codon_weights={"ATG": -1.0})


# --- resample_synonymous ------------------------------------------------


def test_resample_synonymous_keeps_the_protein_and_is_deterministic():
    node = ResampleSynonymous(ResampleSynonymousConfig(seed=1))
    (out,) = node.run(sequence=[REF])
    assert _protein(out) == _protein(REF)
    assert node.run(sequence=[REF]) == [out]
    other = Dna(sequence="GCTGCTGCT")
    assert node.run(sequence=[other, REF])[1] == out


def test_resample_synonymous_redraws_more_than_a_few_positions():
    node = ResampleSynonymous(ResampleSynonymousConfig(seed=3, variants_per_sequence=8))
    variants = node.run(sequence=[REF])
    assert len(variants) == 8
    assert all(_protein(v) == _protein(REF) for v in variants)
    # Across variants, codons with synonyms take more than one spelling.
    assert len({v.sequence for v in variants}) > 1
    columns = [
        {v.sequence[i : i + 3] for v in variants}
        for i in range(0, len(REF.sequence), 3)
    ]
    assert any(len(c) > 1 for c in columns)
    assert columns[0] == {"ATG"}  # M has no synonym.


def test_resample_synonymous_keeps_the_stop_codon():
    # TAA, TAG and TGA are synonyms, but swapping one moves where the gene ends.
    node = ResampleSynonymous(
        ResampleSynonymousConfig(seed=0, variants_per_sequence=40)
    )
    assert {v.sequence[-3:] for v in node.run(sequence=[REF])} == {"TAA"}


# --- domesticate --------------------------------------------------------


def test_domesticate_removes_a_site_and_keeps_the_protein():
    seq = Dna(sequence="ATGGAATTCTAA")  # M E F *, EcoRI at GAA TTC.
    (out,) = Domesticate(DomesticateConfig(motifs=("GAATTC",))).run(sequence=[seq])
    assert _protein(out) == _protein(seq)
    assert motif_hits(out.sequence, {"GAATTC"}) == []


def test_domesticate_removes_the_opposite_strand_when_asked():
    seq = Dna(sequence="ATGGAGACCTAA")  # M E T *, GAGACC = revcomp of GGTCTC.
    both = Domesticate(DomesticateConfig(motifs=("GGTCTC",), both_strands=True))
    one = Domesticate(DomesticateConfig(motifs=("GGTCTC",), both_strands=False))
    (clean,) = both.run(sequence=[seq])
    assert motif_hits(clean.sequence, {"GGTCTC", "GAGACC"}) == []
    # Ignoring the reverse strand leaves the site in place.
    assert one.run(sequence=[seq]) == [seq]


def test_domesticate_leaves_what_no_synonym_can_remove():
    seq = Dna(sequence="ATGATGTAA")  # M M *; ATGATG overlaps only ATG codons.
    node = Domesticate(DomesticateConfig(motifs=("ATGATG",)))
    assert node.run(sequence=[seq]) == [seq]  # Stuck, not stuck forever.


def test_domesticate_random_is_deterministic_per_seed():
    seq = Dna(sequence="ATGGAATTCTAAGAATTCTAA")
    config = DomesticateConfig(motifs=("GAATTC",), strategy="random", seed=4)
    node = Domesticate(config)
    (out,) = node.run(sequence=[seq])
    assert node.run(sequence=[seq]) == [out]
    assert motif_hits(out.sequence, {"GAATTC"}) == []
    assert _protein(out) == _protein(seq)


def test_domesticate_rejects_bad_motifs():
    with pytest.raises(ValidationError, match="No motifs"):
        DomesticateConfig(motifs=())
    with pytest.raises(ValidationError, match="Not an upper-case DNA motif"):
        DomesticateConfig(motifs=("gaattc",))
    with pytest.raises(ValidationError, match="at least 2 bases"):
        DomesticateConfig(motifs=("G",))


# --- gc_target_recode ---------------------------------------------------


def _deviation(s: Dna, window: int, target: float) -> float:
    (scores,) = GcContent(GcContentConfig(window=window, target=target)).run(
        sequence=[s]
    )
    return scores["gc_deviation"].value


def test_gc_target_recode_moves_windows_toward_the_target():
    seq = Dna(sequence="ATG" + "TTA" * 6 + "TAA")  # M Lx6 *, AT-heavy.
    before = _deviation(seq, window=9, target=0.6)
    node = GcTargetRecode(GcTargetRecodeConfig(target=0.6, window=9))
    (out,) = node.run(sequence=[seq])
    assert _protein(out) == _protein(seq)
    assert _deviation(out, window=9, target=0.6) < before


def test_gc_target_recode_is_deterministic_and_bounded():
    seq = Dna(sequence="ATG" + "GCG" * 6 + "TAA")  # GC-heavy alanine run.
    node = GcTargetRecode(GcTargetRecodeConfig(target=0.4, window=9, max_passes=3))
    assert node.run(sequence=[seq]) == node.run(sequence=[seq])


def test_gc_target_recode_handles_a_sequence_shorter_than_the_window():
    seq = Dna(sequence="ATGTTT")  # One window: the whole sequence.
    (out,) = GcTargetRecode(GcTargetRecodeConfig(target=1.0, window=30)).run(
        sequence=[seq]
    )
    assert _protein(out) == _protein(seq)


# --- codon_adaptation ---------------------------------------------------


def _cai(s: Dna, weights: dict[str, float] = WEIGHTS) -> float:
    (scores,) = CodonAdaptation(CodonAdaptationConfig(codon_weights=weights)).run(
        sequence=[s]
    )
    return scores["cai"].value


def test_codon_adaptation_scores_the_table_favourite_at_one():
    assert _cai(Dna(sequence="ATGGCT")) == 1.0
    assert _cai(Dna(sequence="ATGGCG")) == pytest.approx((1 * 0.25 / 2) ** 0.5)


def test_codon_adaptation_ignores_what_the_table_omits_and_stops():
    # Only ATG has a weight, so the alanine codon drops out of the mean and the
    # stop is skipped by definition: the sequence scores on its ATG alone.
    assert _cai(Dna(sequence="ATGGCTTAA"), {"ATG": 1.0}) == 1.0


def test_codon_adaptation_zero_weight_empties_the_mean():
    # AAA's synonym AAG has a weight but AAA does not, so its term is 0 and so
    # is the whole geometric mean.
    assert _cai(Dna(sequence="ATGAAA"), {"ATG": 1.0, "AAG": 1.0}) == 0.0
    # A codon under the top still scores, geometrically: GCA is 0.5 of GCT's 2.
    assert _cai(Dna(sequence="ATGGCA")) == pytest.approx(0.25**0.5)


def test_codon_adaptation_rejects_a_bad_table():
    with pytest.raises(ValidationError, match="Not an upper-case DNA codon"):
        CodonAdaptationConfig(codon_weights={"AT": 1.0})


# --- gc_content ---------------------------------------------------------


def test_gc_content_reports_overall_and_windowed_gc():
    (scores,) = GcContent(GcContentConfig(window=3, target=0.5)).run(
        sequence=[Dna(sequence="ATGGCC")]
    )
    assert scores["gc"].value == pytest.approx(4 / 6)
    # Windows: ATG 1/3, TGG 2/3, GGC 1, GCC 1.
    assert scores["gc_min"].value == pytest.approx(1 / 3)
    assert scores["gc_max"].value == 1.0
    assert scores["gc_deviation"].value == pytest.approx(0.5)


def test_gc_content_scores_rna_and_empty_and_short_sequences():
    node = GcContent(GcContentConfig(window=10))
    (rna_scores,) = node.run(sequence=[Rna(sequence="GGU")])
    assert rna_scores["gc"].value == pytest.approx(2 / 3)
    (empty_scores,) = node.run(sequence=[Dna(sequence="")])
    assert empty_scores["gc"].value == 0.0
    assert empty_scores["gc_deviation"].value == 0.5  # |0 - target|.


# --- motif_count --------------------------------------------------------


def test_motif_count_counts_overlaps_and_runs():
    (scores,) = MotifCount(MotifCountConfig(motifs=("AAA",), both_strands=False)).run(
        sequence=[Dna(sequence="AAAAAA")]
    )
    assert scores["motifs"].value == 4.0
    assert scores["max_homopolymer"].value == 6.0


def test_motif_count_sees_both_strands_without_double_counting():
    seq = Dna(sequence="AAAGAGACCAAA")  # Reverse-strand BsaI site.
    both = MotifCount(MotifCountConfig(motifs=("GGTCTC",), both_strands=True))
    one = MotifCount(MotifCountConfig(motifs=("GGTCTC",), both_strands=False))
    assert both.run(sequence=[seq])[0]["motifs"].value == 1.0
    assert one.run(sequence=[seq])[0]["motifs"].value == 0.0
    # A palindrome is one site, not a site counted twice.
    (pal,) = MotifCount(MotifCountConfig(motifs=("GAATTC",))).run(
        sequence=[Dna(sequence="GAATTC")]
    )
    assert pal["motifs"].value == 1.0


def test_motif_count_reads_rna_as_its_dna():
    (scores,) = MotifCount(MotifCountConfig(motifs=("GAATTC",))).run(
        sequence=[Rna(sequence="GAAUUC")]
    )
    assert scores["motifs"].value == 1.0


# --- mrna_5prime_mfe -----------------------------------------------------


def _mfe(s, **kwargs) -> float:
    (scores,) = Mrna5primeMfe(Mrna5primeMfeConfig(**kwargs)).run(sequence=[s])
    return scores["mfe"].value


def test_mrna_5prime_mfe_scores_a_hairpin_below_a_structureless_sequence():
    hairpin = Dna(sequence="GGGGAAAACCCC")
    open_seq = Dna(sequence="AAAAAAAAAAAA")
    assert _mfe(hairpin, cds_bases=12) < _mfe(open_seq, cds_bases=12) <= 0.0


def test_mrna_5prime_mfe_is_deterministic_and_reads_rna_as_dna():
    seq = Dna(sequence="GGGGAAAACCCCGGGGAAAACCCC")
    assert _mfe(seq) == _mfe(seq)
    assert _mfe(seq, cds_bases=12) == _mfe(Rna(sequence="GGGGAAAACCCC"), cds_bases=12)


def test_mrna_5prime_mfe_utr_changes_the_fold():
    seq = Dna(sequence="GGGGAAAACCCCGGGG")
    utr = "TTTTCCCCCCCC"  # Can pair with the sequence's own GC run.
    assert _mfe(seq, cds_bases=12) != _mfe(seq, utr=utr, cds_bases=12)


# --- codon_pair_score ----------------------------------------------------


def test_codon_pair_score_means_the_pair_weights():
    seq = Dna(sequence="ATGGCTTAA")  # Pairs: ATGGCT, GCTTAA.
    node = CodonPairScore(
        CodonPairScoreConfig(pair_weights={"ATGGCT": 2.0, "GCTTAA": 0.0})
    )
    (scores,) = node.run(sequence=[seq])
    assert scores["codon_pair"].value == 1.0


def test_codon_pair_score_uses_missing_for_pairs_outside_the_table():
    seq = Dna(sequence="ATGGCTTAA")
    node = CodonPairScore(
        CodonPairScoreConfig(pair_weights={"ATGGCT": 2.0}, missing=-1.0)
    )
    (scores,) = node.run(sequence=[seq])
    assert scores["codon_pair"].value == pytest.approx(0.5)
    # One codon is no pair to score.
    assert node.run(sequence=[Dna(sequence="ATG")])[0]["codon_pair"].value == 0.0


def test_codon_pair_score_rejects_a_bad_key():
    with pytest.raises(ValidationError, match="Not two upper-case DNA codons"):
        CodonPairScoreConfig(pair_weights={"ATGG": 1.0})


# --- protein_to_dna ------------------------------------------------------


def _back(protein: str, **kwargs) -> Dna:
    node = ProteinToDna(ProteinToDnaConfig(codon_weights=WEIGHTS, **kwargs))
    (dna,) = node.run(sequence=[AminoAcidSequence(sequence=protein)])
    return dna


def test_protein_to_dna_most_frequent_picks_the_best_codon():
    assert _back("MA").sequence == "ATGGCT"


def test_protein_to_dna_least_frequent_picks_the_worst_codon():
    assert _back("MA", strategy="least_frequent").sequence == "ATGGCG"


def test_protein_to_dna_round_trips_through_dna_to_protein():
    for strategy in ("most_frequent", "least_frequent", "weighted_sample"):
        dna = _back("MALK*", strategy=strategy)
        assert _protein(dna) == "MALK*"
        assert len(dna.sequence) == 3 * len("MALK*")


def test_protein_to_dna_falls_back_to_the_first_codon_without_weights():
    # Leucine and lysine are absent from the table, so the table says nothing.
    assert _back("LK").sequence == "TTAAAA"
    # A stop is a codon like any other; none is appended on its own.
    assert _back("M*").sequence == "ATGTAA"
    assert _back("M").sequence == "ATG"


def test_protein_to_dna_weighted_sample_is_seeded_per_sequence():
    protein = "A" * 30
    assert _back(protein, strategy="weighted_sample", seed=1) == _back(
        protein, strategy="weighted_sample", seed=1
    )
    assert _back(protein, strategy="weighted_sample", seed=1) != _back(
        protein, strategy="weighted_sample", seed=2
    )
    # The draw uses the whole table, not just the favourite.
    sampled = _back(protein, strategy="weighted_sample", seed=1).sequence
    assert len(set(codons(sampled))) > 1
    assert _protein(Dna(sequence=sampled)) == protein


def test_protein_to_dna_rejects_a_bad_table():
    with pytest.raises(ValidationError, match="Not an upper-case DNA codon"):
        ProteinToDnaConfig(codon_weights={"AT": 1.0})
    with pytest.raises(ValidationError, match="Negative weight"):
        ProteinToDnaConfig(codon_weights={"ATG": -1.0})


# --- dinucleotide_bias ---------------------------------------------------


def _dinucleotide(s, dinucleotides=("CG",)) -> dict[str, float]:
    node = DinucleotideBias(DinucleotideBiasConfig(dinucleotides=dinucleotides))
    (scores,) = node.run(sequence=[s])
    return {k: v.value for k, v in scores.items()}


def test_dinucleotide_bias_measures_observed_over_expected():
    # Four CG in seven adjacent pairs, where base composition predicts 1.75.
    scores = _dinucleotide(Dna(sequence="CGCGCGCG"))
    assert scores["odds_ratio"] == pytest.approx(4 / 1.75)
    assert scores["frequency"] == pytest.approx(4 / 7)


def test_dinucleotide_bias_separates_depletion_from_base_composition():
    # The same four C and four G, so only their arrangement differs.
    enriched = _dinucleotide(Dna(sequence="CGCGCGCG"))
    depleted = _dinucleotide(Dna(sequence="CCCCGGGG"))
    assert depleted["odds_ratio"] == pytest.approx(1 / 1.75)
    assert depleted["odds_ratio"] < 1.0 < enriched["odds_ratio"]


def test_dinucleotide_bias_scores_zero_without_the_bases_or_the_pairs():
    assert _dinucleotide(Dna(sequence="AAAAAA")) == {
        "odds_ratio": 0.0,  # No C and no G, so nothing is expected either.
        "frequency": 0.0,
    }
    assert _dinucleotide(Dna(sequence="A"))["odds_ratio"] == 0.0
    assert _dinucleotide(Dna(sequence=""))["frequency"] == 0.0


def test_dinucleotide_bias_pools_several_pairs_and_reads_rna_as_dna():
    both = _dinucleotide(Dna(sequence="CGTACGTA"), dinucleotides=("CG", "TA"))
    assert both["frequency"] == pytest.approx(4 / 7)  # Two CG and two TA.
    assert _dinucleotide(Rna(sequence="CGCGCGCG")) == _dinucleotide(
        Dna(sequence="CGCGCGCG")
    )


def test_dinucleotide_bias_rejects_a_bad_list():
    with pytest.raises(ValidationError, match="Not two upper-case DNA bases"):
        DinucleotideBiasConfig(dinucleotides=("CGA",))
    with pytest.raises(ValidationError, match="No dinucleotides given"):
        DinucleotideBiasConfig(dinucleotides=())
    with pytest.raises(ValidationError, match="Repeated dinucleotide"):
        DinucleotideBiasConfig(dinucleotides=("CG", "CG"))


# --- repeat_score --------------------------------------------------------


def _repeats(s, **kwargs) -> dict[str, float]:
    (scores,) = RepeatScore(RepeatScoreConfig(**kwargs)).run(sequence=[s])
    return {k: v.value for k, v in scores.items()}


def test_repeat_score_reports_the_longest_repeat_and_its_extent():
    scores = _repeats(Dna(sequence="ATGCATGCTT"), min_length=4)
    assert scores["max_repeat"] == 4.0
    assert scores["repeat_fraction"] == pytest.approx(0.8)


def test_repeat_score_separates_a_repetitive_sequence_from_a_clean_one():
    repetitive = _repeats(Dna(sequence="ATGCATGCATGCATGC"))
    clean = _repeats(Dna(sequence="ATGCAAGGTTCCAGTC"))
    assert repetitive["max_repeat"] > clean["max_repeat"]
    assert repetitive["repeat_fraction"] > clean["repeat_fraction"]


def test_repeat_score_finds_a_hairpin_as_an_inverted_repeat():
    assert _repeats(Dna(sequence="GGGGCCCC"))["max_inverted_repeat"] == 8.0
    assert _repeats(Dna(sequence="AAAAAAAA"))["max_inverted_repeat"] == 0.0


def test_repeat_score_reads_rna_as_its_dna():
    assert _repeats(Rna(sequence="GGGGCCCC")) == _repeats(Dna(sequence="GGGGCCCC"))
    assert _repeats(Dna(sequence=""))["max_repeat"] == 0.0


def test_repeat_score_min_length_only_moves_the_fraction():
    long_run = Dna(sequence="ATGCATGCTT")
    assert _repeats(long_run, min_length=4)["repeat_fraction"] > 0.0
    assert _repeats(long_run, min_length=5)["repeat_fraction"] == 0.0
    assert (
        _repeats(long_run, min_length=4)["max_repeat"]
        == _repeats(long_run, min_length=5)["max_repeat"]
    )


# --- mrna_fold_energy ----------------------------------------------------


def _fold(s) -> dict[str, float]:
    (scores,) = MrnaFoldEnergy(MrnaFoldEnergyConfig()).run(sequence=[s])
    return {k: v.value for k, v in scores.items()}


def test_mrna_fold_energy_scores_a_hairpin_below_a_structureless_sequence():
    hairpin = _fold(Dna(sequence="GGGGGAAAACCCCC"))
    open_seq = _fold(Dna(sequence="AAAAAAAAAAAAAA"))
    assert hairpin["mfe"] < open_seq["mfe"] <= 0.0
    assert hairpin["mfe_per_base"] < open_seq["mfe_per_base"]


def test_mrna_fold_energy_ensemble_is_never_above_the_mfe():
    scores = _fold(Dna(sequence="GGGGGAAAACCCCCGGGGGAAAACCCCC"))
    assert scores["ensemble_energy"] <= scores["mfe"]


def test_mrna_fold_energy_normalises_by_length():
    seq = Dna(sequence="GGGGGAAAACCCCC")
    scores = _fold(seq)
    assert scores["mfe_per_base"] == pytest.approx(scores["mfe"] / len(seq.sequence))


def test_mrna_fold_energy_is_deterministic_and_reads_dna_as_mrna():
    seq = Dna(sequence="GGGGGAAAACCCCC")
    assert _fold(seq) == _fold(seq)
    assert _fold(seq) == _fold(Rna(sequence="GGGGGAAAACCCCC"))


def test_mrna_fold_energy_scores_an_empty_sequence_at_zero():
    assert _fold(Dna(sequence="")) == {
        "mfe": 0.0,
        "ensemble_energy": 0.0,
        "mfe_per_base": 0.0,
    }


# --- the nodes compose in a DAG -------------------------------------------


def test_a_dag_can_score_resampled_variants_and_filter_them():
    resample = ResampleSynonymousConfig(seed=1, variants_per_sequence=4)
    score = GcContentConfig(window=6)
    dag = {
        "inputs": {"seqs": "dna"},
        "steps": {
            "variants": {
                "config": resample.model_dump(mode="json"),
                "inputs": {"sequence": "seqs"},
            },
            "gc": {
                "config": score.model_dump(mode="json"),
                "inputs": {"sequence": "variants"},
            },
            "moderate": {
                "config": {
                    "name": "at_most",
                    "column": score.columns()["gc_deviation"],
                    "threshold": 0.2,
                },
                "inputs": {"items": "gc"},
            },
        },
    }
    assert Dag.model_validate(dag)


def test_a_dag_can_take_a_protein_back_to_scored_dna():
    """protein_to_dna closes the loop: an amino acid port feeds the DNA scores."""
    back = ProteinToDnaConfig(codon_weights=WEIGHTS)
    score = RepeatScoreConfig(min_length=6)
    dag = {
        "inputs": {"seqs": "dna"},
        "steps": {
            "protein": {
                "config": {"name": "dna_to_protein"},
                "inputs": {"sequence": "seqs"},
            },
            "coding": {
                "config": back.model_dump(mode="json"),
                "inputs": {"sequence": "protein"},
            },
            "repeats": {
                "config": score.model_dump(mode="json"),
                "inputs": {"sequence": "coding"},
            },
            "unrepetitive": {
                "config": {
                    "name": "at_most",
                    "column": score.columns()["max_repeat"],
                    "threshold": 12,
                },
                "inputs": {"items": "repeats"},
            },
        },
    }
    assert Dag.model_validate(dag)
