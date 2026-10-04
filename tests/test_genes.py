import hashlib
import json

import pytest
from Bio.Seq import Seq

from node_dag import genes


def test_the_committed_gene_set_is_real_coding_dna():
    """The benchmark's reference set: every record must be usable as a CDS."""
    gs = genes.load()

    assert gs.path == genes.DEFAULT
    assert len(gs.cds) == gs.provenance["kept"] == 400
    assert gs.provenance["accession"] == "NC_000913.3"
    for r in gs.cds:
        assert set(r["sequence"]) <= set("ACGT"), r["id"]
        assert len(r["sequence"]) % 3 == 0, r["id"]
        # In-frame coding DNA: it must translate, and stop only at the end. Table 11
        # is the bacterial code, which is what an E. coli CDS is read with.
        protein = str(Seq(r["sequence"]).translate(table=11))
        assert protein.endswith("*"), r["id"]
        assert "*" not in protein[:-1], r["id"]


def test_the_gene_set_records_its_own_provenance():
    """A manifest copies this, so an attempt says which data it used."""
    gs = genes.load()
    p = gs.provenance

    for key in ("accession", "organism", "source", "selection", "sha256"):
        assert p[key]
    assert p["total_annotated"] >= p["total_usable"] >= p["kept"]
    # The digest covers the sequences actually shipped, so an edit is detectable.
    digest = hashlib.sha256(
        "".join(f"{r['id']}:{r['sequence']}" for r in gs.cds).encode()
    ).hexdigest()
    assert digest == p["sha256"]


def test_another_gene_set_can_be_supplied(tmp_path):
    """Nothing fetches: a different set is a file someone passes in."""
    path = tmp_path / "mine.json"
    path.write_text(
        json.dumps(
            {
                "provenance": {"accession": "made up for the test"},
                "cds": [{"id": "x", "gene": "x", "sequence": "ATGTAA"}],
            }
        )
    )

    gs = genes.load(path)

    assert gs.sequences() == ["ATGTAA"]
    assert gs.provenance["accession"] == "made up for the test"


@pytest.mark.parametrize(
    ("doc", "says"),
    [
        ("not json at all", "not valid JSON"),
        (json.dumps({"provenance": {}}), "no `cds` list"),
        (json.dumps({"cds": []}), "holds no coding sequences"),
        (json.dumps({"cds": [{"id": "x"}]}), "have no sequence"),
    ],
)
def test_an_unusable_gene_set_says_why(tmp_path, doc: str, says: str):
    path = tmp_path / "bad.json"
    path.write_text(doc)

    with pytest.raises(genes.GeneSetError, match=says):
        genes.load(path)


def test_a_missing_gene_set_says_so(tmp_path):
    with pytest.raises(genes.GeneSetError, match="Could not read"):
        genes.load(tmp_path / "nope.json")
