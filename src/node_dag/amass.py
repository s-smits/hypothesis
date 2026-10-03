"""Search the literature and other biomedical records with the Amass API.

Every reply is cached under ``$NODE_DAG_RESULTS/amass`` (``results/amass`` by
default), keyed by the request, so asking the same thing twice costs one call. Only
replies that succeed are cached. See https://platform.amass.tech/documentation.
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
from typing import Any, Literal

from node_dag.storage import write_atomic

logger = logging.getLogger(__name__)

BASE_URL = "https://api.amass.tech/api/v1"
Core = Literal[
    "biomedcore", "trialcore", "drugcore", "regulatorycore", "genecore", "patentcore"
]
# The include fields that hold a record's full text, for the cores that have one.
# Amass rejects a request that includes a field its core does not know.
FULL_TEXT: dict[str, tuple[str, ...]] = {
    "biomedcore": ("fulltext",),
    "patentcore": ("claims", "description"),
}


class AmassError(Exception):
    """A request to Amass failed, or there is no API key to make one."""


def cache_dir() -> Path:
    """``$NODE_DAG_RESULTS/amass``. The root defaults to ``results``."""
    return Path(os.environ.get("NODE_DAG_RESULTS", "results")) / "amass"


def _cache_path(kind: str, core: str, request: dict[str, Any]) -> Path:
    key = json.dumps({"core": core, **request}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return cache_dir() / core / kind / f"{digest}.json"


def _get(path: str, params: list[tuple[str, str]]) -> Any:  # noqa: ANN401
    """GET ``path`` and return the ``data`` of the reply."""
    key = os.environ.get("AMASS_API_KEY")
    if not key:
        raise AmassError("AMASS_API_KEY is not set.")
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        # A long patent's claims and description can take over a minute to arrive.
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)["data"]
    except urllib.error.HTTPError as e:
        err = AmassError(f"Amass {e.code} for {path}: {e.read().decode()}")
    except (urllib.error.URLError, TimeoutError) as e:
        err = AmassError(f"Could not reach Amass: {getattr(e, 'reason', e)}")
    # Failures are not cached, so this log is the only trace of them.
    logger.warning("%s (params %s)", err, params)
    raise err


def _cached(
    kind: str, core: str, request: dict[str, Any], fetch: Callable[[], Any]
) -> Any:  # noqa: ANN401
    """The cached reply to ``request``, or ``fetch()``'s, which is then cached."""
    path = _cache_path(kind, core, request)
    if path.exists():
        return json.loads(path.read_text())["data"]
    data = fetch()
    body = {"core": core, "request": request, "data": data}
    write_atomic(path, json.dumps(body, indent=2).encode())
    return data


def search(core: Core, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search the records of ``core`` for ``query`` and return up to ``limit``.

    Raises:
        AmassError: If the request fails.
    """
    request = {"query": query, "limit": limit}
    return _cached(
        "search",
        core,
        request,
        lambda: _get(
            f"/cores/{core}/records", [("query", query), ("limit", str(limit))]
        ),
    )


def get_record(
    core: Core, amass_id: str, include: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Fetch one record of ``core`` by its Amass id, with any extra ``include`` fields.

    Raises:
        AmassError: If the request fails, e.g. there is no such record.
    """
    request = {"amass_id": amass_id, "include": sorted(include)}
    return _cached(
        "records",
        core,
        request,
        lambda: _get(
            f"/cores/{core}/records/{urllib.parse.quote(amass_id)}",
            [("include", f) for f in sorted(include)],
        ),
    )
