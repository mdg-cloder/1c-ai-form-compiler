"""Codec for EDT ordinary forms (``Form.oform``).

EDT keeps an ordinary form of a configuration object as ``Forms/<Name>/Form.oform``:
an uncompressed 1C container with two entries,

* ``form``   - the brace text of the form (FormElements27, the same text as inside an EPF);
* ``module`` - the form module.

:func:`decode_oform` writes the same files v8unpack produces for an EPF form
(``Form.json``, ``Form.elem.json``, ``Form.obj.bsl``), so :class:`OrdinaryForm`, edit plans
and the CLI work unchanged. :func:`encode_oform` builds the container back.
Unedited forms round-trip byte for byte (entry contents).

The brace text is read and written by :func:`read_brace` / :func:`write_brace` instead of v8unpack's
text-mode helpers, and panels are decoded by a ``Panel`` subclass. Defects of v8unpack 1.2.13 this avoids
(each one broke the byte-exact round trip of some forms of a typical configuration):

* CR CR LF / bare CR inside string literals were normalised by the text-mode read;
* quoted strings longer than 64 characters were wrapped as if they were base64
  (``b64decode`` without ``validate`` skips the quotes);
* a ``{#base64:`` block whose last line is full ends with a line break before ``}`` in most forms but not
  in all: the blocks without it are recorded in the meta (``base64_nobreak_blocks``) and restored;
* pages with empty or repeated names overwrote each other (pages were keyed by name): such pages are keyed
  ``<name>#<index>`` (``#`` is not valid in 1C names), e.g. ``#0``, ``#1``; the page records stay untouched;
* a panel with several pages but fewer than four guides per page had its flat guide list sliced wrongly
  (negative counts took scalars of the panel record): it is split by the page index of each guide when
  ordered by page, else it stays whole on the first page.

What still cannot be reproduced is named by :func:`form_defects` with a specific code.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from .errors import FormRefusal

OFORM_SIGNATURE = b"\xff\xff\xff\x7f"
ENTRY_FORM = "form"
ENTRY_MODULE = "module"
META_KEY = "oform"
_BASE64_BLOCK = re.compile(rb"\{#base64:[^}]*\}")


class OformError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OformParts:
    __slots__ = ("form", "module")

    def __init__(self, form: bytes, module: bytes) -> None:
        self.form = form
        self.module = module


class _FormStub:
    """The subset of v8unpack's FormCore that FormElements27 uses."""

    def __init__(self, raw: list, name: str) -> None:
        self.header = {"name": name, "form": [raw]}
        self.options: dict = {}
        self.props: list = []
        self.elements_tree: list = []
        self.elements_data: dict = {}

    @property
    def form(self) -> list:
        return self.header["form"]


def _elements27():
    return _codec()["elements"]


