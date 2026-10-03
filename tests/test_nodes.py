import pytest
from pydantic import ValidationError

from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.decisions.at_least.function import AtLeast
from node_dag.nodes.decisions.at_most.config import AtMostConfig
from node_dag.nodes.decisions.at_most.function import AtMost
from node_dag.nodes.decisions.codons_absent.config import CodonsAbsentConfig
from node_dag.nodes.decisions.codons_absent.function import CodonsAbsent
from node_dag.nodes.tools.dna_complement.config import DnaComplementConfig
from node_dag.nodes.tools.dna_complement.function import DnaComplement
from node_dag.nodes.tools.dna_reverse_complement.config import (
    DnaReverseComplementConfig,
)
from node_dag.nodes.tools.dna_reverse_complement.function import DnaReverseComplement
from node_dag.nodes.tools.dna_transcribe.config import DnaTranscribeConfig
from node_dag.nodes.tools.dna_transcribe.function import DnaTranscribe
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.nodes.tools.rna_back_transcribe.function import RnaBackTranscribe
from node_dag.types import Dna, Rna, Score


@pytest.mark.parametrize(
    ("sequence", "targets"),
    [
        ("ATGTCGTAA", ("TCG",)),
        ("ATGTCGTCATAG", ("TCG", "TCA")),
        ("ATGCTGCTGCTGTAA", ("CTG",)),
        ("ATGAAATTTGGGTAA", ("AAA", "TTT", "GGG")),
        # Nothing to do: no target is in frame.
        ("ATGAAATAA", ("TCG",)),
    ],
)
def test_recode_removes_every_target_and_keeps_the_protein(sequence, targets):
    """A recoded sequence carries no target codon and codes for the same protein."""
    original = Dna(sequence=sequence)
    config = RecodeCodonsConfig(targets=targets)
    recoded = RecodeCodons(config).run(original)
    assert recoded.protein() == original.protein()
    assert not {c.codon for c in recoded.codons()} & set(targets)


def test_recoding_is_deterministic():
    """The same config twice gives the identical sequence, so a run replays."""
    sequence = Dna(sequence="ATGTCGTCGCTGTAA")
    first = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "CTG"))).run(sequence)
    second = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "CTG"))).run(sequence)
    assert first.sequence == second.sequence


def test_recode_raises_for_a_codon_with_no_synonym():
    """ATG is methionine's only codon, so there is nowhere to recode it to."""
    config = RecodeCodonsConfig(targets=("ATG",))
    with pytest.raises(ValueError, match="Every synonymous codon of M"):
        RecodeCodons(config).run(Dna(sequence="ATGTCGTAA"))


def test_recode_raises_when_every_synonym_is_targeted():
    """Histidine has CAT and CAC; target both and the sequence cannot be recoded."""
    config = RecodeCodonsConfig(targets=("CAT", "CAC"))
    with pytest.raises(ValueError, match="Every synonymous codon of H"):
        RecodeCodons(config).run(Dna(sequence="ATGCATTAA"))


def test_recode_config_normalises_targets():
    """Targets are upper-cased and deduped, order preserved."""
    assert RecodeCodonsConfig(targets=("tcg", "TCA", "TCG")).targets == ("TCG", "TCA")


@pytest.mark.parametrize("config_cls", [RecodeCodonsConfig, CodonsAbsentConfig])
def test_a_codon_field_rejects_a_non_codon(config_cls):
    """Only real codons may be named, on either node."""
    field = "targets" if config_cls is RecodeCodonsConfig else "codons"
    with pytest.raises(ValidationError, match="Not a codon"):
        config_cls(**{field: ("ATGG",)})
    with pytest.raises(ValidationError):
        config_cls(**{field: ()})


def test_codons_absent_says_yes_on_a_clean_sequence():
    """No TCG codon, so the yes branch."""
    config = CodonsAbsentConfig(codons=("TCG",))
    assert CodonsAbsent(config).run(Dna(sequence="ATGTCTTAA")) is True


def test_codons_absent_says_no_on_a_dirty_sequence():
    """A TCG codon is present, so the no branch."""
    config = CodonsAbsentConfig(codons=("TCG", "TCA"))
    assert CodonsAbsent(config).run(Dna(sequence="ATGTCGTAA")) is False


def test_codons_absent_only_counts_a_codon_in_frame():
    """AAATGGCCC has ATG as a substring at offset 2, but its codons are AAA, TGG, CCC."""
    sequence = Dna(sequence="AAATGGCCC")
    assert "ATG" in sequence.sequence
    config = CodonsAbsentConfig(codons=("ATG",))
    assert CodonsAbsent(config).run(sequence) is True


def test_recode_then_codons_absent_agree():
    """The pair composes: what recode removes, the check confirms is gone."""
    sequence = Dna(sequence="ATGCTGTCGCTGTAA")
    recoded = RecodeCodons(RecodeCodonsConfig(targets=("CTG", "TCG"))).run(sequence)
    check = CodonsAbsent(CodonsAbsentConfig(codons=("CTG", "TCG")))
    assert check.run(sequence) is False
    assert check.run(recoded) is True


