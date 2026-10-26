"""Error chassis (roadmap B-004, decision D-13).

What this file is for: every error this API ever returns has ONE
shape — RFC 9457 problem+json — with a stable machine ``code`` the
frontend branches on (never on message text), a plain-words
``title``/``detail`` a person can read, and an ``errors[]`` list for
field-level validation problems. Boring and identical everywhere is
the point: 400, 401, 403, 404, 409, 422, 429, and 500 all look alike.

The code registry below is the contract. Codes are never renamed,
only added (roadmap §5).
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# --- The error-code registry ---------------------------------------
# code -> (HTTP status, plain-words title). Services raise AppError
# with one of these codes; the handler renders the shape.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "validation_error": (422, "The request could not be understood"),
    "not_authenticated": (401, "You are not signed in"),
    "invalid_credentials": (401, "Email or password did not match"),
    "token_expired": (401, "That link or token has expired"),
    "token_invalid": (401, "That link or token is not valid"),
    "token_reused": (401, "That token was already used"),
    "forbidden": (403, "You are not allowed to do that"),
    "insufficient_scope": (403, "This key does not have the needed scope"),
    "seat_cannot_approve": (403, "Agent seats can draft, but a person must approve"),
    "not_found": (404, "That could not be found"),
    "conflict": (409, "That conflicts with the current state"),
    "stale_version": (409, "That version is out of date"),
    "last_owner": (409, "A group must keep at least one owner"),
    "email_taken": (409, "That email already has an account"),
    "rate_limited": (429, "Too many attempts — please wait a little"),
    "quota_exceeded": (422, "That would exceed the group's storage quota"),
    "fetch_blocked": (422, "That address may not be fetched"),
    "fetch_failed": (502, "That page could not be read"),
    "internal_error": (500, "Something went wrong on our side"),
    # The internal API's own codes (Phase 17, B-162/B-164): a
    # part of the system asking another part for something it
    # may not have, in a shape it cannot prove, or twice.
    "internal_unknown_operation": (404, "That internal operation does not exist"),
    "internal_scope_denied": (403, "That part of the system does not hold the needed internal scope"),
    "internal_bad_payload": (422, "That internal call's payload could not be understood"),
    "internal_tampered": (400, "That internal message failed its integrity check"),
    "recovery_code_invalid": (401, "That recovery code is not valid"),
    "uncommitted_writes": (500, "The request finished with uncommitted writes"),
    "draft_conflict": (409, "That draft changed on the server since it was opened"),
    "upload_incomplete": (409, "That upload is still missing parts"),
    "upload_hash_mismatch": (422, "The assembled upload does not match its declared hash"),
    "upload_part_size": (422, "That part is not the size its position requires"),
    "internal_bad_signature": (401, "That internal message's signature did not verify"),
    "internal_replayed": (401, "That internal message was already seen"),
    "internal_bad_key": (500, "The internal key configuration is invalid"),
    # Advanced account security (Phase 17, B-174).
    "account_locked": (423, "This account is temporarily locked"),
    "mfa_code_invalid": (401, "That authentication code did not match"),
    "password_too_weak": (422, "That password is not strong enough"),
}


class AppError(Exception):
    """An application error carrying a registry code.

    Services raise this; routers never build error responses by hand.
    ``headers`` exists for the cases HTTP requires extras (notably
    ``Retry-After`` on a 429).
    """

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        errors: list[dict] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        status, title = ERROR_CODES[code]
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail or title
        self.errors = errors or []
        self.headers = headers or {}
        super().__init__(f"{code}: {self.detail}")


def problem_body(request: Request, err: AppError) -> dict:
    """Render one AppError as the problem+json body."""
    return {
        "type": f"about:blank#{err.code}",
        "title": err.title,
        "status": err.status,
        "code": err.code,
        "detail": err.detail,
        "errors": err.errors,
        "instance": str(request.url.path),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Attach the handlers that give every failure the same shape."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, err: AppError):
        return JSONResponse(
            problem_body(request, err),
            status_code=err.status,
            media_type="application/problem+json",
            headers=err.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, err: RequestValidationError):
        # Map pydantic's complaints into the errors[] list so the
        # frontend can point at the exact field that failed.
        fields = [
            {
                "field": ".".join(str(part) for part in e["loc"]),
                "message": e["msg"],
                "code": e["type"],
            }
            for e in err.errors()
        ]
        mapped = AppError(
            "validation_error",
            detail="Some fields need attention — see errors.",
            errors=fields,
        )
        return JSONResponse(
            problem_body(request, mapped),
            status_code=mapped.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, err: StarletteHTTPException):
        # Framework-raised errors (unknown route, wrong method) get
        # the same shape, so the frontend has exactly one parser.
        code = "not_found" if err.status_code == 404 else "validation_error"
        if err.status_code == 405:
            code = "validation_error"
        mapped = AppError(code, detail=str(err.detail))
        mapped.status = err.status_code
        return JSONResponse(
            problem_body(request, mapped),
            status_code=err.status_code,
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def _unknown(request: Request, err: Exception):
        # Last resort: a plain 500 in the same shape. The detail is
        # deliberately vague — internals never leak to clients.
        mapped = AppError("internal_error")
        return JSONResponse(
            problem_body(request, mapped),
            status_code=500,
            media_type="application/problem+json",
        )
