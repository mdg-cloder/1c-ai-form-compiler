from __future__ import annotations

import random
import unittest

from one_c_form_compiler.core.errors import ErrorCode
from one_c_form_compiler.core.parser import ParseError, ParseLimits, emit_unchanged, parse_form


class ParserUnitTests(unittest.TestCase):
    def test_preserves_utf8_bom_quotes_braces_commas_and_cyrillic(self) -> None:
        raw = b'\xef\xbb\xbf {1,"\xd0\x9a\xd0\xb8\xd1\x97\xd0\xb2, {x} and ""quote""",,{2,3}, {#base64:YWJj==\r\n},""}\r\n'
        parsed = parse_form(raw, source_name="synthetic.form")
        self.assertEqual(parsed.encoding, "utf-8-sig")
        self.assertEqual(emit_unchanged(parsed), raw)
        self.assertEqual(parsed.root.raw, raw)
        self.assertEqual(parsed.form.raw, raw[4:-2])
        self.assertEqual(parsed.tokens[0].kind, "BOM")
        self.assertTrue(any(t.kind == "OPAQUE_BASE64" for t in parsed.tokens))
        self.assertTrue(any(t.kind == "STRING" and b'""quote""' in t.raw for t in parsed.tokens))
        self.assertIn("EMPTY_VALUE", [child.kind for child in parsed.form.children if hasattr(child, "kind")])

    def test_windows_1251_synthetic_round_trip(self) -> None:
        raw = b'{1,"\xcf\xf0\xe8\xe2\xe5\xf2, {x}"}'
        parsed = parse_form(raw)
        self.assertEqual(parsed.encoding, "windows-1251")
        self.assertEqual(emit_unchanged(parsed), raw)

    def test_utf8_without_bom_round_trip(self) -> None:
        raw = '{1,"Київ"}'.encode("utf-8")
        parsed = parse_form(raw)
        self.assertEqual(parsed.encoding, "utf-8")
        self.assertFalse(parsed.source.has_bom)
        self.assertEqual(emit_unchanged(parsed), raw)

    def test_string_with_escaped_leading_quote_and_quote_only_value(self) -> None:
        for raw, expected_token in (
            (b'{"""hello"""}', b'"""hello"""'),
            (b'{""""}', b'""""'),
            (b'{""}', b'""'),
        ):
            with self.subTest(raw=raw):
                parsed = parse_form(raw)
                strings = [token.raw for token in parsed.tokens if token.kind == "STRING"]
                self.assertEqual(strings, [expected_token])
                self.assertEqual(emit_unchanged(parsed), raw)

    def test_bom_utf8_error_uses_raw_source_byte_offset(self) -> None:
        raw = b'\xef\xbb\xbf{"\xff"}'
        with self.assertRaises(ParseError) as caught:
            parse_form(raw, source_name="bad-bom-utf8.form")
        self.assertEqual(caught.exception.diagnostic.byte_offset, 5)
        self.assertEqual(caught.exception.diagnostic.source_name, "bad-bom-utf8.form")

    def test_rejects_disallowed_ascii_control_bytes(self) -> None:
        for byte in (0x00, 0x01, 0x08, 0x0E, 0x1F, 0x7F):
            raw = b"{" + bytes((byte,)) + b"}"
            with self.subTest(byte=byte), self.assertRaises(ParseError) as caught:
                parse_form(raw)
            self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_INVALID_BYTE.value)
            self.assertEqual(caught.exception.diagnostic.byte_offset, 1)
            self.assertEqual(caught.exception.diagnostic.details["byte_value"], byte)

    def test_raw_allocation_budget_rejects_nested_slices_before_large_copy(self) -> None:
        depth = 40
        raw = b"{" * depth + b"1" + b"}" * depth
        self.assertLess(len(raw), 128)
        with self.assertRaises(ParseError) as caught:
            parse_form(raw, limits=ParseLimits(max_depth=depth, max_raw_allocation_bytes=512))
        diagnostic = caught.exception.diagnostic
        self.assertEqual(diagnostic.code, ErrorCode.PARSER_RAW_ALLOCATION_LIMIT.value)
        self.assertLessEqual(diagnostic.details["allocated"], diagnostic.details["limit"])
        self.assertGreater(diagnostic.details["allocated"] + diagnostic.details["requested"], diagnostic.details["limit"])

    def test_raw_allocation_budget_cannot_exceed_safety_cap(self) -> None:
        with self.assertRaises(ValueError):
            ParseLimits(max_raw_allocation_bytes=64 * 1024 * 1024 + 1)

    def test_all_leaf_lexemes_cover_source_exactly(self) -> None:
        raw = b'\xef\xbb\xbf{ 1, "a" ,{2,,3}, {#base64:eHl6}, }'
        parsed = parse_form(raw)
        self.assertEqual(b"".join(token.raw for token in parsed.tokens), raw)

    def test_empty_top_level_list(self) -> None:
        for raw in (b"{}", b"\xef\xbb\xbf{\r\n}\r\n"):
            self.assertEqual(emit_unchanged(parse_form(raw)), raw)

    def test_rejects_unterminated_quote_with_byte_line_column(self) -> None:
        raw = b'\xef\xbb\xbf{1,\r\n"abc\nx'
        with self.assertRaises(ParseError) as caught:
            parse_form(raw, source_name="broken.form")
        d = caught.exception.diagnostic
        self.assertEqual(d.code, ErrorCode.PARSER_UNTERMINATED_STRING.value)
        self.assertEqual(d.byte_offset, len(raw))
        self.assertEqual((d.line, d.column), (3, 2))
        self.assertEqual(d.source_name, "broken.form")

    def test_depth_limit_configuration_cannot_exceed_safety_cap(self) -> None:
        with self.assertRaises(ValueError):
            ParseLimits(max_depth=257)

    def test_configured_depth_boundary_is_accepted(self) -> None:
        depth = 256
        raw = b"{" * depth + b"}" * depth
        parsed = parse_form(raw, limits=ParseLimits(max_depth=depth, max_tokens=depth * 2))
        self.assertEqual(emit_unchanged(parsed), raw)

    def test_rejects_unclosed_list(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{1,{2}")
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_UNEXPECTED_TOKEN.value)
        self.assertEqual(caught.exception.diagnostic.byte_offset, 6)

    def test_rejects_missing_separator(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{1 2}")
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_UNEXPECTED_TOKEN.value)
        self.assertEqual(caught.exception.diagnostic.byte_offset, 3)

    def test_rejects_trailing_root(self) -> None:
        with self.assertRaises(ParseError):
            parse_form(b"{} {}")

    def test_rejects_incomplete_base64_block(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{{#base64:abc")
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_UNTERMINATED_BASE64.value)

    def test_input_size_limit(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{12345}", limits=ParseLimits(max_input_bytes=4))
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_INPUT_TOO_LARGE.value)
        self.assertEqual(caught.exception.diagnostic.byte_offset, 4)

    def test_token_limit(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{1,2}", limits=ParseLimits(max_tokens=3))
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_TOKEN_LIMIT.value)

    def test_depth_limit(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse_form(b"{{{}}}", limits=ParseLimits(max_depth=2))
        self.assertEqual(caught.exception.diagnostic.code, ErrorCode.PARSER_DEPTH_LIMIT.value)

    def test_rejects_unknown_profile_and_utf16(self) -> None:
        with self.assertRaises(ParseError) as profile_error:
            parse_form(b"{}", profile="0-26")
        self.assertEqual(profile_error.exception.diagnostic.code, ErrorCode.UNSUPPORTED_PROFILE.value)
        with self.assertRaises(ParseError) as encoding_error:
            parse_form(b"\xff\xfe{\x00}\x00")
        self.assertEqual(encoding_error.exception.diagnostic.code, ErrorCode.PARSER_UNSUPPORTED_ENCODING.value)


class DeterministicMutationTests(unittest.TestCase):
    def test_fixed_seed_byte_mutations_are_bounded_and_controlled(self) -> None:
        rng = random.Random(20260929)
        seed = b'\xef\xbb\xbf{1,"text, {comma}, ""quote""",{2,,3}, {#base64:YWJj==\r\n}}'
        alphabet = b'{}()," abc\r\n'
        for _ in range(300):
            candidate = bytearray(seed)
            for _ in range(rng.randint(1, 4)):
                if candidate and rng.choice((True, False)):
                    index = rng.randrange(len(candidate))
                    candidate[index] = rng.choice(alphabet)
                else:
                    index = rng.randrange(len(candidate) + 1)
                    candidate[index:index] = bytes((rng.choice(alphabet),))
            raw = bytes(candidate)
            try:
                parsed = parse_form(raw, limits=ParseLimits(max_input_bytes=256, max_depth=32, max_tokens=128))
            except ParseError as error:
                self.assertIsInstance(error.diagnostic.byte_offset, int)
                self.assertGreaterEqual(error.diagnostic.byte_offset, 0)
            else:
                self.assertEqual(emit_unchanged(parsed), raw)


if __name__ == "__main__":
    unittest.main()