@functools.lru_cache(maxsize=1)
def _codec() -> dict:
    """v8unpack classes with the fixes listed in the module docstring (built on first use: v8unpack is
    imported lazily)."""
    from base64 import b64decode

    from v8unpack import helper
    from v8unpack.json_container_decoder import JsonContainerDecoder
    from v8unpack.MetaDataObject.Form.FormElements27.FormElements27 import FormElements27
    from v8unpack.MetaDataObject.Form.FormElements27.Panel import Panel

    def encode_scalar(elem: str) -> str:
        if elem.startswith("#base64:"):
            # First line: the prefix and 64 characters, then 64 per line; a full last line is followed by a
            # line break, the platform's usual ending (blocks without it are restored from the meta).
            return "\n".join([elem[:72]] + [elem[j:j + 64] for j in range(72, len(elem) + 1, 64)])
        if elem.startswith("##base64:"):
            return elem[1:]
        if len(elem) > 64 and not elem.startswith('"'):
            try:
                b64decode(elem, validate=True)
            except ValueError:
                return elem
            return "\n".join(elem[j:j + 64] for j in range(0, len(elem), 64))
        return elem

    class Decoder(JsonContainerDecoder):
        """Fed with lines split on LF only (see :func:`read_brace`): the line break CR LF becomes LF and any
        other CR stays in the value, so a literal's CR CR LF is kept as CR LF and written back as CR CR LF."""

        def decode_b64_line(self, line, start_pos):
            end = line.find("}")
            line = line.replace("\r", "") if end < 0 else line[:end].replace("\r", "") + line[end:]
            return super().decode_b64_line(line, start_pos)

        def encode_object(self, data, first=True):
            raw_data = "{" if first else "\n{"
            if not first:
                self.params_in_line = 0
            last = len(data) - 1
            for i, elem in enumerate(data):
                if isinstance(elem, list):
                    raw_data += self.encode_object(elem, False)
                    if i == last:
                        raw_data += "\n"
                    self.params_in_line = 0
                elif isinstance(elem, str):
                    raw_data += encode_scalar(elem)
                elif elem is not None:
                    raise NotImplementedError(elem)
                if i != last:
                    raw_data += ","
                    self.params_in_line += 1
            return raw_data + "}"

    class FixedPanel(Panel):
        @classmethod
        def get_class_form_elem(cls, name):
            return FixedPanel if name == "Panel" else super().get_class_form_elem(name)

        def decode_pages(self, path, raw_data):
            pages_offset = self.pages_offset(raw_data)
            pages_raw_data = raw_data[pages_offset]
            page_count = int(pages_raw_data[1])
            self.pages = []
            for i in range(page_count):
                raw_page = pages_raw_data[i + 2]
                name = helper.str_decode(raw_page[6])
                key = name
                # An empty name would key the page like the panel itself (the form's only page excepted),
                # a repeated one would overwrite the other page.
                if (not name and (page_count > 1 or path)) or name in self.pages:
                    key = f"{name}#{i}"
                if self.auto_include:
                    self.elements_tree.append({"name": key, "type": "Page", "child": []})
                elem_id = self.calc_id(path, key, None)
                self._elements_tree_index[elem_id] = len(self.elements_tree) - 1
                self.pages.append(key)
                self.elements_data[elem_id] = {"ver": self.ver, "page_format_version": raw_page[0], "raw": raw_page}
            del pages_raw_data[1:1 + 2 + page_count]
            self.elements_data[self.calc_id(path, "-pages-", None)] = self.pages

            info_offset = pages_offset + 4
            info_count = int(raw_data[info_offset])
            info = raw_data[info_offset + 1:info_offset + 1 + info_count]
            extra = info_count - 4 * page_count
            if extra < 0:
                # Fewer than four guides per page: each page gets the run of guides carrying its page index
                # (entry[5]) when the list is ordered by page, else the whole list stays on the first page.
                # The encoder joins the pages' lists in page order, so the bytes come back unchanged.
                slices = _guides_by_page(info, page_count) or [info] + [[] for _ in range(page_count - 1)]
            else:
                slices = [info[:4 + extra]] + [info[4 + extra + (i - 1) * 4:4 + extra + i * 4]
                                               for i in range(1, page_count)]
            for key, part in zip(self.pages, slices):
                self.elements_data[self.calc_id(path, key, None)]["info"] = part
            del raw_data[info_offset + 1:info_offset + 1 + info_count]
            raw_data[info_offset] = "в отдельном файле"
            if self.auto_include:
                type_offset = 2
                for _ in range(4):
                    type_count = int(raw_data[type_offset])
                    raw_data[type_offset] = "0"
                    del raw_data[type_offset + 1:type_offset + 1 + type_count]
                    type_offset += 1

    class Elements(FormElements27):
        Panel = FixedPanel

    return {"decoder": Decoder, "elements": Elements, "panel": FixedPanel}


def _guides_by_page(info: list, page_count: int) -> list[list] | None:
    """The flat guide list of a panel split by the page index of each entry; None unless it is ordered by
    page (then the parts, joined in page order, give the list back)."""
    groups: list[list] = [[] for _ in range(page_count)]
    last = 0
    for entry in info:
        try:
            index = int(entry[5])
        except (TypeError, ValueError, IndexError):
            return None
        if not last <= index < page_count:
            return None
        groups[index].append(entry)
        last = index
    return groups


