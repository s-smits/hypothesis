from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, ProteinStructure


class Esmfold2FoldConfig(BaseToolConfig):
    """Predict 3D structures of amino acid sequences, as monomers or one complex.

    Runs `ESMFold2-Fast <https://huggingface.co/biohub/ESMFold2-Fast>`_, the
    single-sequence (no MSA) variant, on a GPU on Modal. One structure comes back
    per sequence, as an mmCIF string, and each is a monomer: a sequence is folded
    on its own, with no partner chain, nucleic acid or ligand.

    With ``as_complex`` every sequence that arrives folds together instead, on
    `ESMFold2 <https://huggingface.co/biohub/ESMFold2>`_, the full model that
    conditions chains on each other: one mmCIF comes back for the whole list, its
    chains lettered in arrival order, and its ``sequence`` is the chains
    concatenated in that order. Identical sequences merge into one entity before
    the step, so a homodimer cannot be asked for by passing the same sequence
    twice. Protein chains only — no nucleic acid or ligand.

    A trailing ``*`` is dropped, since a stop codon is not part of the protein. A
    sequence with a ``*`` in the middle is not one chain, so it is rejected.

    Args:
        num_loops: How many times the model refines its prediction. More is slower and
            usually a little better.
        num_sampling_steps: How many diffusion steps make the final coordinates. More
            is slower; below about 20 the structure starts to degrade.
        seed: The seed for the diffusion sampling, so the same sequence folds to the
            same structure every time. Change it to see another sample of the fold.
        as_complex: Fold the whole input list together as one complex, on
            ESMFold2, and return that one structure. Off, each sequence folds
            alone on ESMFold2-Fast and one structure comes back per sequence.
    """

    name: Literal["esmfold2_fold"] = "esmfold2_fold"
    num_loops: int = Field(default=3, ge=1)
    num_sampling_steps: int = Field(default=50, ge=1)
    seed: int = 0
    as_complex: bool = False
    categories = (Category.CONVERSION,)
    # A cold start builds the image and pulls the weights before any folding starts.
    timeout_minutes: ClassVar[int] = 60
    inputs: ClassVar = {"sequence": AminoAcidSequence}
    output: ClassVar = ProteinStructure
    intents: ClassVar = (
        "fold a protein sequence into a 3D structure",
        "predict protein structure from an amino acid sequence",
        "run ESMFold / ESMFold2 structure prediction",
        "get a monomer structure as mmCIF for a protein",
        "fold several protein sequences together as one complex",
        "predict a protein-protein complex or multimer structure",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the 3D structure, fold, or conformation of a "
        "protein sequence, or for a structure to score or inspect downstream. "
        "Set as_complex when the goal asks how several proteins fold together, "
        "e.g. a protein-protein interface or a heteromultimer."
    )
    when_not_to_use: ClassVar = (
        "Do not use for protein-DNA, protein-RNA or protein-ligand complexes: "
        "only protein chains fold. Do not use on DNA or RNA sequences; translate "
        "them first. A homodimer cannot be folded: identical sequences merge "
        "into one entity before the step."
    )
