import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from node_dag.dag import Dag
from node_dag.dna import gc_window_fractions, motif_hits, reverse_complement
from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
from node_dag.nodes.tools.codon_adaptation.function import CodonAdaptation
from node_dag.nodes.tools.codon_optimise.config import CodonOptimiseConfig
from node_dag.nodes.tools.codon_optimise.function import CodonOptimise
from node_dag.nodes.tools.codon_pair_score.config import CodonPairScoreConfig
from node_dag.nodes.tools.codon_pair_score.function import CodonPairScore
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
from node_dag.nodes.tools.resample_synonymous.config import ResampleSynonymousConfig
from node_dag.nodes.tools.resample_synonymous.function import ResampleSynonymous
from node_dag.types import Dna, Rna

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


@pytest.mark.parametrize("strategy", ["most_frequent", "least_frequent"])
def test_codon_optimise_keeps_the_stop_codon(strategy):
    table = {**WEIGHTS, "TAA": 1.0, "TGA": 0.4, "TAG": 0.1}
    node = CodonOptimise(CodonOptimiseConfig(codon_weights=table, strategy=strategy))
    for stop in ("TAA", "TGA", "TAG"):
        (out,) = node.run(sequence=[Dna(sequence="ATGGCC" + stop)])
        assert out.sequence.endswith(stop)
    # The sense codon is still respelled.
    assert node.run(sequence=[Dna(sequence="ATGGCCTGA")])[0] != Dna(
        sequence="ATGGCCTGA"
    )


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


@pytest.mark.parametrize("stop", ["TAA", "TAG", "TGA"])
def test_domesticate_leaves_the_stop_codon_when_only_it_could_clear_a_site(stop):
    # M has no synonym, so the stop is the one codon that could clear G + stop. The
    # three stops translate alike, but swapping one moves where the gene ends.
    seq = Dna(sequence="ATG" + stop)
    node = Domesticate(DomesticateConfig(motifs=("G" + stop,)))
    assert node.run(sequence=[seq]) == [seq]


@pytest.mark.parametrize("seed", range(20))
def test_domesticate_clears_a_site_by_the_sense_codon_not_the_stop(seed):
    seq = Dna(sequence="ATGAAATAA")  # AATAA spans the last K and the stop.
    config = DomesticateConfig(motifs=("AATAA",), strategy="random", seed=seed)
    (out,) = Domesticate(config).run(sequence=[seq])
    assert motif_hits(out.sequence, {"AATAA"}) == []
    assert out.sequence.endswith("TAA") and _protein(out) == _protein(seq)


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


@pytest.mark.parametrize(("stop", "target"), [("TAA", 1.0), ("TAG", 0.0), ("TGA", 0.0)])
def test_gc_target_recode_leaves_the_stop_codon(stop, target):
    # Each stop has a swap that moves GC toward the target, so a node that took it
    # would end the gene differently while the protein stayed the same.
    seq = Dna(sequence="ATGGCC" + stop)
    (out,) = GcTargetRecode(GcTargetRecodeConfig(target=target, window=30)).run(
        sequence=[seq]
    )
    assert out.sequence.endswith(stop) and _protein(out) == _protein(seq)


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
