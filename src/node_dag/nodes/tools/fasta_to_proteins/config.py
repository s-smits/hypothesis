from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, FastaFile


class FastaToProteinsConfig(BaseToolConfig):
    """Read the amino acid sequences out of each FASTA file.

    Parses every ``>`` record in the file with Biopython
    `SeqIO <https://biopython.org/wiki/SeqIO>`_ and returns one
    ``amino_acid_sequence`` per record. The ``>`` header line is dropped: an
    entity is its sequence and nothing else, so two records with the same
    residues merge into one downstream, as for any list. Lower-case letters are
    upper-cased.

    A file with no record is an error, and so is a record whose letters are not
    amino acid codes: it is named in the error so the file can be fixed.
    """

    name: Literal["fasta_to_proteins"] = "fasta_to_proteins"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"file": FastaFile}
    output: ClassVar = AminoAcidSequence
    intents: ClassVar = (
        "read protein sequences from a FASTA file",
        "extract amino acid sequences from FASTA records",
        "turn an uploaded FASTA file into sequences to run on",
    )
    when_to_use: ClassVar = (
        "Use when a goal's input is a FASTA file of protein sequences, to get "
        "the amino acid sequences each record holds."
    )
    when_not_to_use: ClassVar = (
        "Do not use for a FASTA of nucleotide sequences: A, C, G and T are all "
        "amino acid letters, so a DNA record parses as a meaningless protein."
    )
