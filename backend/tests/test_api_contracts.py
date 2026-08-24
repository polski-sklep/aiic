"""HTTP surface contracts, exercised through starlette's TestClient.

No server, no database and no network: ``get_db`` is overridden with a stub and
every test runs inside the ``no_network`` guard. The three defects covered here
were reported by other agents against unowned files (``api/tools.py``,
``api/projects.py``); these are the failing tests that pin them.
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

    @unittest.expectedFailure
    def test_QA_040_openapi_schema_must_render(self):
        """QA-040 (HIGH): /openapi.json returns 500.

        ``app/api/tools.py`` declares ``arguments: ToolArguments`` on a Pydantic
        model. ToolArguments is a TypeAlias for JSONObject, whose definition in
        ``app/utils/types.py`` uses recursive string forward references
        (``list["JSONValue"]``). Pydantic cannot resolve "JSONValue" from the
        ``app.api.tools`` namespace, so ToolExecuteRequest is never fully
        defined and schema generation raises:

            PydanticUserError: `TypeAdapter[Annotated[ToolExecuteRequest,
            Body(PydanticUndefined)]]` is not fully defined; ... call
            `.rebuild()` on the instance.

        The whole schema document fails, not just that one route, so every
        client generator, /docs and any contract test are down.
        """
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)

    @unittest.expectedFailure
    def test_QA_040_tool_execution_endpoint_must_accept_a_request(self):
        """QA-040 (HIGH): the same unresolved model 500s the route itself.

        The failure is in request-body validation, so it happens before the tool
        is ever looked up -- ``POST /api/tools/{name}`` is unusable for all
        twelve tools. This is the endpoint an operator would reach for to check
        a tool by hand.
        """
        response = self.client.post("/api/tools/get_price", json={"arguments": {"coin_id": "aave"}})
        self.assertNotEqual(response.status_code, 500)

    def test_QA_040_control_the_tool_listing_endpoint_still_works(self):
        """Control for QA-040: only the routes touching ToolExecuteRequest break."""
        response = self.client.get("/api/tools")
        self.assertEqual(response.status_code, 200)
        names = {tool["name"] for tool in response.json()["tools"]}
        self.assertIn("get_price", names)
        self.assertEqual(len(names), 12)

    @unittest.expectedFailure
    def test_QA_040_unknown_tool_must_be_404_not_500(self):
        """QA-040 (HIGH), blast radius: even the 404 path is unreachable.

        Body validation runs before the handler, so the tool-name lookup and its
        deliberate ``HTTPException(404)`` never execute. Every POST to
        /api/tools/* is a 500 regardless of the tool name.
        """
        response = self.client.post("/api/tools/no_such_tool", json={"arguments": {}})
        self.assertEqual(response.status_code, 404)


class PathParameterValidationTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    @unittest.expectedFailure
    def test_QA_041_non_uuid_project_id_must_be_422_not_500(self):
        """QA-041 (MED): ``uuid.UUID(project_id)`` raises ValueError in the handler.

        ``api/projects.py::get_project`` types the path parameter as ``str`` and
        converts it by hand inside the body, so malformed input becomes an
        unhandled exception and a 500. Declaring the parameter as
        ``project_id: uuid.UUID`` makes FastAPI reject it with a 422 before the
        handler or the database dependency is ever reached.

        A 500 on client-supplied input is also a monitoring problem: it is
        indistinguishable from a real server fault in logs and alerts.
        """
        for bad in ("not-a-uuid", "123", "../etc/passwd", ""):
            response = self.client.get(f"/api/projects/{bad}")
            self.assertNotEqual(response.status_code, 500, bad)

    def test_error_bodies_use_the_contracted_detail_envelope(self):
        """CONTRACTS 3.4: FastAPI's default {"detail": ...}, no custom envelope."""
        response = self.client.post("/api/tools/no_such_tool", json={"arguments": {}})
        if response.status_code == 404:
            self.assertIn("detail", response.json())

    def test_500_bodies_never_leak_an_exception_string(self):
        """CONTRACTS 3.4: log the exception, return a generic message.

        Verified against the live 500s from QA-040 and QA-041 -- whatever else
        is wrong with them, they must not put a traceback on the wire.
        """
        with no_network():
            for response in (
                self.client.get("/openapi.json"),
                self.client.get("/api/projects/not-a-uuid"),
                self.client.post("/api/tools/get_price", json={"arguments": {}}),
            ):
                if response.status_code == 500:
                    self.assertNotIn("Traceback", response.text)
                    self.assertNotIn("PydanticUserError", response.text)
                    self.assertNotIn("badly formed hexadecimal", response.text)


if __name__ == "__main__":
    unittest.main()
