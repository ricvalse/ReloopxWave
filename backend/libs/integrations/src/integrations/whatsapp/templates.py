"""WhatsApp template helpers — variable extraction, linting, component builders.

Pure functions (no IO) so they're cheap to unit-test and reusable from both the
API (submit-time linting + component build) and the workers (send-time component
build). Mirrors the wire format 360dialog proxies from Meta Cloud API:

  * SUBMIT shape  → `POST /v1/configs/templates` components
    (`{"type":"BODY","text":"Ciao {{1}}","example":{"body_text":[["Mario"]]}}`)
  * SEND shape    → `POST /messages` template components
    (`{"type":"body","parameters":[{"type":"text","text":"Mario"}]}`)

Placeholders are Meta's positional `{{1}}..{{n}}`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Limits per Meta / 360dialog template rules (the subset we enforce in V1).
MAX_VARIABLES = 10
MAX_FOOTER_LEN = 60
MAX_HEADER_TEXT_LEN = 60
MAX_BODY_LEN = 1024
MAX_BUTTONS = 3
VALID_CATEGORIES = ("MARKETING", "UTILITY", "AUTHENTICATION")
VALID_HEADER_TYPES = ("NONE", "TEXT", "IMAGE")
VALID_BUTTON_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER")

_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


@dataclass(slots=True, frozen=True)
class LintError:
    code: str
    message: str


def extract_variables(text: str) -> list[str]:
    """Return the ordered, de-duplicated placeholder numbers in `text`.

    `"Ciao {{1}}, ordine {{2}} ({{1}})"` → `["1", "2"]`.
    """
    seen: list[str] = []
    for match in _VAR_RE.finditer(text or ""):
        num = match.group(1)
        if num not in seen:
            seen.append(num)
    return seen


def lint_template(
    *,
    body: str,
    category: str = "UTILITY",
    header_type: str = "NONE",
    header_text: str | None = None,
    footer: str | None = None,
    buttons: list[dict[str, Any]] | None = None,
) -> list[LintError]:
    """Validate a template against the rules Meta enforces before submission.

    Returns an empty list when valid. Catching these locally avoids a round-trip
    that would just bounce with a REJECTED status hours later.
    """
    errors: list[LintError] = []

    if category not in VALID_CATEGORIES:
        errors.append(LintError("CATEGORY_INVALID", f"category must be one of {VALID_CATEGORIES}"))

    if not body or not body.strip():
        errors.append(LintError("BODY_EMPTY", "body is required"))
    elif len(body) > MAX_BODY_LEN:
        errors.append(LintError("BODY_TOO_LONG", f"body exceeds {MAX_BODY_LEN} chars"))

    errors.extend(_lint_variables(body or ""))

    if header_type not in VALID_HEADER_TYPES:
        errors.append(LintError("HEADER_TYPE_INVALID", f"header_type must be {VALID_HEADER_TYPES}"))
    if header_type == "IMAGE":
        # The send path (build_send_components) cannot supply an image handle at
        # send time, so an IMAGE-header template would be registered but rejected
        # by Meta on send. Disallow until media-header send support lands (V2).
        errors.append(
            LintError("HEADER_IMAGE_UNSUPPORTED", "IMAGE headers are not supported in V1")
        )
    if header_type == "TEXT":
        if not header_text or not header_text.strip():
            errors.append(LintError("HEADER_TEXT_REQUIRED", "TEXT header needs header_text"))
        else:
            if len(header_text) > MAX_HEADER_TEXT_LEN:
                errors.append(
                    LintError("HEADER_TOO_LONG", f"header exceeds {MAX_HEADER_TEXT_LEN} chars")
                )
            if _VAR_RE.search(header_text):
                errors.append(
                    LintError("HEADER_HAS_VARIABLE", "header variables unsupported in V1")
                )

    if footer is not None:
        if len(footer) > MAX_FOOTER_LEN:
            errors.append(LintError("FOOTER_TOO_LONG", f"footer exceeds {MAX_FOOTER_LEN} chars"))
        if _VAR_RE.search(footer):
            errors.append(LintError("FOOTER_HAS_VARIABLE", "footer cannot contain variables"))

    errors.extend(_lint_buttons(buttons or []))
    return errors


def _lint_variables(body: str) -> list[LintError]:
    errors: list[LintError] = []
    nums = [int(n) for n in extract_variables(body)]
    if not nums:
        return errors
    if len(nums) > MAX_VARIABLES:
        errors.append(LintError("VAR_TOO_MANY", f"at most {MAX_VARIABLES} variables allowed"))
    # Must be 1..N sequential with no gaps.
    expected = list(range(1, len(nums) + 1))
    if sorted(nums) != expected:
        errors.append(
            LintError("VAR_NON_SEQUENTIAL", "variables must be 1..N sequential without gaps")
        )
    stripped = body.strip()
    if _VAR_RE.match(stripped):
        errors.append(LintError("VAR_AT_START", "body cannot start with a variable"))
    if re.search(r"\{\{\s*\d+\s*\}\}$", stripped):
        errors.append(LintError("VAR_AT_END", "body cannot end with a variable"))
    return errors


def _lint_buttons(buttons: list[dict[str, Any]]) -> list[LintError]:
    errors: list[LintError] = []
    if not buttons:
        return errors
    if len(buttons) > MAX_BUTTONS:
        errors.append(LintError("BUTTONS_TOO_MANY", f"at most {MAX_BUTTONS} buttons allowed"))
    for i, btn in enumerate(buttons):
        btype = str(btn.get("type", "")).upper()
        if btype not in VALID_BUTTON_TYPES:
            errors.append(LintError("BUTTON_TYPE_INVALID", f"button[{i}] type {btype!r} invalid"))
            continue
        if not str(btn.get("text", "")).strip():
            errors.append(LintError("BUTTON_TEXT_REQUIRED", f"button[{i}] needs text"))
        if btype == "URL":
            url = str(btn.get("url", ""))
            if not url:
                errors.append(LintError("BUTTON_URL_REQUIRED", f"button[{i}] needs url"))
            elif len(_VAR_RE.findall(url)) > 1:
                errors.append(
                    LintError("BUTTON_URL_VARS", f"button[{i}] URL allows at most one variable")
                )
        if btype == "PHONE_NUMBER" and not str(btn.get("phone_number", "")).strip():
            errors.append(LintError("BUTTON_PHONE_REQUIRED", f"button[{i}] needs phone_number"))
    return errors


def build_submit_components(
    *,
    body: str,
    body_examples: list[str] | None = None,
    header_type: str = "NONE",
    header_text: str | None = None,
    header_image_url: str | None = None,
    footer: str | None = None,
    buttons: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the `components` array for a template SUBMIT (`/v1/configs/templates`).

    `body_examples` supplies one sample value per `{{n}}` (Meta requires examples
    for any template that has body variables). Extra examples are ignored; missing
    ones fall back to a generic placeholder so the submit never fails validation.
    """
    components: list[dict[str, Any]] = []

    if header_type == "TEXT" and header_text:
        components.append({"type": "HEADER", "format": "TEXT", "text": header_text})
    elif header_type == "IMAGE" and header_image_url:
        components.append(
            {
                "type": "HEADER",
                "format": "IMAGE",
                "example": {"header_handle": [header_image_url]},
            }
        )

    body_component: dict[str, Any] = {"type": "BODY", "text": body}
    var_count = len(extract_variables(body))
    if var_count:
        examples = list(body_examples or [])
        row = [
            examples[i] if i < len(examples) and examples[i] else f"esempio{i + 1}"
            for i in range(var_count)
        ]
        body_component["example"] = {"body_text": [row]}
    components.append(body_component)

    if footer:
        components.append({"type": "FOOTER", "text": footer})

    if buttons:
        components.append({"type": "BUTTONS", "buttons": [_submit_button(b) for b in buttons]})

    return components


