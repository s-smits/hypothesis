from node_dag.nodes.tools.codon_count.config import CodonCountConfig
from node_dag.nodes.tools.codon_count.function import CodonCount
from node_dag.nodes.tools.trim_to_first_start.config import TrimToFirstStartConfig
from node_dag.nodes.tools.trim_to_first_start.function import TrimToFirstStart
from node_dag.types import Dna


def test_codon_count_is_in_frame():
    run = CodonCount(CodonCountConfig(codons=("ATG",))).run
    assert [
        r["count"].value
        for r in run(sequence=[Dna(sequence="AAATGGCCC"), Dna(sequence="ATGATGTAA")])
    ] == [0, 2]


# --- trim_to_first_start -----------------------------------------------------


def trim(seqs: list[str]) -> list[Dna]:
    return TrimToFirstStart(TrimToFirstStartConfig()).run(
        sequence=[Dna(sequence=s) for s in seqs]
    )


def test_the_requested_worked_example():
    """The example the blocked plan gave, leader and trailing partial codon cut."""
    (out,) = trim(["ACTTCTAATTTATTCTATTTATTCGCGGATATGCATAGGAGTGCTTCGATGTCAT"])
    assert out.sequence == "ATGCATAGGAGTGCTTCGATGTCA"


def test_the_first_atg_wins_whatever_frame_it_is_in():
    # This ATG sits one base in, off the original frame; the leader still goes.
    (out,) = trim(["AATGAA"])
    assert out.sequence == "ATG"
    (out,) = trim(["TATGATG"])
    assert out.sequence == "ATGATG"  # the second ATG is kept as a codon


def test_a_sequence_already_at_atg_only_loses_its_trailing_partial_codon():
    (out,) = trim(["ATGAAAAT"])
    assert out.sequence == "ATGAAA"
    (out,) = trim(["ATGAAA"])
    assert out.sequence == "ATGAAA"


def test_an_atg_at_the_very_end_still_gives_a_codon():
    (out,) = trim(["CCCCATG"])
    assert out.sequence == "ATG"


def test_a_sequence_without_atg_is_dropped_not_passed_through():
    out = trim(["AAATTTCCC", "GGATGCC", "TTTTTT"])
    assert [o.sequence for o in out] == ["ATG"]


def test_every_output_starts_with_atg_in_frame():
    out = trim(["CATGTT", "AATGGGCC", "ATG"])
    assert all(o.sequence.startswith("ATG") and len(o.sequence) % 3 == 0 for o in out)


def test_empty_input_gives_empty_output():
    assert trim([]) == []
