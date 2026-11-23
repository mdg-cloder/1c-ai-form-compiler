"""Bounded, lossless parser for the evidence-bounded Ordinary Form syntax.

The parser recognizes only list delimiters, separators, whitespace, quoted
strings, opaque atoms, and the observed {#base64:...} opaque block. It does not
assign meaning to positional records, atom values, IDs, controls, or properties.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .contracts import Diagnostic, Node, Severity, SourceDocument, Token
from .errors import CompilerError, ErrorCode
from .profiles import ORDINARY_0_27
from .contracts import FormProfile

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")
_BASE64_OPEN: Final = b"{#base64:"
_WHITESPACE: Final = b" \t\r\n\v\f"
_DELIMITERS: Final = b"{},\" \t\r\n\v\f"
_ALLOWED_ASCII_CONTROLS: Final = frozenset(b"\t\n\v\f\r")
_MAX_RAW_ALLOCATION_BYTES: Final = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_input_bytes: int = 8 * 1024 * 1024
    max_depth: int = 256
    max_tokens: int = 500_000
    max_raw_allocation_bytes: int = _MAX_RAW_ALLOCATION_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_input_bytes", self.max_input_bytes),
            ("max_depth", self.max_depth),
            ("max_tokens", self.max_tokens),
            ("max_raw_allocation_bytes", self.max_raw_allocation_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_depth > 256:
            raise ValueError("max_depth cannot exceed the implementation safety cap of 256")
        if self.max_raw_allocation_bytes > _MAX_RAW_ALLOCATION_BYTES:
            raise ValueError("max_raw_allocation_bytes cannot exceed the implementation safety cap of 64 MiB")


@dataclass(frozen=True, slots=True)
class ParsedForm:
    source: SourceDocument
    root: Node
    form: Node
    tokens: tuple[Token, ...]
    encoding: str
    profile_id: str

    def emit_unchanged(self) -> bytes:
        """Return authoritative source bytes; this is not a serializer for edits."""
        return self.source.raw_bytes


class ParseError(CompilerError):
    """Controlled parse error with a stable code and byte/source location."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(ErrorCode(diagnostic.code), diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class _Encoding:
    name: str
    codec: str
    bom_size: int


def _detect_encoding(data: bytes, source_name: str | None) -> _Encoding:
    if data.startswith(_UTF16_BOMS):
        raise _make_error(ErrorCode.PARSER_UNSUPPORTED_ENCODING, "UTF-16/UTF-32 byte order marks are not supported by profile 0-27", data, 0, "utf-8", source_name)
    if data.startswith(_UTF8_BOM):
        try:
            data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise _make_error(ErrorCode.PARSER_UNSUPPORTED_ENCODING, "input is not valid UTF-8 after its BOM", data, exc.start + len(_UTF8_BOM), "utf-8-sig", source_name) from exc
        return _Encoding("utf-8-sig", "utf-8-sig", len(_UTF8_BOM))
    try:
        data.decode("utf-8", errors="strict")
        return _Encoding("utf-8", "utf-8", 0)
    except UnicodeDecodeError:
        try:
            data.decode("windows-1251", errors="strict")
            return _Encoding("windows-1251", "windows-1251", 0)
        except UnicodeDecodeError as exc:
            raise _make_error(ErrorCode.PARSER_UNSUPPORTED_ENCODING, "input is neither valid UTF-8 nor valid Windows-1251", data, exc.start, "windows-1251", source_name) from exc


def _line_column(data: bytes, offset: int, codec: str) -> tuple[int, int]:
    prefix = data[: max(0, min(offset, len(data)))]
    text = prefix.decode(codec, errors="replace")
    line = 1
    column = 1
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r":
            line += 1
            column = 1
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
        elif char == "\n":
            line += 1
            column = 1
        else:
            column += 1
        index += 1
    return line, column


def _make_error(
    code: ErrorCode,
    message: str,
    data: bytes,
    offset: int,
    codec: str,
    source_name: str | None,
    *,
    details: dict[str, str | int] | None = None,
) -> ParseError:
    line, column = _line_column(data, offset, codec)
    diagnostic = Diagnostic(
        code=code.value,
        severity=Severity.ERROR,
        message=message,
        source_name=source_name,
        byte_offset=offset,
        line=line,
        column=column,
        details=details or {},
    )
    return ParseError(diagnostic)


def _validate_control_bytes(data: bytes, encoding: _Encoding, source_name: str | None) -> None:
    for offset, byte in enumerate(data):
        if (byte < 0x20 and byte not in _ALLOWED_ASCII_CONTROLS) or byte == 0x7F:
            raise _make_error(
                ErrorCode.PARSER_INVALID_BYTE,
                f"disallowed ASCII control byte 0x{byte:02x}",
                data,
                offset,
                encoding.codec,
                source_name,
                details={"byte_value": byte},
            )