def _submit_button(btn: dict[str, Any]) -> dict[str, Any]:
    btype = str(btn.get("type", "")).upper()
    out: dict[str, Any] = {"type": btype, "text": btn.get("text", "")}
    if btype == "URL":
        out["url"] = btn.get("url", "")
        url_vars = _VAR_RE.findall(str(btn.get("url", "")))
        if url_vars:
            out["example"] = [str(btn.get("url_example", "esempio"))]
    elif btype == "PHONE_NUMBER":
        out["phone_number"] = btn.get("phone_number", "")
    return out


def build_send_components(
    *,
    body_params: list[str] | None = None,
    button_url_param: str | None = None,
    button_index: int = 0,
) -> list[dict[str, Any]]:
    """Build the `components` array for a template SEND (`/messages`).

    `body_params` are the resolved values for `{{1}}..{{n}}` in order.
    `button_url_param` fills a single URL-button variable when present.
    Returns `[]` when there's nothing to parameterise (a static template).
    """
    components: list[dict[str, Any]] = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in body_params],
            }
        )
    if button_url_param is not None:
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": str(button_index),
                "parameters": [{"type": "text", "text": str(button_url_param)}],
            }
        )
    return components


def resolve_body_params(
    *, variables: list[str], variable_mapping: dict[str, str], context: dict[str, Any]
) -> list[str]:
    """Resolve body params **positionally** ({{1}}..{{N}}) from a mapping + context.

    Meta's send `body.parameters` array is strictly positional: parameters[0]
    fills {{1}}, parameters[1] fills {{2}}, regardless of the order the
    placeholders appear in the body text. So we iterate slots 1..max, NOT the
    first-seen order of `variables` (which could be e.g. ["2", "1"] for a body
    like "{{2}} {{1}}" and would otherwise swap the values).

    `variable_mapping` maps slot → source key (e.g. {"1": "lead.first_name"});
    `context` maps dotted source keys → values. Missing values resolve to "" so
    the send never crashes on a sparse context.
    """
    if not variables:
        return []
    max_slot = max(int(v) for v in variables)
    params: list[str] = []
    for slot_num in range(1, max_slot + 1):
        source = variable_mapping.get(str(slot_num), "")
        value = context.get(source, "")
        params.append("" if value is None else str(value))
    return params
