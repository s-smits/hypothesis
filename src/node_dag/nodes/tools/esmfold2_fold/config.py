from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, ProteinStructure


class Esmfold2FoldConfig(BaseToolConfig):
    """Predict 3D structures of amino acid sequences, as monomers or as complexes.

    Runs `ESMFold2-Fast <https://huggingface.co/biohub/ESMFold2-Fast>`_, the
    single-sequence (no MSA) variant, on a GPU on Modal. With only the ``sequence``
    port wired and ``as_complex`` off, one structure comes back per sequence, as an
    mmCIF string, and each is a monomer: a sequence is folded on its own, with no
    partner chain, nucleic acid or ligand.

    The optional ``partner`` port takes chains to fold *with* each sequence, on
    `ESMFold2 <https://huggingface.co/biohub/ESMFold2>`_, the full model that
    conditions chains on each other. Wire it and one complex comes back per
    sequence, each holding that sequence and every partner chain, which is how to
    put a list of designs against a fixed target. Leave it unwired and the node
    behaves exactly as it did before it had the port.

    ``as_complex`` instead folds everything that arrives on both ports together as
    one complex, and returns that one structure.

    A complex's chains are lettered in arrival order, the sequence port's first,
    and its ``sequence`` is those chains concatenated in the same order. Identical
    sequences merge into one entity within a port, so a homodimer has to be asked
    for by putting the sequence on both ports, not twice on one. Protein chains
    only — no nucleic acid or ligand.

    A trailing ``*`` is dropped, since a stop codon is not part of the protein. A
    sequence with a ``*`` in the middle is not one chain, so it is rejected.

    Args:
        num_loops: How many times the model refines its prediction. More is slower and
            usually a little better.
        num_sampling_steps: How many diffusion steps make the final coordinates. More
            is slower; below about 20 the structure starts to degrade.
        seed: The seed for the diffusion sampling, so the same sequence folds to the
            same structure every time. Change it to see another sample of the fold.
        as_complex: Fold everything that arrives, on both ports, together as one
            complex on ESMFold2, and return that one structure. Off, each sequence
            folds with the partner chains if any arrived, else alone on
            ESMFold2-Fast, and one structure comes back per sequence.
    """

    name: Literal["esmfold2_fold"] = "esmfold2_fold"
    num_loops: int = Field(default=3, ge=1)
    num_sampling_steps: int = Field(default=50, ge=1)
    seed: int = 0
    as_complex: bool = False
    categories = (Category.CONVERSION,)
    # A cold start builds the image and pulls the weights before any folding starts.
    timeout_minutes: ClassVar[int] = 60
    inputs: ClassVar = {
        "sequence": AminoAcidSequence,
        "partner": AminoAcidSequence,
    }
    optional_inputs: ClassVar = ("partner",)
    output: ClassVar = ProteinStructure
    intents: ClassVar = (
        "fold a protein sequence into a 3D structure",
        "predict protein structure from an amino acid sequence",
        "run ESMFold / ESMFold2 structure prediction",
        "get a monomer structure as mmCIF for a protein",
        "fold several protein sequences together as one complex",
        "predict a protein-protein complex or multimer structure",
        "co-fold each design with a target chain",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the 3D structure, fold, or conformation of a "
        "protein sequence, or for a structure to score or inspect downstream. "
        "Wire partner when each sequence should be folded against the same other "
        "chain, e.g. designs against a target. Set as_complex when the goal asks "
        "how several proteins fold together as one, e.g. a heteromultimer."
    )
    when_not_to_use: ClassVar = (
        "Do not use for protein-DNA, protein-RNA or protein-ligand complexes: "
        "only protein chains fold. Do not use on DNA or RNA sequences; translate "
        "them first. Do not put the same sequence twice on one port to get a "
        "homodimer: identical entities merge. Put it on both ports instead."
    )
