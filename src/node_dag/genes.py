"""The coding sequences the recoding benchmark runs on, read from a committed file.

The benchmark needs a fixed, real reference set: fixed so a comparison can be repeated
and a split can be hashed, and real so a result says something about genes rather than
about generated strings. ``ecoli_k12_cds.json`` holds that set, with how it was selected
and a digest of its sequences in its ``provenance`` block, so a manifest can record
which data an attempt used without reaching the network.

``load`` reads the committed set; pass a path to run on another one. Nothing here
fetches anything: a different gene set is a file someone supplies, with its own
provenance, not a download this module arranges.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT = Path(__file__).with_name("data") / "ecoli_k12_cds.json"


@dataclass(frozen=True)
class GeneSet:
    """Coding sequences to benchmark on, and where they came from.

    Args:
        cds: One record per sequence, each with ``id``, ``gene``, ``protein_id``,
            ``location`` and ``sequence``. In the file's order, which is stable.
        provenance: What the file records about its own origin: the accession, the
            organism, when it was taken, how the records were selected, and a digest
            of the sequences. Copied into an experiment manifest as it stands.
        path: The file it was read from.
    """

    cds: list[dict[str, Any]]
    provenance: dict[str, Any]
    path: Path

    def sequences(self) -> list[str]:
        """Every coding sequence, in file order."""
        return [r["sequence"] for r in self.cds]


class GeneSetError(Exception):
    """A gene set file is missing, unreadable, or not shaped like one."""


def load(path: Path | None = None) -> GeneSet:
    """Read a gene set. Default: the committed E. coli K-12 set.

    Args:
        path: A JSON file shaped like the committed one: ``{"provenance": {...},
            "cds": [{"id", "gene", "protein_id", "location", "sequence"}, ...]}``.

    Raises:
        GeneSetError: The file is missing, is not JSON, or has no usable records.
    """
    p = path or DEFAULT
    try:
        doc = json.loads(p.read_bytes())
    except OSError as e:
        raise GeneSetError(f"Could not read the gene set at {p}: {e}") from e
    except json.JSONDecodeError as e:
        raise GeneSetError(f"{p} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("cds"), list):
        raise GeneSetError(f"{p} has no `cds` list. See node_dag/data for the shape.")
    cds = doc["cds"]
    missing = [
        i for i, r in enumerate(cds) if not isinstance(r, dict) or not r.get("sequence")
    ]
    if missing:
        raise GeneSetError(f"{p}: records {missing[:5]} have no sequence.")
    if not cds:
        raise GeneSetError(f"{p} holds no coding sequences.")
    return GeneSet(cds=cds, provenance=doc.get("provenance", {}), path=p)
