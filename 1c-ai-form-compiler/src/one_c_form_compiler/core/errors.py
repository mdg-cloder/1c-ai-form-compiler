"""Stable machine-readable diagnostic codes for the public contract."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_SOURCE = "E_SOURCE_INVALID"
    UNSUPPORTED_PROFILE = "E_PROFILE_UNSUPPORTED"
    MALFORMED_SERIALIZATION = "E_SERIALIZATION_MALFORMED"
    INVALID_PATCH = "E_PATCH_INVALID"
    BACKEND_FAILURE = "E_BACKEND_FAILURE"
    PROVIDER_FAILURE = "E_PROVIDER_FAILURE"
    OUTPUT_FAILURE = "E_OUTPUT_FAILURE"
    CONTRACT_VIOLATION = "E_CONTRACT_VIOLATION"
    PARSER_INPUT_TOO_LARGE = "E_PARSER_INPUT_TOO_LARGE"
    PARSER_UNSUPPORTED_ENCODING = "E_PARSER_UNSUPPORTED_ENCODING"
    PARSER_TOKEN_LIMIT = "E_PARSER_TOKEN_LIMIT"
    PARSER_DEPTH_LIMIT = "E_PARSER_DEPTH_LIMIT"
    PARSER_UNEXPECTED_TOKEN = "E_PARSER_UNEXPECTED_TOKEN"
    PARSER_UNTERMINATED_STRING = "E_PARSER_UNTERMINATED_STRING"
    PARSER_UNTERMINATED_BASE64 = "E_PARSER_UNTERMINATED_BASE64"
    PARSER_INVALID_BYTE = "E_PARSER_INVALID_BYTE"
    PARSER_RAW_ALLOCATION_LIMIT = "E_PARSER_RAW_ALLOCATION_LIMIT"


class CompilerError(Exception):
    """Expected compiler failure carrying a stable public error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Codes of the ordinary-form edit sessions (BackendError.code) for forms that cannot be rebuilt unchanged.
class FormRefusal(StrEnum):
    OFORM_ROUNDTRIP = "E_OFORM_ROUNDTRIP"            # Form.oform: rebuilding the unchanged form differs
    FORM_ROUNDTRIP = "E_FORM_ROUNDTRIP"              # EPF/ERF: a no-op rebuild changes a form entry
    FORM_DUPLICATE_NAMES = "E_FORM_DUPLICATE_NAMES"  # two elements with one name on one page
    FORM_EMPTY_NAME = "E_FORM_EMPTY_NAME"            # an element without a name
    FORM_PROP_NAME = "E_FORM_PROP_NAME"              # a form attribute whose name is not a string
    FORM_DUPLICATE_PAGES = "E_FORM_DUPLICATE_PAGES"  # EPF/ERF: pages with empty or repeated names
