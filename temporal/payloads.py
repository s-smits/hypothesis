"""Offload large Temporal payloads to files under ``$NODE_DAG_RESULTS``.

Temporal refuses to send a payload past its size limit (TMPRL1103), and a folding
step returning twenty ``ProteinStructure`` entities, mmCIF strings and all, is
already past it. ``ExternalStorage`` swaps any payload over its threshold for a
small reference, stored through a driver; every boundary goes through it, so a
step's inputs and result, the child workflow's, the saved run and the
Hypothesis passed between rounds are all covered by the one mechanism.

The driver here writes each oversized payload under
``<results>/payloads/<sha256 of its bytes>``, the same local store the node cache
and the saved runs already use. Content-addressed files mean a repeat of the same
payload is still one file, and a reference in a run's history resolves for as
long as the file lasts. A reference only resolves where the file is, which is
the same caveat as the node cache (see ``results_root``). Everything past the
boundary — the node, the saved run, the Hypothesis, the verifier's view — sees
the payload itself, so no contract or file format changes.
"""

import dataclasses
import hashlib
from collections.abc import Sequence

from temporalio.api.common.v1 import Payload
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import (
    DataConverter,
    ExternalStorage,
    StorageDriver,
    StorageDriverClaim,
    StorageDriverRetrieveContext,
    StorageDriverStoreContext,
)

from temporal.dag.activities import results_subdir, write_atomic


class ResultsDriver(StorageDriver):
    """Keep oversized payloads as files under ``<results>/payloads/``.

    ``name`` goes into every reference left in a run's history, so it must not
    change while those runs may still be replayed.
    """

    def name(self) -> str:
        """The name a stored reference points back to."""
        return "results"

    async def store(
        self,
        context: StorageDriverStoreContext,
        payloads: Sequence[Payload],
    ) -> list[StorageDriverClaim]:
        """Write each payload to a file named by its hash, and claim it by name."""
        claims = []
        for payload in payloads:
            # The whole message, metadata and all, so retrieve gives back exactly
            # the payload the converter would have sent.
            data = payload.SerializeToString()
            name = hashlib.sha256(data).hexdigest()
            write_atomic(results_subdir("payloads") / name, data)
            claims.append(StorageDriverClaim(claim_data={"file": name}))
        return claims

    async def retrieve(
        self,
        context: StorageDriverRetrieveContext,
        claims: Sequence[StorageDriverClaim],
    ) -> list[Payload]:
        """Read each claimed file back into the payload that was stored."""
        return [
            Payload.FromString(
                (results_subdir("payloads") / claim.claim_data["file"]).read_bytes()
            )
            for claim in claims
        ]


# A payload over the ExternalStorage default of 256 KiB becomes a file and a
# reference; anything smaller travels as it did. Every client and worker must use
# this, since a reference cannot be read without the driver configured.
data_converter: DataConverter = dataclasses.replace(
    pydantic_data_converter,
    external_storage=ExternalStorage(drivers=[ResultsDriver()]),
)
