import pytest
from pydantic import ValidationError

from node_dag.nodes.decisions.codons_absent.config import CodonsAbsentConfig
from node_dag.nodes.decisions.codons_absent.function import CodonsAbsent
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.types import Dna


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
