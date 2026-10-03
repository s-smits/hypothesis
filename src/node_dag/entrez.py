"""Look up coding sequences in NCBI Nucleotide with the E-utilities API.

Every reply is cached under ``$NODE_DAG_RESULTS/entrez`` (``results/entrez`` by
default), keyed by the request, so asking the same thing twice costs one call. Only
replies that succeed are cached. See
https://www.ncbi.nlm.nih.gov/books/NBK25501 for the API.

The point of this module is that a sequence an agent puts into a hypothesis comes
from a named record rather than from the model. Each CDS it returns carries its
accession and location, and the checks a coding sequence has to pass before
``Dna`` will take it: in frame, no ambiguity codes, a start and a stop codon.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from node_dag.storage import write_atomic

logger = logging.getLogger(__name__)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NCBI asks every caller to identify itself, and raises the rate limit for a key.
TOOL = "node-dag"
STOP_CODONS = frozenset({"TAA", "TAG", "TGA"})


class EntrezError(Exception):
    """A request to NCBI failed, or returned something this module cannot read."""


def cache_dir() -> Path:
    """``$NODE_DAG_RESULTS/entrez``. The root defaults to ``results``."""
    return Path(os.environ.get("NODE_DAG_RESULTS", "results")) / "entrez"


def _cache_path(kind: str, request: dict[str, Any]) -> Path:
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    return cache_dir() / kind / f"{digest[:16]}.json"


def _cached(kind: str, request: dict[str, Any], fetch: Callable[[], Any]) -> Any:  # noqa: ANN401
    """The cached reply to ``request``, or ``fetch()``'s, which is then cached."""
    path = _cache_path(kind, request)
    if path.exists():
        return json.loads(path.read_text())["data"]
    data = fetch()
    body = {"request": request, "data": data}
    write_atomic(path, json.dumps(body, indent=2).encode())
    return data


def _get(endpoint: str, params: list[tuple[str, str]]) -> str:
    """GET an E-utilities endpoint and return the body as text."""
    params = [*params, ("tool", TOOL)]
    if email := os.environ.get("NCBI_EMAIL"):
        params.append(("email", email))
    if key := os.environ.get("NCBI_API_KEY"):
        params.append(("api_key", key))
    url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        err = EntrezError(f"NCBI {e.code} for {endpoint}: {e.read().decode()[:400]}")
    except (urllib.error.URLError, TimeoutError) as e:
        err = EntrezError(f"Could not reach NCBI: {getattr(e, 'reason', e)}")
    # Failures are not cached, so this log is the only trace of them.
    logger.warning("%s (params %s)", err, params)
    raise err


