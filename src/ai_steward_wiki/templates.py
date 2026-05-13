# FILE: src/ai_steward_wiki/templates.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Slug-validated markdown template loader for user-facing strings
#            (onboarding intro, /start, /help, /manual). Shared loader so
#            wording lives in templates/, not in code.
#   SCOPE: render_template(path, required_slugs, **vars), TemplateError.
#   DEPENDS: pathlib, re
#   LINKS: D-030, D-032, D-041, M-AUTH-ONBOARDING, M-TG-HANDLERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   render_template - read template, validate slug set matches required_slugs, format with vars
#   TemplateError - raised on slug mismatch (missing / extra)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - initial package-level slug-validated loader (aisw-s5i Phase A)
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["TemplateError", "render_template"]

# Slug-разметка: <!-- slug:name --> where name is [a-z][a-z0-9_-]*
# (mirrors the regex already used by auth.onboarding to preserve back-compat.)
_SLUG_RE = re.compile(r"<!--\s*slug:([a-z][a-z0-9_-]*)\s*-->")


class TemplateError(ValueError):
    """Raised when a template's slug set does not exactly match the required set."""


# START_CONTRACT: render_template
#   PURPOSE: Load a markdown template, validate that its slug-разметка matches
#            required_slugs exactly (no missing, no extra), then format with vars.
#   INPUTS: { path: Path - template file
#             required_slugs: frozenset[str] - expected slug names
#             **format_vars: str - values for {placeholder} substitution }
#   OUTPUTS: { str - rendered template body }
#   SIDE_EFFECTS: file read only
#   LINKS: M-AUTH-ONBOARDING (adapter caller), M-TG-HANDLERS (consumer)
# END_CONTRACT: render_template
def render_template(
    path: Path,
    *,
    required_slugs: frozenset[str],
    **format_vars: str,
) -> str:
    # START_BLOCK_TEMPLATES_RENDER
    text = path.read_text(encoding="utf-8")  # may raise FileNotFoundError
    found = frozenset(m.group(1) for m in _SLUG_RE.finditer(text))
    missing = required_slugs - found
    extra = found - required_slugs
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={sorted(missing)}")
        if extra:
            parts.append(f"extra={sorted(extra)}")
        raise TemplateError(f"slug mismatch in {path}: {'; '.join(parts)}")
    return text.format(**format_vars)
    # END_BLOCK_TEMPLATES_RENDER