def read_brace(data: bytes) -> list:
    """Parse brace text (the bytes of a container entry) without newline translation."""
    for encoding in ("utf-8-sig", "windows-1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("unknown code page")
    decoder = _codec()["decoder"](src_dir="", file_name=ENTRY_FORM)
    return decoder.decode_file(io.StringIO(text, newline="\n"))


def write_brace(data: list) -> bytes:
    """Brace text as the platform writes it: BOM, CR LF line breaks (also inside multi-line literals)."""
    text = _codec()["decoder"](src_dir="", file_name=ENTRY_FORM).encode_root_object(data)
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def form_defects(form_dir: Path) -> list[tuple[str, str]]:
    """Constructs of a decoded ordinary form (oform or EPF/ERF) that cannot be rebuilt unchanged:
    ``[(code, message)]``, empty when none. v8unpack keys elements by panel/page/name, so an element without
    a name or two elements with one name on one page overwrite each other; a form attribute whose name is not
    a string cannot be indexed; pages with empty or repeated names collide when decoded by plain v8unpack."""
    form_dir = Path(form_dir)
    try:
        elem = json.loads((form_dir / "Form.elem.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    result: list[tuple[str, str]] = []
    empty: list[str] = []
    duplicate: list[str] = []

    def walk(nodes: list, path: str) -> None:
        seen = Counter((node.get("page") or "", node.get("name")) for node in nodes if isinstance(node, dict))
        for (page, name), count in seen.items():
            where = "/".join(part for part in (path, page) if part) or "<form>"
            if not name:
                empty.append(where)
            elif count > 1:
                duplicate.append(f"{name} ({where})")
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("child"), list):
                key = "/".join(part for part in (path, node.get("page") or "", node.get("name") or "") if part)
                walk(node["child"], key)

    walk(elem.get("tree") or [], "")
    if duplicate:
        result.append((FormRefusal.FORM_DUPLICATE_NAMES.value,
                       "several elements share a name on one page: " + ", ".join(sorted(duplicate)[:5])))
    if empty:
        result.append((FormRefusal.FORM_EMPTY_NAME.value, "an element has an empty name: " + ", ".join(sorted(empty)[:5])))
    bad_props = [repr(prop.get("name"))[:40] for prop in elem.get("props") or []
                 if isinstance(prop, dict) and not isinstance(prop.get("name"), str)]
    if bad_props:
        result.append((FormRefusal.FORM_PROP_NAME.value, "a form attribute has a name that is not a string: "
                       + ", ".join(bad_props[:3])))
    pages = [key for key, value in (elem.get("data") or {}).items()
             if key.endswith("-pages-") and isinstance(value, list)
             and (len(set(value)) != len(value) or (len(value) > 1 and "" in value))]
    if pages:
        result.append((FormRefusal.FORM_DUPLICATE_PAGES.value, "a panel has pages with empty or repeated names: "
                       + ", ".join(key.rsplit("/", 1)[0] if "/" in key else "<form>" for key in pages[:5])))
    return result


def read_oform(path: Path) -> OformParts:
    path = Path(path)
    data = path.read_bytes()
    if not data.startswith(OFORM_SIGNATURE):
        raise OformError("E_OFORM_FORMAT", f"{path.name} is not an ordinary form container (Form.oform)")
    from v8unpack.container import Container
    with tempfile.TemporaryDirectory(prefix="oform-") as tmp:
        try:
            container = Container()
            with open(path, "rb") as stream:
                container.read(stream)
                container.extract(tmp, deflate=False, recursive=False)
        except Exception as error:  # v8unpack raises bare exceptions on damaged input
            raise OformError("E_OFORM_FORMAT", f"{path.name}: damaged container ({error})") from None
        names = sorted(p.name for p in Path(tmp).iterdir())
        if names != [ENTRY_FORM, ENTRY_MODULE]:
            raise OformError("E_OFORM_PROFILE",
                             f"{path.name}: unexpected container entries {names}; expected form and module")
        return OformParts((Path(tmp) / ENTRY_FORM).read_bytes(), (Path(tmp) / ENTRY_MODULE).read_bytes())


def _block_key(block: bytes) -> str:
    return hashlib.sha1(b"".join(block.split())).hexdigest()[:20]


def _base64_keys(form: bytes) -> set[str]:
    """Keys of the base64 blocks that use CR CR LF line breaks."""
    return {_block_key(m.group(0)) for m in _BASE64_BLOCK.finditer(form) if b"\r\r\n" in m.group(0)}


def _base64_nobreak_keys(form: bytes) -> set[str]:
    """Keys of the multi-line base64 blocks whose full last line is not followed by a line break."""
    result = set()
    for match in _BASE64_BLOCK.finditer(form):
        block = match.group(0)
        lines = re.split(rb"\r*\n", block[:-1])
        if len(lines) > 1 and len(lines[-1]) == 64:
            result.add(_block_key(block))
    return result


def _restore_base64_endings(form: bytes, keys: set[str]) -> bytes:
    """Drop the line break the writer puts after the full last line of the blocks listed in ``keys``."""
    def fix(match: re.Match) -> bytes:
        block = match.group(0)
        return re.sub(rb"\r*\n\}$", b"}", block) if _block_key(block) in keys else block
    return _BASE64_BLOCK.sub(fix, form) if keys else form


def _restore_base64_breaks(form: bytes, keys: set[str] | None = None) -> bytes:
    """Bring back the CR CR LF line breaks the platform writes inside embedded pictures.

    With ``keys`` only the blocks that had them in the source are changed."""
    def fix(match: re.Match) -> bytes:
        block = match.group(0)
        if keys is not None and _block_key(block) not in keys:
            return block
        return re.sub(rb"(?<!\r)\r\n", b"\r\r\n", block)
    return _BASE64_BLOCK.sub(fix, form)


def decode_oform(path: Path, form_dir: Path) -> None:
    parts = read_oform(path)
    form_dir = Path(form_dir)
    form_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = read_brace(parts.form)
        stub = _FormStub(raw, Path(path).parent.name)
        if raw[0][1][2][0] != "09ccdc77-ea1a-4a6d-ab1c-3435eada2433":
            raise ValueError("not FormElements27")
        _elements27()(stub).decode("", "", "", raw[0])
    except OformError:
        raise
    except Exception as error:
        raise OformError("E_OFORM_PROFILE",
                         f"{Path(path).name}: not an ordinary form of the supported profile ({error})") from None
    meta = {
        "crcrlf_base64": b"\r\r\n" in parts.form,
        # Pictures whose base64 used CR CR LF breaks, by payload hash: restored per block.
        "crcrlf_blocks": sorted(_base64_keys(parts.form)),
        # Pictures whose full last base64 line has no line break before "}", by payload hash.
        "base64_nobreak_blocks": sorted(_base64_nobreak_keys(parts.form)),
        "module_bom": parts.module.startswith(b"\xef\xbb\xbf"),
        "module_crlf": b"\r\n" in parts.module or b"\n" not in parts.module,
    }
    form_json = {"name": stub.header["name"], "form": stub.form, META_KEY: meta}
    elem_json = dict(params=[], props=stub.props, commands=[], tree=stub.elements_tree, data=stub.elements_data)
    with open(form_dir / "Form.json", "w", encoding="utf-8") as stream:
        json.dump(form_json, stream, ensure_ascii=False, indent=2)
    with open(form_dir / "Form.elem.json", "w", encoding="utf-8") as stream:
        json.dump(elem_json, stream, ensure_ascii=False, indent=2)
    module_text = parts.module.decode("utf-8-sig")
    with open(form_dir / "Form.obj.bsl", "w", encoding="utf-8-sig", newline="") as stream:
        stream.write(module_text)


def _module_bytes(form_dir: Path, meta: dict) -> bytes:
    path = form_dir / "Form.obj.bsl"
    text = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
    if meta.get("module_crlf", True) and "\r" not in text:
        # Rewritten by the model with LF only: back to the platform's CR LF.
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if meta.get("module_bom", True) else data


def encode_oform(form_dir: Path, out_path: Path) -> None:
    from v8unpack.container import Container
    form_dir = Path(form_dir)
    form_json = json.loads((form_dir / "Form.json").read_text(encoding="utf-8-sig"))
    meta = form_json.get(META_KEY, {})
    form = form_json["form"]
    stub = _FormStub(form[0], form_json.get("name", "form"))
    with tempfile.TemporaryDirectory(prefix="oform-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copyfile(form_dir / "Form.elem.json", tmp_dir / "Form.elem.json")
        try:
            _elements27()(stub).encode(str(tmp_dir), "Form", None, stub.form[0][0])
            form_bytes = write_brace(stub.form[0])
        except Exception as error:
            raise OformError("E_OFORM_BUILD", f"cannot build the form text: {error}") from None
        entries = tmp_dir / "entries"
        entries.mkdir()
        form_bytes = _restore_base64_endings(form_bytes, set(meta.get("base64_nobreak_blocks", [])))
        if "crcrlf_blocks" in meta:
            form_bytes = _restore_base64_breaks(form_bytes, set(meta["crcrlf_blocks"]))
        elif meta.get("crcrlf_base64"):
            form_bytes = _restore_base64_breaks(form_bytes)
        (entries / ENTRY_FORM).write_bytes(form_bytes)
        (entries / ENTRY_MODULE).write_bytes(_module_bytes(form_dir, meta))
        out_path = Path(out_path)
        with open(out_path, "wb") as stream:
            Container().build(stream, str(entries), nested=True)
