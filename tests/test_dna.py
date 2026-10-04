import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.filters.at_most.function import AtMost
from node_dag.nodes.tools.dna_complement.config import DnaComplementConfig
from node_dag.nodes.tools.dna_complement.function import DnaComplement
from node_dag.nodes.tools.dna_reverse_complement.config import (
    DnaReverseComplementConfig,
)
from node_dag.nodes.tools.dna_reverse_complement.function import DnaReverseComplement
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.dna_to_protein.function import DnaToProtein
from node_dag.nodes.tools.dna_transcribe.config import DnaTranscribeConfig
from node_dag.nodes.tools.dna_transcribe.function import DnaTranscribe
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.nodes.tools.rna_back_transcribe.function import RnaBackTranscribe
from node_dag.types import (
    TYPES,
    AminoAcidSequence,
    Dna,
    ProteinStructure,
    Rna,
    Table,
)

REF = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *


def test_dna_to_protein_translates_using_biopython():
    dna_seq = Dna(sequence="ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG")
    (protein,) = DnaToProtein(DnaToProteinConfig()).run(sequence=[dna_seq])
    assert protein.sequence == "MAIVMGR*KGAR*"

    (ref_protein,) = DnaToProtein(DnaToProteinConfig()).run(sequence=[REF])
    assert ref_protein.sequence == "MALK*"


@pytest.mark.parametrize("bad", ["ATGAAU", "atg", "ACGTX", "123"])
def test_dna_rejects_other_bases(bad):
    with pytest.raises(ValidationError):
        Dna(sequence=bad)


@pytest.mark.parametrize("seq", ["A", "AC", "ATGA", "ATGCA"])
def test_dna_accepts_lengths_not_divisible_by_three(seq):
    dna = Dna(sequence=seq)
    assert dna.sequence == seq



def test_id_is_the_same_for_the_same_sequence_and_differs_by_kind():
    assert Dna(sequence="ATG").id == Dna(sequence="ATG").id
    assert Dna(sequence="ATG").id != Dna(sequence="ATA").id
    assert Dna(sequence="GCT").id != AminoAcidSequence(sequence="GCT").id
    assert Dna.model_validate_json(REF.model_dump_json()) == REF
    assert REF.model_dump()["id"] == REF.id


def test_each_type_has_a_display_string():
    assert str(REF) == REF.display == "ATGGCTCTGAAATAA"
    protein = AminoAcidSequence(sequence="MALK*")
    assert str(protein) == protein.display == "MALK*"
    assert REF.model_dump()["display"] == "ATGGCTCTGAAATAA"
    assert Dna.model_validate_json(REF.model_dump_json()) == REF


def test_protein_structure_fields_and_id():
    ps1 = ProteinStructure(sequence="MALK*", structure="ATOM 1 ...")
    ps2 = ProteinStructure(sequence="MALK*", structure="ATOM 1 ...")
    ps3 = ProteinStructure(sequence="MALK*", structure="ATOM 2 ...")
    ps4 = ProteinStructure(sequence="MALK", structure="ATOM 1 ...")

    assert ps1.kind == "protein_structure"
    assert ps1.sequence == "MALK*"
    assert ps1.structure == "ATOM 1 ..."
    assert ps1.id == ps2.id
    assert ps1.id != ps3.id
    assert ps1.id != ps4.id
    assert ps1.id != AminoAcidSequence(sequence="MALK*").id

    # Display matches sequence
    assert str(ps1) == ps1.display == "MALK*"

    # Serialization roundtrip
    dumped = ps1.model_dump()
    assert dumped["id"] == ps1.id
    assert dumped["structure"] == "ATOM 1 ..."
    assert dumped["kind"] == "protein_structure"
    assert ProteinStructure.model_validate_json(ps1.model_dump_json()) == ps1


def test_protein_structure_validation():
    with pytest.raises(ValidationError):
        ProteinStructure(sequence="malk", structure="ATOM 1 ...")
    with pytest.raises(ValidationError):
        ProteinStructure(sequence="123", structure="ATOM 1 ...")


def test_protein_structure_in_table_and_types():
    assert TYPES["protein_structure"] is ProteinStructure
    ps = ProteinStructure(sequence="MALK*", structure="ATOM 1 ...")
    t = Table.of([ps])
    assert t.items == [ps]
    roundtripped = Table.model_validate_json(t.model_dump_json())
    assert roundtripped.items == [ps]
    assert isinstance(roundtripped.items[0], ProteinStructure)


def test_table_merges_repeats_and_keeps_scores_of_what_is_left():
    a, b = Dna(sequence="ATG"), Dna(sequence="AAA")
    t = Table.of([a, b, a], {"c": {a.id: 1.0, b.id: 2.0}})
    assert t.items == [a, b]
    assert Table.of([b], t.scores).scores == {"c": {b.id: 2.0}}


def test_config_hash_says_what_the_node_does():
    same = OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA")
    assert (
        OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA").config_hash
        == same.config_hash
    )
    other = OstirExpressionConfig(utr="TTCTAGACCTCCTTATAAAAA")
    assert other.config_hash != same.config_hash
    assert same.model_dump()["config_hash"] == same.config_hash
    # The hash survives a round trip, and a wrong one is rejected.
    assert OstirExpressionConfig.model_validate_json(same.model_dump_json()) == same
    with pytest.raises(ValidationError, match="config_hash"):
        OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA", config_hash="deadbeef")
    # It covers every field, and the version.
    assert (
        MutateSynonymousConfig(seed=1).config_hash
        != MutateSynonymousConfig(seed=2).config_hash
    )
    assert (
        MutateSynonymousConfig(seed=1).config_hash != DnaToProteinConfig().config_hash
    )