class _RawAllocationBudget:
    """Charge every owned source slice before allocating its bytes copy."""

    def __init__(self, data: bytes, limit: int) -> None:
        self.data = data
        self.limit = limit
        self.allocated = 0

    def copy(self, start: int, end: int, *, source_name: str | None, codec: str) -> bytes:
        size = end - start
        if self.allocated + size > self.limit:
            raise _make_error(
                ErrorCode.PARSER_RAW_ALLOCATION_LIMIT,
                f"raw byte copy budget {self.limit} exceeded",
                self.data,
                start,
                codec,
                source_name,
                details={"limit": self.limit, "allocated": self.allocated, "requested": size},
            )
        self.allocated += size
        return self.data[start:end]


class _Lexer:
    def __init__(self, data: bytes, encoding: _Encoding, limits: ParseLimits, source_name: str | None, budget: _RawAllocationBudget) -> None:
        self.data = data
        self.encoding = encoding
        self.limits = limits
        self.source_name = source_name
        self.budget = budget
        self.tokens: list[Token] = []

    def fail(self, code: ErrorCode, message: str, offset: int) -> ParseError:
        return _make_error(code, message, self.data, offset, self.encoding.codec, self.source_name)

    def append(self, kind: str, start: int, end: int) -> None:
        if len(self.tokens) >= self.limits.max_tokens:
            raise self.fail(
                ErrorCode.PARSER_TOKEN_LIMIT,
                f"token limit {self.limits.max_tokens} exceeded",
                start,
            )
        raw = self.budget.copy(start, end, source_name=self.source_name, codec=self.encoding.codec)
        self.tokens.append(Token(kind=kind, raw=raw, start=start, end=end))

    def run(self) -> list[Token]:
        data = self.data
        size = len(data)
        i = 0
        if data.startswith(_UTF8_BOM):
            self.append("BOM", 0, len(_UTF8_BOM))
            i = len(_UTF8_BOM)
        while i < size:
            start = i
            byte = data[i]
            if byte in _WHITESPACE:
                i += 1
                while i < size and data[i] in _WHITESPACE:
                    i += 1
                self.append("WHITESPACE", start, i)
                continue
            if data.startswith(_BASE64_OPEN, i):
                end = data.find(b"}", i + len(_BASE64_OPEN))
                if end < 0:
                    raise self.fail(ErrorCode.PARSER_UNTERMINATED_BASE64, "opaque {#base64: block has no closing brace", i)
                i = end + 1
                self.append("OPAQUE_BASE64", start, i)
                continue
            if byte == ord("{"):
                i += 1
                self.append("OPEN_BRACE", start, i)
                continue
            if byte == ord("}"):
                i += 1
                self.append("CLOSE_BRACE", start, i)
                continue
            if byte == ord(","):
                i += 1
                self.append("COMMA", start, i)
                continue
            if byte == ord('"'):
                i = self._scan_string(i)
                self.append("STRING", start, i)
                continue
            i += 1
            while i < size and data[i] not in _DELIMITERS:
                i += 1
            self.append("ATOM", start, i)
        return self.tokens

    def _scan_string(self, start: int) -> int:
        data = self.data
        size = len(data)
        i = start + 1
        while i < size:
            if data[i] == ord('"'):
                if i + 1 < size and data[i + 1] == ord('"'):
                    i += 2  # In this syntax a doubled quote represents a quote in a string.
                    continue
                return i + 1
            i += 1
        raise _make_error(
            ErrorCode.PARSER_UNTERMINATED_STRING,
            "quoted string has no closing quote",
            data,
            size,
            self.encoding.codec,
            self.source_name,
            details={"opening_offset": start},
        )


