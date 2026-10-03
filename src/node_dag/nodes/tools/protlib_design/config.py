from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, ProteinStructure

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


class ProtlibDesignConfig(BaseToolConfig):
    """Design a diverse protein variant library for each structure, with protlib-designer.

    Scores every single-point mutation at the chosen positions with ProteinMPNN on
    the structure and each ``plm_models`` language model on the sequence, then
    solves an integer linear programme (PuLP/CBC) for ``library_size`` variants
    that Pareto-minimise the scores under diversity constraints. Each variant
    comes back as an amino acid sequence holding between ``min_mut`` and
    ``max_mut`` mutations.

    Positions index the structure's chain sequence 1-based, so ``WA12`` is the
    residue ``sequence[11]`` on chain A, ``*A{3-20}`` is that range inclusive and
    ``*A*`` is the whole chain. All positions must name the same chain; other
    chains in the structure are held fixed as design context.

    Runs on a GPU on Modal: the scorers need torch and downloaded model weights.

    Args:
        positions: The positions open to mutation, as ``{WT}{chain}{index}``
            entries or ``*{chain}*`` / ``*{chain}{start-end}`` placeholders.
        library_size: How many variants the solver returns per structure.
        min_mut: The fewest mutations in a variant.
        max_mut: The most mutations in a variant.
        use_ifold: Score mutations with ProteinMPNN on the structure.
        plm_models: Hugging Face names of masked language models that also score
            the mutations on the bare sequence, e.g. ``facebook/esm2_t6_8M_UR50D``.
        plm_chain_type: Token type passed to PLMs that use one; only relevant to
            antibody-aware models such as ProtBert.
        forbidden_aa: Amino acids no variant may gain.
        max_arom_per_seq: At most this many aromatic residues (F, Y, W) per
            variant. None leaves the count unbounded.
        dissimilarity_tolerance: How similar chosen mutations may be, from 0.
        interleave_mutant_order: Spread mutated positions across the chain.
        force_mutant_order_balance: Balance mutated positions across the chain.
        schedule: Diversity schedule: 0 none, 1 remove the commonest
            mutation/position every ``schedule_param`` iterations, 2 remove any
            mutation/position seen more than ``schedule_param`` times.
        schedule_param: The ``p0,p1`` pair a nonzero schedule reads.
        weighted_multi_objective: Optimise a weighted sum of the score columns
            rather than their rank-1 approximation.
        data_normalization: Normalise the score matrix to [-1, 1] first.
        seed: Seeds ProteinMPNN scoring, so a config reproduces its library.
    """

    name: Literal["protlib_design"] = "protlib_design"
    positions: list[str] = Field(min_length=1)
    library_size: int = Field(default=10, ge=1)
    min_mut: int = Field(default=1, ge=0)
    max_mut: int = Field(default=4, ge=1)
    use_ifold: bool = True
    plm_models: list[str] = Field(default_factory=lambda: ["facebook/esm2_t6_8M_UR50D"])
    plm_chain_type: Literal["heavy", "light"] = "heavy"
    forbidden_aa: list[str] = Field(default_factory=list)
    max_arom_per_seq: int | None = Field(default=None, ge=0)
    dissimilarity_tolerance: float = Field(default=0.0, ge=0)
    interleave_mutant_order: bool = False
    force_mutant_order_balance: bool = False
    schedule: Literal[0, 1, 2] = 0
    schedule_param: list[int] = Field(default_factory=list)
    weighted_multi_objective: bool = True
    data_normalization: bool = False
    seed: int = 0
    categories = (Category.GENERATION,)
    # A cold start builds the image and pulls the model weights before any
    # scoring starts; the solver itself is fast.
    timeout_minutes: ClassVar[int] = 60
    inputs: ClassVar = {"structure": ProteinStructure}
    output: ClassVar = AminoAcidSequence
    intents: ClassVar = (
        "design a diverse protein variant library from a structure",
        "choose Pareto-optimal combinations of point mutations",
        "optimise a combinatorial protein library with ProteinMPNN and ESM-2",
        "generate multi-mutation protein variants under diversity constraints",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for a library or panel of protein variants from a "
        "structure, trading off predicted mutation effects against library "
        "diversity rather than producing one best sequence."
    )
    when_not_to_use: ClassVar = (
        "Do not use to rescore or filter given sequences, or for synonymous "
        "DNA-level recoding that keeps the protein sequence unchanged."
    )

    @field_validator("forbidden_aa")
    @classmethod
    def _aa(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if a not in AMINO_ACIDS]
        if bad:
            raise ValueError(f"Not amino acid codes: {bad}")
        return v

    @model_validator(mode="after")
    def _solvable(self) -> "ProtlibDesignConfig":
        if self.min_mut > self.max_mut:
            raise ValueError(f"min_mut {self.min_mut} exceeds max_mut {self.max_mut}.")
        if not self.use_ifold and not self.plm_models:
            raise ValueError(
                "Nothing would score the mutations: set use_ifold or plm_models."
            )
        if self.schedule and len(self.schedule_param) != 2:
            raise ValueError(
                f"schedule {self.schedule} needs two schedule_param values, "
                f"got {self.schedule_param}."
            )
        return self
