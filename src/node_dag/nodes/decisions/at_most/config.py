from typing import ClassVar, Literal

from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import Score


class AtMostConfig(BaseDecisionConfig):
    """Filter: forward ``value`` on yes when its score is at most ``threshold``, else on no.

    The mirror of ``at_least``: use this to select for a lower score, such as an atom
    count at or below a baseline. A score exactly equal to the threshold takes yes, so
    the threshold is the highest score that passes.

    Args:
        threshold: The highest score value that takes the yes branch.
    """

    name: Literal["at_most"] = "at_most"
    threshold: float
    categories = (Category.FILTER,)
    inputs: ClassVar = {"value": Score}
    forwards = "value"
    example: ClassVar = (
        "value=score 297 with threshold=300 -> yes, forwarding the score on <step>.yes; "
        "with threshold=200 -> no, forwarding it on <step>.no; with threshold=297 -> yes"
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
