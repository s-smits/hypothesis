from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.nodes.tools.codon_count.function import CodonCount
from node_dag.types import Dna


def test_codon_count_is_in_frame():
    run = CodonCount(CodonCountConfig(codons=("ATG",))).run
    assert [
        r["count"].value
        for r in run(sequence=[Dna(sequence="AAATGGCCC"), Dna(sequence="ATGATGTAA")])
    ] == [0, 2]