class _TreeParser:
    def __init__(self, data: bytes, tokens: list[Token], limits: ParseLimits, encoding: _Encoding, source_name: str | None, budget: _RawAllocationBudget) -> None:
        self.data = data
        self.tokens = tokens
        self.limits = limits
        self.encoding = encoding
        self.source_name = source_name
        self.budget = budget
        self.index = 0

    def fail(self, message: str, offset: int) -> ParseError:
        return _make_error(ErrorCode.PARSER_UNEXPECTED_TOKEN, message, self.data, offset, self.encoding.codec, self.source_name)

    def peek(self) -> Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def consume_whitespace(self, into: list[Node | Token]) -> None:
        while (token := self.peek()) is not None and token.kind == "WHITESPACE":
            into.append(self.take())

    def parse(self) -> tuple[Node, Node]:
        document_children: list[Node | Token] = []
        if (token := self.peek()) is not None and token.kind == "BOM":
            document_children.append(self.take())
        self.consume_whitespace(document_children)
        token = self.peek()
        if token is None or token.kind != "OPEN_BRACE":
            offset = len(self.data) if token is None else token.start
            raise self.fail("expected one top-level brace list", offset)
        form = self.parse_list(depth=1)
        document_children.append(form)
        self.consume_whitespace(document_children)
        token = self.peek()
        if token is not None:
            raise self.fail("unexpected data after top-level list", token.start)
        document = Node(
            kind="document",
            raw=self.data,
            start=0,
            end=len(self.data),
            children=tuple(document_children),
            properties={"profile_id": "0-27", "encoding": self.encoding.name},
        )
        return document, form

    def parse_list(self, depth: int) -> Node:
        token = self.peek()
        if token is None or token.kind != "OPEN_BRACE":
            raise self.fail("expected opening brace", len(self.data) if token is None else token.start)
        if depth > self.limits.max_depth:
            raise _make_error(
                ErrorCode.PARSER_DEPTH_LIMIT,
                f"nesting depth limit {self.limits.max_depth} exceeded",
                self.data,
                token.start,
                self.encoding.codec,
                self.source_name,
                details={"limit": self.limits.max_depth},
            )
        start = self.take().start
        children: list[Node | Token] = [self.tokens[self.index - 1]]
        expecting_value = True
        after_comma = False
        while True:
            self.consume_whitespace(children)
            current = self.peek()
            if current is None:
                raise self.fail("brace list ended before its closing brace", len(self.data))
            if expecting_value:
                if current.kind == "CLOSE_BRACE":
                    if after_comma:
                        children.append(Token(kind="EMPTY_VALUE", raw=b"", start=current.start, end=current.start))
                    closing = self.take()
                    children.append(closing)
                    raw = self.budget.copy(start, closing.end, source_name=self.source_name, codec=self.encoding.codec)
                    return Node("list", raw, start, closing.end, tuple(children))
                if current.kind == "COMMA":
                    children.append(Token(kind="EMPTY_VALUE", raw=b"", start=current.start, end=current.start))
                    children.append(self.take())
                    after_comma = True
                    continue
                value = self.parse_value(depth)
                children.append(value)
                expecting_value = False
                after_comma = False
                continue
            if current.kind == "COMMA":
                children.append(self.take())
                expecting_value = True
                after_comma = True
                continue
            if current.kind == "CLOSE_BRACE":
                closing = self.take()
                children.append(closing)
                raw = self.budget.copy(start, closing.end, source_name=self.source_name, codec=self.encoding.codec)
                return Node("list", raw, start, closing.end, tuple(children))
            raise self.fail("expected a comma or closing brace after a value", current.start)

    def parse_value(self, parent_depth: int) -> Node | Token:
        token = self.peek()
        if token is None:
            raise self.fail("expected a value before end of input", len(self.data))
        if token.kind == "OPEN_BRACE":
            return self.parse_list(depth=parent_depth + 1)
        if token.kind in {"STRING", "ATOM", "OPAQUE_BASE64"}:
            return self.take()
        raise self.fail("expected a value, string, opaque atom, or nested list", token.start)


def parse_form(
    data: bytes,
    *,
    source_name: str | None = None,
    profile: FormProfile | str = ORDINARY_0_27,
    limits: ParseLimits = ParseLimits(),
) -> ParsedForm:
    """Parse one profile 0-27 brace list without normalizing its source bytes."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    profile_id = profile.profile_id if isinstance(profile, FormProfile) else profile
    if profile_id != "0-27":
        raise _make_error(ErrorCode.UNSUPPORTED_PROFILE, f"unsupported serialization profile: {profile_id}", data, 0, "utf-8", source_name)
    if len(data) > limits.max_input_bytes:
        raise _make_error(
            ErrorCode.PARSER_INPUT_TOO_LARGE,
            f"input size {len(data)} exceeds limit {limits.max_input_bytes}",
            data,
            limits.max_input_bytes,
            "utf-8-sig" if data.startswith(_UTF8_BOM) else "utf-8",
            source_name,
            details={"limit": limits.max_input_bytes},
        )
    encoding = _detect_encoding(data, source_name)
    _validate_control_bytes(data, encoding, source_name)
    budget = _RawAllocationBudget(data, limits.max_raw_allocation_bytes)
    tokens = _Lexer(data, encoding, limits, source_name, budget).run()
    parser = _TreeParser(data, tokens, limits, encoding, source_name, budget)
    root, form = parser.parse()
    has_bom = data.startswith(_UTF8_BOM)
    source = SourceDocument(raw_bytes=data, source_name=source_name, declared_encoding=encoding.name, has_bom=has_bom)
    return ParsedForm(source=source, root=root, form=form, tokens=tuple(tokens), encoding=encoding.name, profile_id=profile_id)


def emit_unchanged(parsed: ParsedForm) -> bytes:
    """Return the original bytes exactly; modified AST emission is out of scope."""
    return parsed.emit_unchanged()
