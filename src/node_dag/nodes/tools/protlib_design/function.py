import re
from typing import Any

from node_dag.links import report
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.esmfold2_fold.function import app_page_url
from node_dag.nodes.tools.protlib_design.config import ProtlibDesignConfig
from node_dag.types import AminoAcidSequence, ProteinStructure

_WHOLE = re.compile(r"\*([A-Za-z])\*")  # *A* : the whole chain.
_RANGE = re.compile(r"\*([A-Za-z])\{(\d+)-(\d+)\}")  # *A{3-20} : a closed range.
_EXPLICIT = re.compile(r"([A-Z])([A-Za-z])(\d+)")  # WA12 : one residue.


def positions_for(spec: str, sequence: str) -> list[str]:
    """Expand one position spec into explicit ``{WT}{chain}{index}`` entries.

    Index ``i`` is the residue ``sequence[i-1]``. An explicit entry keeps its
    claimed wild-type letter, so it must agree with the sequence.

    Raises:
        ValueError: If the spec cannot be parsed, lands outside the sequence, or
            names a wild-type the sequence does not have there.
    """
    if m := _WHOLE.fullmatch(spec):
        chain = m.group(1)
        return [f"{aa}{chain}{i}" for i, aa in enumerate(sequence, 1)]
    if m := _RANGE.fullmatch(spec):
        chain, start, end = m.group(1), int(m.group(2)), int(m.group(3))
        if start < 1 or end > len(sequence) or start > end:
            raise ValueError(
                f"{spec!r} is outside a sequence of length {len(sequence)}."
            )
        return [f"{sequence[i - 1]}{chain}{i}" for i in range(start, end + 1)]
    if m := _EXPLICIT.fullmatch(spec):
        wt, i = m.group(1), int(m.group(3))
        if i < 1 or i > len(sequence) or sequence[i - 1] != wt:
            raise ValueError(f"{spec!r}: position {i} is not {wt} in this sequence.")
        return [spec]
    raise ValueError(
        f"Cannot parse position spec {spec!r}: use 'WA12', '*A*' or '*A{{3-20}}'."
    )


def apply_solution(sequence: str, solution: str) -> str:
    """The variant sequence for one solution, e.g. ``"WA12C,YA34D"`` applied.

    Raises:
        ValueError: If a mutation's wild-type does not match the sequence, so the
            library would not descend from this structure.
    """
    variant = list(sequence)
    for mutation in solution.split(","):
        wt, i = mutation[0], int(mutation[2:-1]) - 1
        if not 0 <= i < len(variant) or variant[i] != wt:
            raise ValueError(f"Mutation {mutation!r} does not match this sequence.")
        variant[i] = mutation[-1]
    return "".join(variant)


def design_remote(payloads: list[dict], options: dict[str, Any]) -> list[list[str]]:
    """Design the libraries on a GPU on Modal: one list of solutions per payload."""
    # Importing modal_app starts no container, but it does need modal credentials,
    # so only reach for it when a step actually runs.
    import modal

    from node_dag.nodes.tools.protlib_design.modal_app import app, design

    # An ephemeral run, so nothing has to be deployed first. The GPU is held only
    # for this call, and the PLM weights come off a volume rather than Hugging Face.
    with modal.enable_output(), app.run():
        # The app exists now, so its page on Modal has the logs, the GPU and the
        # cost of this design. Offer it while the step runs.
        if url := app_page_url(app):
            report("Modal", url)
        return design.remote(payloads, options)


class ProtlibDesign(BaseNode[ProtlibDesignConfig]):
    """Design a diverse variant library per structure with protlib-designer."""

    def run(self, structure: list[ProteinStructure]) -> list[AminoAcidSequence]:
        """Return the variant sequences of each structure's library."""
        if not structure:
            return []
        payloads = []
        for s in structure:
            if "*" in s.sequence:
                raise ValueError(f"A stop codon cannot be mutated: {s.sequence!r}")
            positions = list(
                dict.fromkeys(
                    p
                    for spec in self.config.positions
                    for p in positions_for(spec, s.sequence)
                )
            )
            # The scorers work on one chain at a time.
            if len({p[1] for p in positions}) > 1:
                raise ValueError(
                    "All positions must name one chain: "
                    f"{sorted({p[1] for p in positions})}."
                )
            payloads.append(
                {
                    "sequence": s.sequence,
                    "mmcif": s.structure,
                    "positions": positions,
                }
            )
        options = {
            k: getattr(self.config, k)
            for k in (
                "seed",
                "use_ifold",
                "plm_models",
                "plm_chain_type",
                "library_size",
                "min_mut",
                "max_mut",
                "forbidden_aa",
                "max_arom_per_seq",
                "dissimilarity_tolerance",
                "interleave_mutant_order",
                "force_mutant_order_balance",
                "schedule",
                "schedule_param",
                "weighted_multi_objective",
                "data_normalization",
            )
        }
        libraries = design_remote(payloads, options)
        return [
            AminoAcidSequence(sequence=apply_solution(s.sequence, solution))
            for s, solutions in zip(structure, libraries, strict=True)
            for solution in solutions
        ]
