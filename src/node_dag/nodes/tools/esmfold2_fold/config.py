from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, ProteinStructure


class Esmfold2FoldConfig(BaseToolConfig):
    """Predict the 3D structure of each amino acid sequence, as a monomer.

    Runs `ESMFold2-Fast <https://huggingface.co/biohub/ESMFold2-Fast>`_, the
    single-sequence (no MSA) variant, on a GPU on Modal. One structure comes back per
    sequence, as an mmCIF string, and each is a monomer: a sequence is folded on its
    own, never with another chain, a nucleic acid or a ligand.

    A trailing ``*`` is dropped, since a stop codon is not part of the protein. A
    sequence with a ``*`` in the middle is not one chain, so it is rejected.

    Args:
        num_loops: How many times the model refines its prediction. More is slower and
            usually a little better.
        num_sampling_steps: How many diffusion steps make the final coordinates. More
            is slower; below about 20 the structure starts to degrade.
        seed: The seed for the diffusion sampling, so the same sequence folds to the
            same structure every time. Change it to see another sample of the fold.
    """

    name: Literal["esmfold2_fold"] = "esmfold2_fold"
    num_loops: int = Field(default=3, ge=1)
    num_sampling_steps: int = Field(default=50, ge=1)
    seed: int = 0
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
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the 3D structure, fold, or conformation of a "
        "protein sequence, or for a structure to score or inspect downstream."
    )
    when_not_to_use: ClassVar = (
        "Do not use for complexes: it folds each sequence alone, with no partner "
        "chain, DNA, RNA or ligand. Do not use on DNA or RNA sequences; translate "
        "them first."
    )