@pytest.mark.parametrize(
    ("sequence", "complement"),
    [
        ("ATGTCGTAA", "TACAGCATT"),
        ("AAACCCGGGTTT", "TTTGGGCCCAAA"),
        # A palindrome base for base: its complement is itself reversed.
        ("ATGCAT", "TACGTA"),
    ],
)
def test_complement_pairs_each_base_in_order(sequence, complement):
    """A pairs with T and C with G, position for position."""
    result = DnaComplement(DnaComplementConfig()).run(Dna(sequence=sequence))
    assert result.sequence == complement


@pytest.mark.parametrize(
    ("sequence", "reverse_complement"),
    [
        ("ATGTCGTAA", "TTACGACAT"),
        ("AAACCCGGGTTT", "AAACCCGGGTTT"),
        ("ATGCAT", "ATGCAT"),
    ],
)
def test_reverse_complement_gives_the_opposite_strand(sequence, reverse_complement):
    """The opposite strand runs antiparallel, so it is read 5' to 3' the other way."""
    config = DnaReverseComplementConfig()
    result = DnaReverseComplement(config).run(Dna(sequence=sequence))
    assert result.sequence == reverse_complement


@pytest.mark.parametrize("sequence", ["ATGTCGTAA", "AAACCCGGGTTT", "ATGCATCAT"])
def test_reverse_complement_is_the_complement_reversed(sequence):
    """The two conversion nodes agree about which base pairs with which."""
    original = Dna(sequence=sequence)
    complement = DnaComplement(DnaComplementConfig()).run(original)
    reversed_ = DnaReverseComplement(DnaReverseComplementConfig()).run(original)
    assert reversed_.sequence == complement.sequence[::-1]


@pytest.mark.parametrize("sequence", ["ATGTCGTAA", "AAACCCGGGTTT"])
def test_complement_twice_is_the_original(sequence):
    """Complementing is its own inverse, so a pair of nodes composes back."""
    node = DnaComplement(DnaComplementConfig())
    assert node.run(node.run(Dna(sequence=sequence))).sequence == sequence


def test_transcribe_swaps_t_for_u():
    """The coding strand transcribes by a change of alphabet, not of order."""
    rna = DnaTranscribe(DnaTranscribeConfig()).run(Dna(sequence="ATGTCGTAA"))
    assert isinstance(rna, Rna)
    assert rna.sequence == "AUGUCGUAA"


def test_back_transcribe_swaps_u_for_t():
    """The inverse: U becomes T and the bases keep their order."""
    dna = RnaBackTranscribe(RnaBackTranscribeConfig()).run(Rna(sequence="AUGUCGUAA"))
    assert isinstance(dna, Dna)
    assert dna.sequence == "ATGTCGTAA"


@pytest.mark.parametrize("sequence", ["ATGTCGTAA", "AAACCCGGGTTT", "ATGCATTTTTAG"])
def test_transcribe_then_back_transcribe_round_trips(sequence):
    """Chaining the two conversions returns the DNA it started from."""
    original = Dna(sequence=sequence)
    rna = DnaTranscribe(DnaTranscribeConfig()).run(original)
    back = RnaBackTranscribe(RnaBackTranscribeConfig()).run(rna)
    assert back == original
    # The RNA codes for the same protein, read through the same genetic code.
    assert rna.protein() == original.protein()


def test_rna_rejects_thymine_and_accepts_uracil():
    """RNA has U where DNA has T, and nothing in between."""
    assert Rna(sequence="AUG").sequence == "AUG"
    with pytest.raises(ValidationError, match="Not whole codons of A, C, G, U"):
        Rna(sequence="ATG")


def test_rna_rejects_a_partial_codon():
    """Like Dna, an RNA value is a whole number of codons."""
    with pytest.raises(ValidationError, match="Not whole codons of A, C, G, U"):
        Rna(sequence="AUGU")


@pytest.mark.parametrize(
    ("score", "threshold", "yes"),
    [
        (297.0, 300.0, True),
        (297.0, 200.0, False),
        # The boundary: at most means the threshold itself passes.
        (297.0, 297.0, True),
        (0.0, 0.0, True),
    ],
)
def test_at_most_takes_yes_up_to_and_including_the_threshold(score, threshold, yes):
    """A score at or below the threshold is forwarded on yes, a higher one on no."""
    config = AtMostConfig(threshold=threshold)
    assert AtMost(config).run(Score(value=score)) is yes


def test_at_most_and_at_least_split_on_the_same_boundary():
    """Both say yes at the threshold, so neither branch drops the boundary value."""
    at_most = AtMost(AtMostConfig(threshold=100.0))
    at_least = AtLeast(AtLeastConfig(threshold=100.0))
    boundary = Score(value=100.0)
    assert at_most.run(boundary) is True
    assert at_least.run(boundary) is True
    assert at_most.run(Score(value=101.0)) is False
    assert at_least.run(Score(value=99.0)) is False
