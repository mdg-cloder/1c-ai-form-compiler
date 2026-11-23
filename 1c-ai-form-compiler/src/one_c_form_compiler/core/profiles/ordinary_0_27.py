"""The only supported serialization profile: ordinary forms, FormElements27 (platform 8.3)."""

from ..contracts import FormProfile

ORDINARY_0_27 = FormProfile(
    profile_id="0-27",
    format_name="1C Ordinary Form",
    element_decoder="FormElements27",
    platform_version="8.3.27",               # verified with 8.3.27 (descriptive, not enforced)
    operating_system="Windows",
    python_version="3.14",
    evidence_ref="checked on ordinary forms of typical 1C configurations",
    verified=True,
)