def search(term: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search NCBI Nucleotide for ``term`` and return up to ``limit`` records.

    Args:
        term: An Entrez query, e.g. ``lacZ[gene] AND "Escherichia coli"[orgn]``.
        limit: How many records to return, at most.

    Returns:
        One dict per record: its accession, title, organism and length in bases.

    Raises:
        EntrezError: If the request fails or the reply cannot be read.
    """
    request = {"term": term, "limit": limit}
    return _cached("search", request, lambda: _search(term, limit))


def _search(term: str, limit: int) -> list[dict[str, Any]]:
    body = _get(
        "esearch.fcgi",
        [
            ("db", "nuccore"),
            ("term", term),
            ("retmax", str(limit)),
            ("retmode", "json"),
        ],
    )
    try:
        ids = json.loads(body)["esearchresult"]["idlist"]
    except (ValueError, KeyError) as e:
        raise EntrezError(f"Could not read the NCBI search reply: {e}") from e
    if not ids:
        return []
    summaries = _get(
        "esummary.fcgi",
        [("db", "nuccore"), ("id", ",".join(ids)), ("retmode", "json")],
    )
    try:
        result = json.loads(summaries)["result"]
    except (ValueError, KeyError) as e:
        raise EntrezError(f"Could not read the NCBI summary reply: {e}") from e
    return [
        {
            "accession": r.get("accessionversion") or r.get("caption", uid),
            "title": r.get("title", ""),
            "organism": r.get("organism", ""),
            "length": r.get("slen"),
        }
        for uid in ids
        if isinstance(r := result.get(uid), dict)
    ]


def _parse_header(header: str) -> dict[str, str]:
    """The ``[key=value]`` fields of a ``fasta_cds_na`` header, plus its id."""
    fields: dict[str, str] = {"id": header.split(None, 1)[0] if header else ""}
    rest = header
    while (start := rest.find("[")) != -1 and (end := rest.find("]", start)) != -1:
        key, sep, value = rest[start + 1 : end].partition("=")
        if sep:
            fields[key.strip()] = value.strip()
        rest = rest[end + 1 :]
    return fields


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    """The ``(header, sequence)`` pairs of a FASTA body."""
    records: list[tuple[str, str]] = []
    header, chunks = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(chunks)))
            header, chunks = line[1:].strip(), []
        elif header is not None and line.strip():
            chunks.append(line.strip())
    if header is not None:
        records.append((header, "".join(chunks)))
    return records


def _describe(header: str, sequence: str) -> dict[str, Any]:
    """One CDS: where it came from, and whether it is usable as coding DNA.

    ``usable`` is what ``Dna`` and the recoding nodes need: upper-case A, C, G, T,
    a whole number of codons, an ATG start and a stop codon. A real record can fail
    any of these, e.g. a partial CDS or one with ambiguity codes, so the caller is
    told rather than handed a sequence that will fail validation later.
    """
    fields = _parse_header(header)
    seq = sequence.upper()
    ambiguous = sorted(set(seq) - set("ACGT"))
    in_frame = len(seq) > 0 and len(seq) % 3 == 0
    starts = seq.startswith("ATG")
    ends = in_frame and seq[-3:] in STOP_CODONS
    reasons = []
    if ambiguous:
        reasons.append(f"holds non-ACGT codes {ambiguous}")
    if not in_frame:
        reasons.append(f"length {len(seq)} is not a whole number of codons")
    if not starts:
        reasons.append("does not start with ATG")
    if not ends:
        reasons.append("does not end with a stop codon")
    return {
        "gene": fields.get("gene", ""),
        "protein": fields.get("protein", ""),
        "protein_id": fields.get("protein_id", ""),
        "location": fields.get("location", ""),
        "id": fields.get("id", ""),
        "length": len(seq),
        "sequence": seq,
        "usable": not reasons,
        "problems": reasons,
    }


def fetch_cds(accession: str, gene: str | None = None) -> list[dict[str, Any]]:
    """Fetch the coding sequences annotated on a Nucleotide record.

    Args:
        accession: The record's accession, e.g. ``NC_000913.3`` or ``J01636.1``.
        gene: Keep only the CDS features whose ``gene`` matches this, ignoring case.
            A whole genome has thousands, so name the gene when fetching one.

    Returns:
        One dict per CDS: its gene, protein, location, sequence, and whether it is
        usable as in-frame coding DNA.

    Raises:
        EntrezError: If the request fails or the record has no CDS features.
    """
    request = {"accession": accession}
    records = _cached("cds", request, lambda: _fetch_cds(accession))
    if gene:
        records = [r for r in records if r["gene"].lower() == gene.lower()]
    return records


def _fetch_cds(accession: str) -> list[dict[str, Any]]:
    body = _get(
        "efetch.fcgi",
        [
            ("db", "nuccore"),
            ("id", accession),
            ("rettype", "fasta_cds_na"),
            ("retmode", "text"),
        ],
    )
    records = [_describe(h, s) for h, s in _parse_fasta(body)]
    if not records:
        raise EntrezError(
            f"No CDS features on {accession!r}. Check the accession, and that the "
            "record is an annotated nucleotide record rather than a bare sequence."
        )
    return records
