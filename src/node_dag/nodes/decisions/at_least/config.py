from typing import ClassVar, Literal

from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import Score


class AtLeastConfig(BaseDecisionConfig):
    """Filter: forward ``value`` on yes when its score is at least ``threshold``, else on no.

    Args:
        threshold: The lowest score value that takes the yes branch.
    """

    name: Literal["at_least"] = "at_least"
    threshold: float
    categories = (Category.FILTER,)
    inputs: ClassVar = {"value": Score}
    forwards = "value"
    example: ClassVar = (
        "value=score 297 with threshold=200 -> yes, forwarding the score on <step>.yes; "
        "with threshold=300 -> no, forwarding it on <step>.no"
    )
    intents: ClassVar = (
        "decide whether a score is at least a threshold",
        "select a candidate whose score beats a baseline",
        "check that expression is high enough",
        "prove a claim that a value went up",
    )
    when_to_use: ClassVar = (
        "Use after a scoring node to settle whether its score reached a minimum: "
        "expression at or above a baseline, a count at or above a floor. A score equal "
        "to the threshold takes yes. Set the threshold from the input's own baseline, "
        "so passing means something."
    )
    when_not_to_use: ClassVar = (
        "Do not use to select for a lower value; at_most is the mirror of this. Do not "
        "set a threshold every candidate clears, such as 0.0 on a positive score: the "
        "branch then decides nothing and cannot support an assertion. Its port takes a "
        "Score, so it cannot check anything about a sequence itself."
    )
