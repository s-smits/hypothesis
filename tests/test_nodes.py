import pytest

from node_dag.dna import CODON_TABLE, codons
from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.nodes.tools.codon_count.function import CodonCount
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.types import Dna


def _recode(*targets: str, seqs: list[str]) -> list[Dna]:
    return RecodeCodons(RecodeCodonsConfig(targets=targets)).run(
        sequence=[Dna(sequence=s) for s in seqs]
    )


def test_recode_removes_targets_and_keeps_the_protein():
    seq = "ATGTCGTCATAA"  # M S S *
    (out,) = _recode("tcg", "TCA", seqs=[seq])  # Case is normalised.
    assert not {"TCG", "TCA"} & set(codons(out.sequence))
    assert [CODON_TABLE[c] for c in codons(out.sequence)] == [
        CODON_TABLE[c] for c in codons(seq)
    ]
    assert _recode("TCG", "TCA", seqs=[seq]) == [out]


def test_recode_leaves_a_partial_codon_at_the_end_alone():
    (out,) = _recode("TCG", seqs=["ATGTCGTC"])
    assert out.sequence.endswith("TC") and "TCG" not in codons(out.sequence)


def test_recode_raises_when_no_synonym_is_left():
    with pytest.raises(ValueError, match="Every synonym"):
        _recode("ATG", seqs=["ATGTAA"])


def test_codon_count_is_in_frame():
    run = CodonCount(CodonCountConfig(codons=("ATG",))).run
    assert [
        r["count"].value
        for r in run(sequence=[Dna(sequence="AAATGGCCC"), Dna(sequence="ATGATGTAA")])
    ] == [0, 2]
