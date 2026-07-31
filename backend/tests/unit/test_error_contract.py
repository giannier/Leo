"""The error envelope, at runtime and in the published schema.

`contracts/api.md` fixes both: the body shape every failure uses, and the
status codes clients should expect. The schema half matters because the
frontend's API client is generated from it - a documented 422 the service
never sends would become a branch in generated code that can never run.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import ApiError, ErrorResponse, error_body, error_responses


class TestEnvelope:
    def test_fields_is_omitted_when_there_is_none(self) -> None:
        assert error_body(error="email_taken", message="Taken.") == {
            "error": "email_taken",
            "message": "Taken.",
        }

    def test_fields_is_included_when_present(self) -> None:
        body = error_body(error="validation_error", message="Bad.", fields={"email": "invalid"})
        assert body["fields"] == {"email": "invalid"}

    def test_the_model_matches_the_body_builder(self) -> None:
        body = error_body(error="validation_error", message="Bad.", fields={"email": "invalid"})
        assert ErrorResponse.model_validate(body).fields == {"email": "invalid"}

    def test_api_error_carries_the_whole_envelope(self) -> None:
        error = ApiError(status_code=409, error="email_taken", message="Taken.")
        assert (error.status_code, error.error, error.message, error.fields) == (
            409,
            "email_taken",
            "Taken.",
            None,
        )

    def test_error_responses_share_one_model(self) -> None:
        responses = error_responses((400, "bad"), (409, "conflict"))
        assert set(responses) == {400, 409}
        assert all(entry["model"] is ErrorResponse for entry in responses.values())


class TestPublishedSchema:
    def test_no_operation_advertises_the_422_the_api_never_sends(self, app: FastAPI) -> None:
        schema = app.openapi()
        advertised = {
            code
            for operations in schema["paths"].values()
            for operation in operations.values()
            for code in operation["responses"]
        }
        assert "422" not in advertised

    def test_the_auth_endpoints_advertise_the_envelope(self, app: FastAPI) -> None:
        schema = app.openapi()
        register = schema["paths"]["/api/v1/auth/register"]["post"]["responses"]
        assert set(register) == {"201", "400", "409", "503"}
        assert (
            register["409"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ErrorResponse"
        )


class TestFrameworkErrorsUseTheEnvelope:
    async def test_an_unknown_route_is_not_fastapis_detail_shape(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/nope")

        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "not_found"
        assert "detail" not in body

    async def test_a_wrong_method_uses_the_envelope_too(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/auth/register")

        assert response.status_code == 405
        assert response.json()["error"] == "method_not_allowed"
