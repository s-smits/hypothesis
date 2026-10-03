import pytest
from pydantic import BaseModel

from node_dag.factory import MAPPING
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.wiring import PortContract, check_wiring


class Wire(BaseModel):
    """A step wired by hand: sources per port, plus the contract of its node."""

    inputs: dict[str, str]
    port_contract: PortContract

    def contract(self) -> PortContract:
        """The ports and kinds of the node this step runs."""
        return self.port_contract


TO_PROTEIN = PortContract.of(DnaToProteinConfig)
AT_LEAST = PortContract.of(AtLeastConfig)


def _step(contract: PortContract, **inputs: str) -> Wire:
    return Wire(inputs=inputs, port_contract=contract)


@pytest.mark.parametrize(
    ("inputs", "steps", "match"),
    [
        # A step reading its own output.
        (
            {"seq": "dna"},
            {"protein": _step(TO_PROTEIN, sequence="protein")},
            "Cycle",
        ),
        # A source that is neither an input nor a step.
        (
            {"seq": "dna"},
            {"protein": _step(TO_PROTEIN, sequence="nope")},
            "Unknown source",
        ),
        # The step wires a port the node does not declare.
        (
            {"seq": "dna"},
            {"protein": _step(TO_PROTEIN, dna="seq")},
            r"ports \['dna'\] != node ports \['sequence'\]",
        ),
        # The source gives a kind the port does not take.
        (
            {"seq": "dna"},
            {
                "protein": _step(TO_PROTEIN, sequence="seq"),
                "protein2": _step(TO_PROTEIN, sequence="protein"),
            },
            "port 'sequence' takes dna, but 'protein' gives amino_acid_sequence",
        ),
    ],
)
def test_check_wiring_rejects_bad_graphs(inputs, steps, match):
    with pytest.raises(ValueError, match=match):
        check_wiring(inputs, steps)


def test_check_wiring_returns_the_kind_of_every_source():
    types = check_wiring(
        {"seq": "dna", "cut": "score"},
        {
            "protein": _step(TO_PROTEIN, sequence="seq"),
            "gate": _step(AT_LEAST, value="cut"),
        },
    )
    assert types == {
        "seq": "dna",
        "cut": "score",
        "protein": "amino_acid_sequence",
        "gate.yes": "score",
        "gate.no": "score",
    }


def test_a_tool_contract_is_its_ports_and_output():
    assert TO_PROTEIN.inputs == {"sequence": "dna"}
    assert TO_PROTEIN.output == "amino_acid_sequence"
    assert TO_PROTEIN.forwards is None


def test_a_decision_forwards_its_input_on_both_branches():
    assert AT_LEAST.sources("gate") == {"gate.yes": "score", "gate.no": "score"}


@pytest.mark.parametrize("config", MAPPING)
def test_port_contract_agrees_with_the_config(config):
    """``PortContract.of`` must say exactly what ``contract()`` ships to the agent."""
    contract, declared = PortContract.of(config), config.contract()
    assert contract.inputs == declared["inputs"]
    assert contract.sources("<step>") == declared["outputs"]
