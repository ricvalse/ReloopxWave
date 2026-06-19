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

# Limits per Meta / 360dialog template rules.
MAX_VARIABLES = 10
MAX_FOOTER_LEN = 60
MAX_HEADER_TEXT_LEN = 60
MAX_BODY_LEN = 1024
MAX_BUTTON_TEXT_LEN = 25
MAX_PHONE_LEN = 20
# Meta button caps: up to 10 total, with per-type sub-caps.
MAX_BUTTONS_TOTAL = 10
MAX_URL_BUTTONS = 2
MAX_PHONE_BUTTONS = 1
MAX_COPY_CODE_BUTTONS = 1
MAX_BUTTONS = MAX_BUTTONS_TOTAL  # back-compat alias
VALID_CATEGORIES = ("MARKETING", "UTILITY", "AUTHENTICATION")
VALID_HEADER_TYPES = ("NONE", "TEXT", "IMAGE")
VALID_BUTTON_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE")

# WhatsApp template language codes (the common Meta set). An unknown-but-format-
# valid code degrades to a *warning* — the enum drifts, so we don't hard-block.
SUPPORTED_LANGUAGES = frozenset(
    {
        "af", "sq", "ar", "az", "bn", "bg", "ca", "zh_CN", "zh_HK", "zh_TW",
        "hr", "cs", "da", "nl", "en", "en_GB", "en_US", "et", "fil", "fi",
        "fr", "ka", "de", "el", "gu", "ha", "he", "hi", "hu", "id", "ga",
        "it", "ja", "kn", "kk", "rw_RW", "ko", "ky_KG", "lo", "lv", "lt",
        "mk", "ms", "ml", "mr", "nb", "fa", "pl", "pt_BR", "pt_PT", "pa",
        "ro", "ru", "sr", "sk", "sl", "es", "es_AR", "es_ES", "es_MX", "sw",
        "sv", "ta", "te", "th", "tr", "uk", "ur", "uz", "vi", "zu",
    }
)

# Promotional wording that often gets a UTILITY template reclassified/rejected.
_PROMO_KEYWORDS = (
    "sconto", "offerta", "promo", "promozione", "gratis", "saldi", "buono",
    "coupon", "regalo", "omaggio", "% di sconto", "black friday", "occasione",
    "affare", "imperdibile", "acquista ora", "compra ora", "discount", "sale",
    "free", "% off",
)

_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")
# Two placeholders separated only by optional whitespace — Meta rejects these as
# "Invalid Format" (a variable must be surrounded by static text on both sides).
_ADJACENT_VARS_RE = re.compile(r"\{\{\s*\d+\s*\}\}\s*\{\{\s*\d+\s*\}\}")
_LANG_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
# Approximate emoji detection (stdlib re has no \p{Emoji}); covers the common
# pictographic / symbol / dingbat blocks. Used for advisory footer/auth checks.
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f0ff\U00002190-\U000021ff\U00002b00-\U00002bff]"
)


@dataclass(slots=True, frozen=True)
class LintIssue:
    """A single validation finding.

    `severity="error"` blocks the submit (deterministic Meta rule); `"warning"`
    is advisory (heuristic / reclassification risk) and never blocks. `field`
    lets the UI attach the message to the right input.
    """

    code: str
    message: str
    severity: str = "error"  # error | warning
    field: str = "body"  # body | footer | header | category | buttons | language


