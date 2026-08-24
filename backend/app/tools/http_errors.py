"""One vocabulary for tool failure, shared by every tool that speaks HTTP.

QA-028/029/042. Three unrelated result shapes used to reach the model and none
of them said which of two very different things had happened:

* ``{"error": "<a sentence the tool wrote>"}``
* ``{"error": "Tool execution failed: Client error '429 ...' for url ..."}``,
  synthesised by the registry after an uncaught ``httpx`` raise
* a plain success envelope with empty lists and null fields

Only the last of those means "there is nothing to find", and an agent cannot
tell it from the other two. That distinction is load-bearing here: "this
protocol is not on DeFiLlama" is a **finding about the project**, while
"DeFiLlama did not answer" is a **gap in our data**. An agent that collapses
them writes the gap into its findings as a fact and the committee scores it.

Every failure now carries a ``failure`` key from the closed set below, and every
inconclusive one says in prose that nothing was learned.

Nothing in this module imports a tool module, the registry, or ``app.knowledge``,
so both the tool layer and ``app.knowledge.calibration`` can depend on it without
reintroducing the cycle ADR 0001 removed.
"""
from __future__ import annotations

from typing import Any, TypedDict


class ToolFailure(TypedDict, total=False):
    """The shape every tool returns when it cannot answer."""

    error: str
    failure: str
    details: str


#: The upstream answered and the thing genuinely does not exist. A finding.
NOT_FOUND = "not_found"

#: The upstream answered and holds nothing for this query. A real data gap, and
#: distinguishable from NOT_FOUND: the subject exists, the datum does not.
NO_DATA = "no_data"

#: Quota exhausted. Nothing was learned about the subject.
RATE_LIMITED = "rate_limited"

#: The upstream errored, timed out, or returned something unparseable.
UNAVAILABLE = "unavailable"

#: The request we sent was wrong. Says nothing about the subject.
BAD_REQUEST = "bad_request"

#: A credential is missing or was rejected.
NOT_CONFIGURED = "not_configured"

#: Kinds where the call did not complete. The defining property of this set is
#: that the result is *not evidence*: no inference about the subject may rest on
#: it. NOT_FOUND and NO_DATA are outside it because both are real observations.
INCONCLUSIVE_KINDS = frozenset({RATE_LIMITED, UNAVAILABLE, BAD_REQUEST, NOT_CONFIGURED})

_NOTHING_LEARNED = "No data was retrieved, so this is a gap, not a finding about the subject."


def tool_failure(kind: str, message: str, *, details: str = "") -> ToolFailure:
    """Build the failure envelope a tool returns to the model.

    The prose carries the distinction as well as the ``failure`` key, because
    the consumer is a language model reading JSON and the sentence is what it
    reasons over.
    """
    text = message.strip()
    if kind in INCONCLUSIVE_KINDS:
        text = f"{text} {_NOTHING_LEARNED}"

    failure: ToolFailure = {"error": text, "failure": kind}
    if details:
        failure["details"] = details[:300]
    return failure


def http_failure(
    service: str,
    status_code: int,
    *,
    details: str = "",
) -> ToolFailure:
    """Classify a non-2xx status into the shared vocabulary.

    404 is deliberately absent: whether a 404 means "no such protocol" or "no
    data for this protocol" depends on the endpoint, so each tool decides that
    itself rather than having it guessed here.
    """
    if status_code == 429:
        return tool_failure(
            RATE_LIMITED, f"{service} rate limit exceeded.", details=details
        )
    if status_code in (401, 403):
        return tool_failure(
            NOT_CONFIGURED,
            f"{service} rejected our credentials (HTTP {status_code}).",
            details=details,
        )
    if status_code == 400:
        return tool_failure(
            BAD_REQUEST,
            f"{service} rejected the request as malformed (HTTP 400).",
            details=details,
        )
    if 500 <= status_code < 600:
        return tool_failure(
            UNAVAILABLE, f"{service} is unavailable (HTTP {status_code}).", details=details
        )
    return tool_failure(
        UNAVAILABLE, f"{service} request failed with status {status_code}.", details=details
    )


def transport_failure(service: str, exc: BaseException) -> ToolFailure:
    """Classify a transport-level failure — timeout, DNS, connection reset.

    Only the exception's *class name* is reported. ``httpx.HTTPStatusError``
    stringifies to the full request URL and query string, which is how a query —
    or a token that a tool ever moves into a query parameter — ends up copied
    into the model's context and then into ``agent_outputs`` in Postgres.
    """
    return tool_failure(
        UNAVAILABLE,
        f"{service} could not be reached ({type(exc).__name__}).",
    )


def body_rate_limited(data: Any) -> bool:
    """True when an HTTP 200 body is actually a rate-limit error.

    CoinGecko's free tier answers an over-quota request with HTTP 200 and a body
    of ``{"status": {"error_code": 429, "error_message": ...}}``. Code that
    branches on the status code alone reads that as data, and code that then
    checks for a missing key reads it as "the coin does not exist".

    This is the canonical copy. ``app.knowledge.calibration`` carries an
    identical detector it discovered first, against ``/coins/{id}/history``;
    that module is owned by ``agent/calibration`` and should re-export this one.
    """
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if not isinstance(status, dict):
        return False
    return status.get("error_code") == 429
