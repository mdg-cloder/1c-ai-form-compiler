"""Built-in neutral element templates for ordinary forms (FormElements27).

Used when the edited form contains no element of the requested type to clone.
Regenerate with ``tools/generate_ordinary_templates.py``.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

from .ordinary_form import FormModelError


@lru_cache(maxsize=1)
def _load() -> dict[str, list]:
    text = resources.files(__package__).joinpath("ordinary_templates.json").read_text(encoding="utf-8")
    return json.loads(text)["templates"]


def standard_types() -> tuple[str, ...]:
    return tuple(_load())


def standard_template(element_type: str) -> list:
    templates = _load()
    if element_type not in templates:
        raise FormModelError("E_TEMPLATE_MISSING", f"no built-in template for {element_type}")
    return copy.deepcopy(templates[element_type])
