"""HTTP surface contracts, exercised through starlette's TestClient.

No server, no database and no network: ``get_db`` is overridden with a stub and
the socket guard is active. The defects covered here were reported against
unowned files (``api/tools.py``, ``api/projects.py``) in QA pass 1; both have
since been fixed, and these tests now hold the fixes in place.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from tests._support import no_network


def make_client() -> TestClient:
    """A client that reports handler exceptions as 500 instead of re-raising.

    ``raise_server_exceptions=False`` is what lets these tests assert on the
    status code a real caller would see rather than on the traceback.
    """
    from app.database import get_db
    from app.main import app

    async def stub_db():
        yield None

    app.dependency_overrides[get_db] = stub_db
    return TestClient(app, raise_server_exceptions=False)


class OpenApiSchemaTest(unittest.TestCase):
    """The schema must render. Everything that consumes the API depends on it."""

    def setUp(self):
        self.client = make_client()

    def test_QA_040_openapi_schema_must_render(self):
        """QA-040 (was HIGH, fixed): /openapi.json used to return 500.

        ``api/tools.py`` declares ``arguments: ToolArguments`` on a Pydantic
        model. ToolArguments aliases JSONObject, whose definition in
        ``app/utils/types.py`` uses recursive string forward references
        (``list["JSONValue"]``). Pydantic could not resolve "JSONValue" from the
        ``app.api.tools`` namespace, so ToolExecuteRequest was never fully
        defined and schema generation raised PydanticUserError -- taking the
        whole document down, not just that one route, and with it every client
        generator, /docs and any contract test.
        """
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/tools/{tool_name}", response.json()["paths"])

    def test_tool_execution_over_http_is_gated_not_broken(self):
        """The QA-040 schema fix did not switch this endpoint back on.

        This assertion previously read "must accept a request" and was expected
        to fail. It passes now -- but *not* because the endpoint accepts
        requests. ``api/tools.py`` fixed the forward reference and then
        deliberately gated execution behind ``TOOL_EXECUTION_OVER_HTTP_ENABLED``,
        because this is unauthenticated arbitrary tool execution on a service
        with no auth (security review SEC-03) and the 500 had been acting as an
        accidental control.

        So the test asserts the contract that actually holds: the request is
        *understood* (403, a decision) rather than *fatal* (500, a bug). If
        someone enables the flag, this test is where they find out what moved.
        """
        response = self.client.post("/api/tools/get_price", json={"arguments": {"coin_id": "aave"}})
        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.json()["detail"])

    def test_the_execution_gate_precedes_the_tool_lookup(self):
        """Deliberate ordering, replacing the old QA-040 404 expectation.

        Pass 1 asserted that an unknown tool name yields 404 rather than 500.
        That expectation is now obsolete rather than unmet: the 403 gate runs
        before the registry lookup, so an unauthenticated caller gets the same
        answer for a real tool and an invented one and cannot use the endpoint
        to enumerate the roster. Pinned so a later refactor cannot reintroduce
        the disclosure by moving the lookup first.
        """
        known = self.client.post("/api/tools/get_price", json={"arguments": {}})
        unknown = self.client.post("/api/tools/no_such_tool", json={"arguments": {}})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json()["detail"], unknown.json()["detail"])

    def test_the_tool_listing_endpoint_is_unaffected(self):
        """Listing was never broken by QA-040 and is not behind the gate.

        This asserted a hardcoded count of 12 until 29 Aug 2026, which made
        every tool addition look like an API-contract regression and said
        nothing about the contract. The invariant that matters is that the
        endpoint exposes the *whole* registry, ungated and unfiltered — a
        silently truncated listing is the failure worth catching, and a count
        cannot see it. ``LiveRegistryTest`` is where the roster itself is
        pinned.
        """
        from app.tools import get_tool_registry

        response = self.client.get("/api/tools")
        self.assertEqual(response.status_code, 200)
        names = {tool["name"] for tool in response.json()["tools"]}
        self.assertIn("get_price", names)
        self.assertEqual(names, set(get_tool_registry().tool_names))


class PathParameterValidationTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_QA_041_non_uuid_project_id_is_422_not_500(self):
        """QA-041 (was MED, fixed): ``uuid.UUID(project_id)`` raised in the handler.

        ``api/projects.py::get_project`` typed the path parameter as ``str`` and
        converted it by hand inside the body, so malformed input became an
        unhandled ValueError and a 500. It is now ``project_id: UUID``, which
        FastAPI rejects with a 422 before the handler or the database dependency
        is reached.

        A 500 on client-supplied input is also a monitoring problem: it is
        indistinguishable from a real server fault in logs and alerts.
        """
        for bad in ("not-a-uuid", "123", "00000000-0000-0000-0000-00000000000z"):
            response = self.client.get(f"/api/projects/{bad}")
            self.assertEqual(response.status_code, 422, bad)

    def test_a_path_that_is_not_a_project_id_at_all_is_a_404(self):
        """Traversal-shaped input does not match the route; it must not 500."""
        self.assertEqual(self.client.get("/api/projects/../etc/passwd").status_code, 404)

    def test_a_well_formed_uuid_reaches_the_handler(self):
        """Guard against over-correcting: validation must not reject valid ids.

        The stub db makes the handler fail, which is fine -- the point is that
        the request got past path validation rather than being rejected as 422.
        """
        response = self.client.get("/api/projects/3830a58c-96ec-8123-a384-d8f217a43a6e")
        self.assertNotEqual(response.status_code, 422)

    def test_error_bodies_use_the_contracted_detail_envelope(self):
        """CONTRACTS 3.4: FastAPI's default {"detail": ...}, no custom envelope."""
        for response in (
            self.client.post("/api/tools/no_such_tool", json={"arguments": {}}),
            self.client.get("/api/projects/not-a-uuid"),
        ):
            self.assertIn("detail", response.json())

    def test_error_bodies_never_leak_an_exception_string(self):
        """CONTRACTS 3.4: log the exception, return a generic message."""
        with no_network():
            for response in (
                self.client.get("/openapi.json"),
                self.client.get("/api/projects/not-a-uuid"),
                self.client.post("/api/tools/get_price", json={"arguments": {}}),
            ):
                self.assertNotIn("Traceback", response.text)
                self.assertNotIn("PydanticUserError", response.text)
                self.assertNotIn("badly formed hexadecimal", response.text)


if __name__ == "__main__":
    unittest.main()