# Back-compat alias — older imports referenced `LintError`.
LintError = LintIssue


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
    language: str = "it",
    header_type: str = "NONE",
    header_text: str | None = None,
    footer: str | None = None,
    buttons: list[dict[str, Any]] | None = None,
    body_examples: list[str] | None = None,
) -> list[LintIssue]:
    """Validate a template against the rules Meta enforces before submission.

    Returns every finding (errors *and* advisory warnings). Callers split on
    `severity`: only `error` blocks the submit. Catching these locally avoids a
    round-trip that just bounces with a REJECTED status hours later.
    """
    issues: list[LintIssue] = []

    if category not in VALID_CATEGORIES:
        issues.append(
            LintIssue("CATEGORY_INVALID", f"category must be one of {VALID_CATEGORIES}", field="category")
        )

    issues.extend(_lint_language(language))

    if not body or not body.strip():
        issues.append(LintIssue("BODY_EMPTY", "body is required"))
    elif len(body) > MAX_BODY_LEN:
        issues.append(LintIssue("BODY_TOO_LONG", f"body exceeds {MAX_BODY_LEN} chars"))

    issues.extend(_lint_variables(body or ""))
    issues.extend(_lint_body_format(body or ""))
    issues.extend(_lint_examples(body or "", body_examples))

    if header_type not in VALID_HEADER_TYPES:
        issues.append(
            LintIssue("HEADER_TYPE_INVALID", f"header_type must be {VALID_HEADER_TYPES}", field="header")
        )
    if header_type == "IMAGE":
        # The send path (build_send_components) cannot supply an image handle at
        # send time, so an IMAGE-header template would be registered but rejected
        # by Meta on send. Disallow until media-header send support lands (V2).
        issues.append(
            LintIssue("HEADER_IMAGE_UNSUPPORTED", "IMAGE headers are not supported in V1", field="header")
        )
    if header_type == "TEXT":
        if not header_text or not header_text.strip():
            issues.append(
                LintIssue("HEADER_TEXT_REQUIRED", "TEXT header needs header_text", field="header")
            )
        else:
            if len(header_text) > MAX_HEADER_TEXT_LEN:
                issues.append(
                    LintIssue(
                        "HEADER_TOO_LONG", f"header exceeds {MAX_HEADER_TEXT_LEN} chars", field="header"
                    )
                )
            if _VAR_RE.search(header_text):
                issues.append(
                    LintIssue("HEADER_HAS_VARIABLE", "header variables unsupported in V1", field="header")
                )

    if footer is not None:
        if len(footer) > MAX_FOOTER_LEN:
            issues.append(
                LintIssue("FOOTER_TOO_LONG", f"footer exceeds {MAX_FOOTER_LEN} chars", field="footer")
            )
        if _VAR_RE.search(footer):
            issues.append(
                LintIssue("FOOTER_HAS_VARIABLE", "footer cannot contain variables", field="footer")
            )
        if _EMOJI_RE.search(footer):
            issues.append(
                LintIssue(
                    "FOOTER_EMOJI",
                    "avoid emoji in the footer — keep it plain text",
                    severity="warning",
                    field="footer",
                )
            )

    issues.extend(_lint_buttons(buttons or []))
    issues.extend(_lint_category_semantics(body or "", footer, category))
    issues.extend(_lint_authentication(category, body or "", buttons or []))
    return issues


def _lint_language(language: str) -> list[LintIssue]:
    lang = (language or "").strip()
    if not lang:
        return [LintIssue("LANG_REQUIRED", "language is required", field="language")]
    if not _LANG_RE.match(lang):
        return [
            LintIssue(
                "LANG_FORMAT",
                "language must be a Meta code like 'it', 'en' or 'en_US'",
                field="language",
            )
        ]
    if lang not in SUPPORTED_LANGUAGES:
        return [
            LintIssue(
                "LANG_UNSUPPORTED",
                f"'{lang}' is not a recognised WhatsApp language code — double-check it",
                severity="warning",
                field="language",
            )
        ]
    return []


