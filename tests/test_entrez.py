import pytest

from node_dag import entrez

# One usable CDS and one that a real record can easily hold: a partial feature,
# not in frame, with an ambiguity code and no stop codon.
CDS_FASTA = """\
>lcl|J01636.1_cds_AAB59138.1_1 [gene=lacZ] [protein=beta-D-galactosidase] [protein_id=AAB59138.1] [location=1..15]
ATGGCTCTG
AAATAA
>lcl|J01636.1_cds_AAB59139.1_2 [gene=lacY] [protein=lac permease] [location=<16..28] [partial=true]
ATGNNNCCGGT
"""


def test_fetch_cds_reads_headers_and_flags_what_is_usable(monkeypatch):
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: CDS_FASTA)
    good, bad = entrez.fetch_cds("J01636.1")

    assert good["gene"] == "lacZ"
    assert good["protein"] == "beta-D-galactosidase"
    assert good["location"] == "1..15"
    assert good["sequence"] == "ATGGCTCTGAAATAA"  # Wrapped lines are joined.
    assert good["length"] == 15
    assert good["usable"] is True
    assert good["problems"] == []

    assert bad["gene"] == "lacY"
    assert bad["usable"] is False
    assert bad["problems"] == [
        "holds non-ACGT codes ['N']",
        "length 11 is not a whole number of codons",
        "does not end with a stop codon",
    ]


def test_fetch_cds_filters_by_gene_and_caches_the_record(monkeypatch):
    calls = []

    def fake_get(endpoint, params):
        calls.append(endpoint)
        return CDS_FASTA

    monkeypatch.setattr(entrez, "_get", fake_get)
    assert [r["gene"] for r in entrez.fetch_cds("J01636.1", "lacz")] == ["lacZ"]
    # The gene filter is applied to the cached record, not to the request, so a
    # second gene from the same record costs no second call.
    assert [r["gene"] for r in entrez.fetch_cds("J01636.1", "lacY")] == ["lacY"]
    assert calls == ["efetch.fcgi"]


def test_fetch_cds_without_cds_features_is_an_error(monkeypatch):
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: "")
    with pytest.raises(entrez.EntrezError, match="No CDS features"):
        entrez.fetch_cds("X00001.1")


def test_search_returns_accessions_and_skips_the_summary_call_when_empty(monkeypatch):
    replies = {
        "esearch.fcgi": '{"esearchresult": {"idlist": ["147"]}}',
        "esummary.fcgi": (
            '{"result": {"147": {"accessionversion": "J01636.1", "title": "lac operon",'
            ' "organism": "Escherichia coli", "slen": 7477}}}'
        ),
    }
    monkeypatch.setattr(entrez, "_get", lambda endpoint, params: replies[endpoint])
    assert entrez.search("lacZ[gene]") == [
        {
            "accession": "J01636.1",
            "title": "lac operon",
            "organism": "Escherichia coli",
            "length": 7477,
        }
    ]

    monkeypatch.setattr(
        entrez, "_get", lambda *a, **k: '{"esearchresult": {"idlist": []}}'
    )
    assert entrez.search("nothing at all[gene]") == []


def test_a_failed_request_is_not_cached(monkeypatch):
    def fail(*a, **k):
        raise entrez.EntrezError("down")

    monkeypatch.setattr(entrez, "_get", fail)
    with pytest.raises(entrez.EntrezError):
        entrez.fetch_cds("J01636.1")
    monkeypatch.setattr(entrez, "_get", lambda *a, **k: CDS_FASTA)
    assert entrez.fetch_cds("J01636.1", "lacZ")[0]["sequence"] == "ATGGCTCTGAAATAA"
