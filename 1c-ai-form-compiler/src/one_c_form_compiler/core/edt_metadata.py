"""EDT project lookup for configuration ordinary forms.

* :func:`resolve_form` maps ``Catalog.X.ФормаЭлемента`` (English or Russian type names) to
  ``src/Catalogs/X/Forms/ФормаЭлемента/Form.oform``.
* :func:`read_object_metadata` reads the object's attributes, standard attributes and tabular
  sections from its ``.mdo`` with the value-type ``Pattern`` records a form field uses.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .object_metadata import ObjectAttribute

# FQN type -> (folder under src/, English kind).
OBJECT_KINDS = {
    "catalog": ("Catalogs", "Catalog"), "справочник": ("Catalogs", "Catalog"),
    "document": ("Documents", "Document"), "документ": ("Documents", "Document"),
    "dataprocessor": ("DataProcessors", "DataProcessor"), "обработка": ("DataProcessors", "DataProcessor"),
    "report": ("Reports", "Report"), "отчет": ("Reports", "Report"), "отчёт": ("Reports", "Report"),
    "informationregister": ("InformationRegisters", "InformationRegister"),
    "регистрсведений": ("InformationRegisters", "InformationRegister"),
    "accumulationregister": ("AccumulationRegisters", "AccumulationRegister"),
    "регистрнакопления": ("AccumulationRegisters", "AccumulationRegister"),
    "accountingregister": ("AccountingRegisters", "AccountingRegister"),
    "регистрбухгалтерии": ("AccountingRegisters", "AccountingRegister"),
    "calculationregister": ("CalculationRegisters", "CalculationRegister"),
    "регистррасчета": ("CalculationRegisters", "CalculationRegister"),
    "chartofcalculationtypes": ("ChartsOfCalculationTypes", "ChartOfCalculationTypes"),
    "планвидоврасчета": ("ChartsOfCalculationTypes", "ChartOfCalculationTypes"),
    "filtercriterion": ("FilterCriteria", "FilterCriterion"), "критерийотбора": ("FilterCriteria", "FilterCriterion"),
    "settingsstorage": ("SettingsStorages", "SettingsStorage"),
    "хранилищенастроек": ("SettingsStorages", "SettingsStorage"),
    "chartofcharacteristictypes": ("ChartsOfCharacteristicTypes", "ChartOfCharacteristicTypes"),
    "плановидовхарактеристик": ("ChartsOfCharacteristicTypes", "ChartOfCharacteristicTypes"),
    "chartofaccounts": ("ChartsOfAccounts", "ChartOfAccounts"), "планосчетов": ("ChartsOfAccounts", "ChartOfAccounts"),
    "exchangeplan": ("ExchangePlans", "ExchangePlan"), "планобмена": ("ExchangePlans", "ExchangePlan"),
    "businessprocess": ("BusinessProcesses", "BusinessProcess"), "бизнеспроцесс": ("BusinessProcesses", "BusinessProcess"),
    "task": ("Tasks", "Task"), "задача": ("Tasks", "Task"),
    "documentjournal": ("DocumentJournals", "DocumentJournal"), "журналдокументов": ("DocumentJournals", "DocumentJournal"),
    "enum": ("Enums", "Enum"), "перечисление": ("Enums", "Enum"),
}
COMMON_FORM = {"commonform", "общаяформа"}

# Reference type prefix in .mdo -> folder of the referenced object.
REF_FOLDERS = {
    "CatalogRef": "Catalogs", "DocumentRef": "Documents", "EnumRef": "Enums",
    "ChartOfCharacteristicTypesRef": "ChartsOfCharacteristicTypes", "ChartOfAccountsRef": "ChartsOfAccounts",
    "ChartOfCalculationTypesRef": "ChartsOfCalculationTypes", "ExchangePlanRef": "ExchangePlans",
    "BusinessProcessRef": "BusinessProcesses", "TaskRef": "Tasks",
}

# Standard attribute data-path ids, observed on ordinary forms of typical configurations (8.3.27).
STANDARD_ATTRIBUTES = {
    "Catalog": {"Код": "-2", "Наименование": "-3", "Родитель": "-4", "Владелец": "-5"},
    "Document": {"Номер": "-2", "Дата": "-3"},
}


class EdtMetadataError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TabularSection:
    name: str
    uuid: str
    attributes: dict[str, ObjectAttribute]
    object_type_id: str | None = None
    # EDT project root: lets a column follow a dotted path into referenced objects (see resolve_path).
    project_dir: str | None = None


@dataclass(slots=True)
class ObjectMetadata:
    kind: str
    name: str
    object_type_id: str | None
    attributes: dict[str, ObjectAttribute] = field(default_factory=dict)
    tabular_sections: dict[str, TabularSection] = field(default_factory=dict)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in node if _local(item.tag) == name), None)


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in node if _local(item.tag) == name]


def _text(node: ET.Element | None, name: str, default: str = "") -> str:
    found = _child(node, name) if node is not None else None
    return (found.text or "").strip() if found is not None and found.text else default


def resolve_form(project_dir: Path, fqn: str) -> Path:
    project_dir = Path(project_dir)
    parts = [part for part in (fqn or "").strip().split(".") if part]
    if len(parts) >= 2 and parts[0].lower() in COMMON_FORM:
        base = project_dir / "src" / "CommonForms" / parts[1]
        return _form_file(base, f"{parts[0]}.{parts[1]}", [])
    if len(parts) == 4 and parts[2].lower() in {"form", "форма"}:
        parts = [parts[0], parts[1], parts[3]]
    if len(parts) != 3 or parts[0].lower() not in OBJECT_KINDS:
        raise EdtMetadataError("E_FORM_FQN",
                               f"form name {fqn!r} must look like Catalog.<Объект>.<Форма> "
                               "(Справочник/Документ/Обработка/Отчет/... or ОбщаяФорма.<Имя>)")
    folder, _kind = OBJECT_KINDS[parts[0].lower()]
    object_dir = project_dir / "src" / folder / parts[1]
    if not object_dir.is_dir():
        raise EdtMetadataError("E_OBJECT_NOT_FOUND", f"{parts[0]}.{parts[1]} not found in {project_dir / 'src' / folder}")
    forms_dir = object_dir / "Forms"
    available = sorted(p.name for p in forms_dir.iterdir()) if forms_dir.is_dir() else []
    return _form_file(forms_dir / parts[2], fqn, available)


def _form_file(base: Path, fqn: str, available: list[str]) -> Path:
    oform = base / "Form.oform"
    if oform.is_file():
        return oform
    if (base / "Form.form").is_file():
        raise EdtMetadataError("E_FORM_MANAGED",
                               f"{fqn} is a managed form (Form.form); edit it with AI-EDT (edit_form), not this tool")
    raise EdtMetadataError("E_FORM_NOT_FOUND",
                           f"form {fqn} not found; forms of the object: {', '.join(available) or 'none'}")


def object_mdo_for_form(form_path: Path) -> Path | None:
    """``.../X/Forms/<Form>/Form.oform`` -> ``.../X/X.mdo`` (None for common forms)."""
    form_path = Path(form_path)
    if form_path.parent.parent.name != "Forms":
        return None
    object_dir = form_path.parent.parent.parent
    mdo = object_dir / f"{object_dir.name}.mdo"
    return mdo if mdo.is_file() else None


def project_root_for(path: Path) -> Path | None:
    for parent in Path(path).resolve().parents:
        if (parent / "DT-INF").is_dir() and (parent / "src").is_dir():
            return parent
    return None


@lru_cache(maxsize=512)
def _ref_type_id(project_dir: str, folder: str, name: str) -> str | None:
    mdo = Path(project_dir) / "src" / folder / name / f"{name}.mdo"
    if not mdo.is_file():
        return None
    produced = _child(ET.parse(mdo).getroot(), "producedTypes")
    ref = _child(produced, "refType") if produced is not None else None
    return ref.get("typeId") if ref is not None else None


def _pattern(type_node: ET.Element | None, project_dir: Path | None) -> list | None:
    """``<type>`` of an attribute -> ``["Pattern", ...]``; None when a type is not supported."""
    if type_node is None:
        return None
    items: list = []
    for node in _children(type_node, "types"):
        name = (node.text or "").strip()
        if name == "String":
            qualifiers = _child(type_node, "stringQualifiers")
            length = _text(qualifiers, "length", "0")
            fixed = _text(qualifiers, "fixed") == "true"
            items.append(['"S"', length, "0" if fixed else "1"] if length != "0" else ['"S"'])
        elif name == "Number":
            qualifiers = _child(type_node, "numberQualifiers")
            items.append(['"N"', _text(qualifiers, "precision", "10"), _text(qualifiers, "scale", "0"),
                          "1" if _text(qualifiers, "nonNegative") == "true" else "0"])
        elif name == "Boolean":
            items.append(['"B"'])
        elif name == "Date":
            fractions = _text(_child(type_node, "dateQualifiers"), "dateFractions", "DateTime")
            items.append({"Date": ['"D"', '"D"'], "Time": ['"D"', '"T"']}.get(fractions, ['"D"']))
        elif "." in name and name.split(".", 1)[0] in REF_FOLDERS and project_dir is not None:
            prefix, ref_name = name.split(".", 1)
            type_id = _ref_type_id(str(project_dir), REF_FOLDERS[prefix], ref_name)
            if type_id is None:
                return None
            items.append(['"#"', type_id])
        else:
            return None
    return ['"Pattern"', *items] if items else None


def _attributes(node: ET.Element, project_dir: Path | None) -> dict[str, ObjectAttribute]:
    result = {}
    for item in _children(node, "attributes"):
        name = _text(item, "name")
        if name:
            type_node = _child(item, "type")
            names = tuple((node.text or "").strip() for node in _children(type_node, "types")) if type_node is not None else ()
            result[name] = ObjectAttribute(name, item.get("uuid", ""), _pattern(type_node, project_dir), names)
    return result


def _refs(names: list[str], project_dir: Path | None) -> list | None:
    """``["Catalog.X", ...]`` -> Pattern of their reference types (None when one is unknown)."""
    items = []
    for name in names:
        kind, _, object_name = name.partition(".")
        folder = OBJECT_KINDS.get(kind.lower(), (None,))[0]
        type_id = _ref_type_id(str(project_dir), folder, object_name) if folder and project_dir else None
        if type_id is None:
            return None
        items.append(['"#"', type_id])
    return ['"Pattern"', *items] if items else None


def _standard(kind: str, root: ET.Element, project_dir: Path | None = None) -> dict[str, ObjectAttribute]:
    ids = dict(STANDARD_ATTRIBUTES.get(kind, {}))
    owners = [(node.text or "").strip() for node in _children(root, "owners") if (node.text or "").strip()]
    if kind == "Catalog":
        # Only objects that have them: a binding to a missing standard attribute breaks the form.
        if _text(root, "hierarchical") != "true":
            ids.pop("Родитель", None)
        if not owners:
            ids.pop("Владелец", None)
    own_name = _text(root, "name")
    patterns = {
        "Родитель": _refs([f"{kind}.{own_name}"], project_dir),
        "Владелец": _refs(owners, project_dir),
        # Code length is fixed unless <codeAllowedLength>Variable</codeAllowedLength>.
        "Код": (_string(_text(root, "codeLength", "0"), _text(root, "codeAllowedLength", "Fixed") != "Variable")
                if _text(root, "codeType", "String") == "String" else None),
        "Наименование": _string(_text(root, "descriptionLength", "0")),
        "Номер": (_string(_text(root, "numberLength", "0"), _text(root, "numberAllowedLength", "Fixed") != "Variable")
                  if _text(root, "numberType", "String") == "String" else None),
        "Дата": ['"Pattern"', ['"D"']],
    }
    types = {"Родитель": (f"{kind}Ref.{own_name}",),
             "Владелец": tuple(f"{owner.partition('.')[0]}Ref.{owner.partition('.')[2]}" for owner in owners),
             "Код": ("String",) if _text(root, "codeType", "String") == "String" else ("Number",),
             "Наименование": ("String",),
             "Номер": ("String",) if _text(root, "numberType", "String") == "String" else ("Number",),
             "Дата": ("Date",)}
    return {name: ObjectAttribute(name, value, patterns.get(name), types.get(name, ())) for name, value in ids.items()}


def _string(length: str, fixed: bool = False) -> list:
    return ['"Pattern"', ['"S"', length, "0" if fixed else "1"] if length and length != "0" else ['"S"']]


def read_object_metadata(mdo: Path, project_dir: Path | None = None) -> ObjectMetadata:
    mdo = Path(mdo)
    try:
        root = ET.parse(mdo).getroot()
    except (OSError, ET.ParseError) as error:
        raise EdtMetadataError("E_METADATA_FORMAT", f"{mdo.name}: {error}") from None
    project_dir = Path(project_dir) if project_dir else project_root_for(mdo)
    kind = _local(root.tag)
    produced = _child(root, "producedTypes")
    object_type = _child(produced, "objectType") if produced is not None else None
    meta = ObjectMetadata(kind, _text(root, "name"), object_type.get("typeId") if object_type is not None else None)
    meta.attributes = {**_standard(kind, root, project_dir), **_attributes(root, project_dir)}
    for section in _children(root, "tabularSections"):
        name = _text(section, "name")
        produced = _child(section, "producedTypes")
        object_type = _child(produced, "objectType") if produced is not None else None
        meta.tabular_sections[name] = TabularSection(name, section.get("uuid", ""), _attributes(section, project_dir),
                                                     object_type.get("typeId") if object_type is not None else None,
                                                     str(project_dir) if project_dir else None)
    return meta


# Standard attributes every reference of these kinds has (names only: a dotted column shows them as text).
STANDARD_NAMES = {
    "Catalog": ("Код", "Наименование"), "ChartOfCharacteristicTypes": ("Код", "Наименование"),
    "ChartOfAccounts": ("Код", "Наименование"), "ChartOfCalculationTypes": ("Код", "Наименование"),
    "ExchangePlan": ("Код", "Наименование"), "Document": ("Номер", "Дата"),
    "BusinessProcess": ("Номер", "Дата"), "Task": ("Номер", "Дата", "Наименование"),
}


@lru_cache(maxsize=256)
def _referenced_metadata(project_dir: str, folder: str, name: str) -> ObjectMetadata | None:
    mdo = Path(project_dir) / "src" / folder / name / f"{name}.mdo"
    if not mdo.is_file():
        return None
    meta = read_object_metadata(mdo, Path(project_dir))
    for standard in STANDARD_NAMES.get(meta.kind, ()):
        meta.attributes.setdefault(standard, ObjectAttribute(standard, "", None, ()))
    return meta


def resolve_path(project_dir: str | Path | None, attribute: ObjectAttribute, names: list[str]) -> list[ObjectAttribute]:
    """Follow ``attribute.<names[0]>.<names[1]>...`` through reference types (CatalogRef.X, DocumentRef.Y ...).

    Returns the chain ``[attribute, step1, ...]``. Every link but the last must have exactly one
    reference type of an object of the EDT project; its attributes (and standard ones such as
    Код/Наименование/Номер/Дата) are read from the referenced object's ``.mdo``.
    """
    if project_dir is None:
        raise EdtMetadataError("E_DATA_UNSUPPORTED",
                               "a dotted data path needs the metadata of the referenced objects: it works in "
                               "configuration forms of an EDT project, not in EPF/ERF")
    chain = [attribute]
    for name in names:
        current = chain[-1]
        refs = [item for item in current.type_names if item.partition(".")[0] in REF_FOLDERS]
        if not current.type_names:
            raise EdtMetadataError("E_DATA_UNSUPPORTED", f"the type of {current.name} is unknown; "
                                                         "a dotted path cannot go through it")
        if len(current.type_names) != 1 or not refs:
            raise EdtMetadataError("E_DATA_TYPE", f"{current.name} is {' | '.join(current.type_names)}: a dotted path "
                                                  "goes only through a reference to one object (CatalogRef.X, "
                                                  "DocumentRef.Y ...)")
        prefix, _, object_name = refs[0].partition(".")
        meta = _referenced_metadata(str(project_dir), REF_FOLDERS[prefix], object_name)
        if meta is None:
            raise EdtMetadataError("E_DATA_UNKNOWN", f"{refs[0]} (type of {current.name}) is not in the project")
        step = meta.attributes.get(name)
        if step is None:
            raise EdtMetadataError("E_DATA_UNKNOWN", f"{refs[0]} has no attribute {name!r}; available: "
                                                     f"{', '.join(sorted(meta.attributes)) or 'none'}")
        chain.append(step)
    return chain