def _lint_variables(body: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    nums = [int(n) for n in extract_variables(body)]
    if not nums:
        return issues
    if len(nums) > MAX_VARIABLES:
        issues.append(LintIssue("VAR_TOO_MANY", f"at most {MAX_VARIABLES} variables allowed"))
    # Must be 1..N sequential with no gaps.
    expected = list(range(1, len(nums) + 1))
    if sorted(nums) != expected:
        issues.append(
            LintIssue("VAR_NON_SEQUENTIAL", "variables must be 1..N sequential without gaps")
        )
    stripped = body.strip()
    if _VAR_RE.match(stripped):
        issues.append(LintIssue("VAR_AT_START", "body cannot start with a variable"))
    if re.search(r"\{\{\s*\d+\s*\}\}$", stripped):
        issues.append(LintIssue("VAR_AT_END", "body cannot end with a variable"))
    if _ADJACENT_VARS_RE.search(body):
        issues.append(LintIssue("VAR_ADJACENT", "variables must be separated by static text"))
    return issues


def _lint_body_format(body: str) -> list[LintIssue]:
    """Whitespace rules Meta hard-rejects as 'Invalid Format'."""
    issues: list[LintIssue] = []
    if "\t" in body:
        issues.append(LintIssue("BODY_TAB", "body cannot contain tab characters"))
    if re.search(r" {5,}", body):
        issues.append(LintIssue("BODY_SPACE_RUN", "body cannot contain more than 4 consecutive spaces"))
    if re.search(r"\n{5,}", body):
        issues.append(
            LintIssue("BODY_NEWLINE_RUN", "body cannot contain more than 4 consecutive newlines")
        )
    return issues


def _lint_examples(body: str, body_examples: list[str] | None) -> list[LintIssue]:
    """Warn when variables lack sample values (poor examples invite rejection).

    Only checked when `body_examples` is supplied — an omitted list means the
    caller fills generic placeholders downstream, which Meta accepts.
    """
    if body_examples is None:
        return []
    var_count = len(extract_variables(body))
    if var_count == 0:
        return []
    provided = sum(1 for e in body_examples if e and str(e).strip())
    if provided < var_count:
        return [
            LintIssue(
                "VAR_EXAMPLE_MISSING",
                f"add a realistic sample value for each of the {var_count} variables to reduce rejection risk",
                severity="warning",
            )
        ]
    return []


def _lint_category_semantics(body: str, footer: str | None, category: str) -> list[LintIssue]:
    """Advisory: promotional wording in a UTILITY template risks reclassification."""
    if category != "UTILITY":
        return []
    text = f"{body} {footer or ''}".lower()
    if any(kw in text for kw in _PROMO_KEYWORDS):
        return [
            LintIssue(
                "CAT_PROMO_IN_UTILITY",
                "promotional wording in a UTILITY template is often reclassified or rejected — consider MARKETING",
                severity="warning",
                field="category",
            )
        ]
    return []


def _lint_authentication(category: str, body: str, buttons: list[dict[str, Any]]) -> list[LintIssue]:
    """AUTHENTICATION templates follow Meta's rigid OTP format (no links/media)."""
    if category != "AUTHENTICATION":
        return []
    issues: list[LintIssue] = []
    if _URL_RE.search(body):
        issues.append(LintIssue("AUTH_NO_URL", "AUTHENTICATION templates cannot contain links"))
    if _EMOJI_RE.search(body):
        issues.append(
            LintIssue(
                "AUTH_NO_EMOJI",
                "AUTHENTICATION templates cannot contain emoji",
                severity="warning",
            )
        )
    for i, btn in enumerate(buttons):
        if str(btn.get("type", "")).upper() not in ("COPY_CODE", "OTP"):
            issues.append(
                LintIssue(
                    "AUTH_BUTTON_TYPE",
                    f"button[{i}]: AUTHENTICATION templates use a single copy-code button",
                    severity="warning",
                    field="buttons",
                )
            )
    return issues


def _lint_buttons(buttons: list[dict[str, Any]]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    if not buttons:
        return issues
    if len(buttons) > MAX_BUTTONS_TOTAL:
        issues.append(
            LintIssue("BUTTONS_TOO_MANY", f"at most {MAX_BUTTONS_TOTAL} buttons allowed", field="buttons")
        )
    counts: dict[str, int] = {}
    for i, btn in enumerate(buttons):
        btype = str(btn.get("type", "")).upper()
        if btype not in VALID_BUTTON_TYPES:
            issues.append(
                LintIssue("BUTTON_TYPE_INVALID", f"button[{i}] type {btype!r} invalid", field="buttons")
            )
            continue
        counts[btype] = counts.get(btype, 0) + 1
        text = str(btn.get("text", ""))
        if btype != "COPY_CODE" and not text.strip():
            issues.append(LintIssue("BUTTON_TEXT_REQUIRED", f"button[{i}] needs text", field="buttons"))
        if len(text) > MAX_BUTTON_TEXT_LEN:
            issues.append(
                LintIssue(
                    "BUTTON_TEXT_TOO_LONG",
                    f"button[{i}] text exceeds {MAX_BUTTON_TEXT_LEN} chars",
                    field="buttons",
                )
            )
        if btype == "URL":
            url = str(btn.get("url", ""))
            if not url:
                issues.append(LintIssue("BUTTON_URL_REQUIRED", f"button[{i}] needs url", field="buttons"))
            else:
                if not url.lower().startswith("https://"):
                    issues.append(
                        LintIssue(
                            "BUTTON_URL_NOT_HTTPS",
                            f"button[{i}] URL must start with https://",
                            field="buttons",
                        )
                    )
                if len(_VAR_RE.findall(url)) > 1:
                    issues.append(
                        LintIssue(
                            "BUTTON_URL_VARS",
                            f"button[{i}] URL allows at most one variable",
                            field="buttons",
                        )
                    )
        if btype == "PHONE_NUMBER":
            phone = str(btn.get("phone_number", "")).strip()
            if not phone:
                issues.append(
                    LintIssue("BUTTON_PHONE_REQUIRED", f"button[{i}] needs phone_number", field="buttons")
                )
            elif not phone.startswith("+") or len(phone) > MAX_PHONE_LEN:
                issues.append(
                    LintIssue(
                        "BUTTON_PHONE_FORMAT",
                        f"button[{i}] phone must be E.164 (+country…) and at most {MAX_PHONE_LEN} chars",
                        field="buttons",
                    )
                )
    if counts.get("URL", 0) > MAX_URL_BUTTONS:
        issues.append(
            LintIssue("BUTTONS_URL_TOO_MANY", f"at most {MAX_URL_BUTTONS} URL buttons", field="buttons")
        )
    if counts.get("PHONE_NUMBER", 0) > MAX_PHONE_BUTTONS:
        issues.append(
            LintIssue("BUTTONS_PHONE_TOO_MANY", f"at most {MAX_PHONE_BUTTONS} phone button", field="buttons")
        )
    if counts.get("COPY_CODE", 0) > MAX_COPY_CODE_BUTTONS:
        issues.append(
            LintIssue(
                "BUTTONS_COPY_TOO_MANY", f"at most {MAX_COPY_CODE_BUTTONS} copy-code button", field="buttons"
            )
        )
    return issues


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
    if btype == "COPY_CODE":
        # Meta copy-code buttons carry no label, just an example coupon/OTP code.
        return {"type": "COPY_CODE", "example": [str(btn.get("example") or btn.get("text") or "123456")]}
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
