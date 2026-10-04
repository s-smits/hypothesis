from node_dag.links import report
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.types import AminoAcidSequence, ProteinStructure


def _chain(sequence: str) -> str:
    """The one chain to fold: the sequence without its stop codon.

    Raises:
        ValueError: If a ``*`` sits anywhere but the end, so the sequence is not one
            chain.
    """
    chain = sequence.rstrip("*")
    if "*" in chain:
        raise ValueError(f"A stop codon mid-sequence is not one chain: {sequence!r}")
    if not chain:
        raise ValueError("Nothing to fold: the sequence is only stop codons.")
    return chain


def app_page_url(app: object) -> str | None:
    """A running modal app's page, or None if this modal cannot say where it is.

    The page is ``/apps/<workspace>/<environment>/<app id>``. Only the backend knows
    the workspace and the environment, and it tells the client when the app starts,
    so neither ``app_id`` nor ``get_dashboard_url`` is enough: a URL built from the
    app id alone 404s. ``modal.App`` wraps the object that was told and does not pass
    it on, so look through the wrapper for it. A modal that keeps it somewhere else
    gets no link, rather than one that goes nowhere.
    """
    for wrapped in vars(app).values():
        if url := getattr(getattr(wrapped, "_running_app", None), "app_page_url", None):
            return url
    return None


def fold_remote(
    sequences: list[str],
    as_complex: bool,
    num_loops: int,
    num_sampling_steps: int,
    seed: int,
) -> list[str]:
    """Fold the sequences on a GPU on Modal and return the mmCIF strings.

    With ``as_complex`` the whole list co-folds as one complex and one mmCIF comes
    back; otherwise each sequence folds alone and one comes back per sequence.
    """
    # Importing modal_app starts no container, but it does need modal credentials, so
    # only reach for it when a step actually runs.
    import modal

    from node_dag.nodes.tools.esmfold2_fold.modal_app import (
        COMPLEX_MODEL,
        MONOMER_MODEL,
        app,
        fold,
    )

    groups = [sequences] if as_complex else [[s] for s in sequences]
    model = COMPLEX_MODEL if as_complex else MONOMER_MODEL
    # An ephemeral run, so nothing has to be deployed first. The GPU is held only for
    # this call, and the weights come off a volume rather than Hugging Face.
    with modal.enable_output(), app.run():
        # The app exists now, so its page on Modal has the logs, the GPU and the cost
        # of this fold. Offer it while the step runs, since that is when you want it.
        if url := app_page_url(app):
            report("Modal", url)
        return fold.remote(groups, model, num_loops, num_sampling_steps, seed)


class Esmfold2Fold(BaseNode[Esmfold2FoldConfig]):
    """Fold each amino acid sequence, alone or together as one complex."""

    def run(self, sequence: list[AminoAcidSequence]) -> list[ProteinStructure]:
        """Return the predicted structures, as mmCIF.

        One structure per sequence, or one for the whole list when ``as_complex``
        folds them together. A complex's ``sequence`` is its chains concatenated
        in arrival order, the convention ``pdbfixer_fix.sequence_of`` also keeps.
        """
        if not sequence:
            return []
        chains = [_chain(s.sequence) for s in sequence]
        structures = fold_remote(
            chains,
            self.config.as_complex,
            self.config.num_loops,
            self.config.num_sampling_steps,
            self.config.seed,
        )
        if self.config.as_complex:
            return [ProteinStructure(sequence="".join(chains), structure=structures[0])]
        return [
            ProteinStructure(sequence=c, structure=s)
            for c, s in zip(chains, structures, strict=True)
        ]