def test_entity_serialization_ignores_display_when_hashing():
    # Saved runs, registries and DAG columns hold these hashes, so they must not change
    # when an entity gains a field that is only for display.
    dna = Dna(sequence="ATGGCTCTGAAATAA")
    assert "display" in dna.model_dump()
    assert "display" not in dna.model_dump(context={"hashing": True})


def test_at_most_keeps_values_up_to_the_threshold():
    node = AtMost(AtMostConfig(column="c", threshold=2))
    assert node.run(items=[REF] * 3, values=[1.0, 2.0, 3.0]) == [True, True, False]


@pytest.mark.parametrize("seed", range(20))
def test_mutation_is_synonymous_and_changes_the_requested_codons(seed):
    (out,) = MutateSynonymous(MutateSynonymousConfig(seed=seed, count=2)).run(
        sequence=[REF]
    )
    assert str(Seq(out.sequence).translate()) == str(Seq(REF.sequence).translate())
    ref_codons = [REF.sequence[i : i + 3] for i in range(0, len(REF.sequence), 3)]
    out_codons = [out.sequence[i : i + 3] for i in range(0, len(out.sequence), 3)]
    assert sum(a != b for a, b in zip(ref_codons, out_codons)) == 2


def test_mutation_is_deterministic_per_seed_and_per_sequence():
    other = Dna(sequence="GCTGCTGCTGCT")
    node = MutateSynonymous(MutateSynonymousConfig(seed=7, count=2))
    assert node.run(sequence=[REF]) == node.run(sequence=[REF])
    # What else is in the list does not change a sequence's mutant.
    assert node.run(sequence=[other, REF])[1] == node.run(sequence=[REF])[0]


def test_mutation_swaps_all_it_can_when_count_is_more_than_swappable():
    # M has no synonym and the stop is not swapped, so only A, L and K can change.
    (out,) = MutateSynonymous(MutateSynonymousConfig(seed=0, count=5)).run(
        sequence=[REF]
    )
    ref_codons = [REF.sequence[i : i + 3] for i in range(0, len(REF.sequence), 3)]
    out_codons = [out.sequence[i : i + 3] for i in range(0, len(out.sequence), 3)]
    changed = [a != b for a, b in zip(ref_codons, out_codons)]
    assert changed == [False, True, True, True, False]


def test_mutation_never_swaps_the_stop_codon():
    # TAA, TAG and TGA are synonyms, but swapping one moves where the gene ends. With
    # count=5 every swappable codon goes, so the stop is the test.
    for seed in range(30):
        node = MutateSynonymous(
            MutateSynonymousConfig(seed=seed, count=5, variants_per_sequence=4)
        )
        assert {v.sequence[-3:] for v in node.run(sequence=[REF])} == {"TAA"}


def test_mutation_can_generate_multiple_variants_per_sequence():
    node = MutateSynonymous(
        MutateSynonymousConfig(seed=42, count=2, variants_per_sequence=5)
    )
    variants = node.run(sequence=[REF])
    assert len(variants) == 5
    for v in variants:
        assert str(Seq(v.sequence).translate()) == str(Seq(REF.sequence).translate())
    # At least some variants should differ from each other
    unique_seqs = {v.sequence for v in variants}
    assert len(unique_seqs) > 1


def test_complement_and_reverse_complement():
    seq = Dna(sequence="GATCGATGGGCCTATATAGGATCGAAAATCGC")
    (comp,) = DnaComplement(DnaComplementConfig()).run(sequence=[seq])
    (rc,) = DnaReverseComplement(DnaReverseComplementConfig()).run(sequence=[seq])
    assert comp.sequence == "CTAGCTACCCGGATATATCCTAGCTTTTAGCG"
    assert rc.sequence == "GCGATTTTCGATCCTATATAGGCCCATCGATC"


def test_transcribe_swaps_t_for_u():
    seq = Dna(sequence="ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG")
    (rna,) = DnaTranscribe(DnaTranscribeConfig()).run(sequence=[seq])
    assert rna == Rna(sequence="AUGGCCAUUGUAAUGGGCCGCUGAAAGGGUGCCCGAUAG")
    assert TYPES["rna"] is Rna
    with pytest.raises(ValidationError):
        Rna(sequence="ATG")


def test_back_transcribe_swaps_u_for_t():
    rna = Rna(sequence="ACUUCUAAUUUAUUCUAUUUAUUCGCGGAUAUGCAUAGGAGUGCUUCGAUGUCAU")
    (dna,) = RnaBackTranscribe(RnaBackTranscribeConfig()).run(sequence=[rna])
    assert dna == Dna(
        sequence="ACTTCTAATTTATTCTATTTATTCGCGGATATGCATAGGAGTGCTTCGATGTCAT"
    )


def test_node_contracts_have_intents_and_guidelines():
    from node_dag.factory import MAPPING

    for config_cls in MAPPING:
        contract = config_cls.contract()
        assert "intents" in contract
        assert len(contract["intents"]) >= 1
        assert "when_to_use" in contract
        assert contract["when_to_use"]
        assert "when_not_to_use" in contract
