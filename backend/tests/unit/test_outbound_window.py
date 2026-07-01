"""Unit tests for the 24h-window outbound dispatcher (compliance core)."""

from datetime import UTC, datetime, timedelta

from workers.outbound import (
    MODE_SKIP,
    MODE_TEMPLATE,
    MODE_TEXT,
    decide_outbound,
    is_within_24h,
    render_free_text,
)

from db import ResolvedFlowStep

NOW = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


def _approved_step(**over: object) -> ResolvedFlowStep:
    base: dict[str, object] = {
        "flow_enabled": True,
        "step_enabled": True,
        "window_policy": "auto",
        "free_text": None,
        "variable_mapping": {"1": "contact.phone"},
        "template_name": "reloop_reactivation_x",
        "template_language": "it",
        "template_variables": ["1"],
        "template_approved": True,
    }
    base.update(over)
    return ResolvedFlowStep(**base)  # type: ignore[arg-type]


def test_is_within_24h() -> None:
    assert is_within_24h(NOW - timedelta(hours=1), NOW) is True
    assert is_within_24h(NOW - timedelta(hours=25), NOW) is False
    assert is_within_24h(None, NOW) is False


def test_no_flow_inside_window_sends_text() -> None:
    d = decide_outbound(within_window=True, fallback_text="ciao", step=None)
    assert d.mode == MODE_TEXT
    assert d.text == "ciao"


def test_no_flow_outside_window_skips_never_freeform() -> None:
    # The key compliance fix: outside the window with no template → skip, never text.
    d = decide_outbound(within_window=False, fallback_text="ciao", step=None)
    assert d.mode == MODE_SKIP
    assert d.reason == "no_template_outside_window"


def test_auto_outside_window_with_approved_template_sends_template() -> None:
    d = decide_outbound(
        within_window=False,
        fallback_text="ciao",
        step=_approved_step(),
        context={"contact.phone": "39333"},
    )
    assert d.mode == MODE_TEMPLATE
    assert d.template_name == "reloop_reactivation_x"
    assert d.components == [{"type": "body", "parameters": [{"type": "text", "text": "39333"}]}]


def test_require_template_without_approval_skips() -> None:
    d = decide_outbound(
        within_window=True,
        fallback_text="ciao",
        step=_approved_step(window_policy="require_template", template_approved=False),
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "no_approved_template"


def test_freeform_only_outside_window_skips() -> None:
    d = decide_outbound(
        within_window=False,
        fallback_text="ciao",
        step=_approved_step(window_policy="freeform_only"),
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "outside_window_freeform_only"


def test_disabled_flow_skips() -> None:
    d = decide_outbound(
        within_window=True, fallback_text="ciao", step=_approved_step(flow_enabled=False)
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "flow_disabled"


def test_template_with_unmapped_variables_skips_instead_of_broken_send() -> None:
    # Template declares {{1}} but the step has no mapping → params would resolve
    # to "" and Meta would reject the send; we skip instead.
    d = decide_outbound(
        within_window=False,
        fallback_text="ciao",
        step=_approved_step(variable_mapping={}),
        context={},
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "incomplete_template_mapping"


def test_step_free_text_overrides_fallback_inside_window() -> None:
    d = decide_outbound(
        within_window=True,
        fallback_text="builtin",
        step=_approved_step(free_text="custom step copy"),
    )
    assert d.mode == MODE_TEXT
    assert d.text == "custom step copy"


def test_render_free_text_placeholders() -> None:
    ctx = {"contact.name": "Mario Rossi", "appointment.datetime": "12/04 alle 10:00"}
    assert render_free_text("Ciao {name}", ctx) == "Ciao Mario Rossi"
    assert render_free_text("Ciao {first_name}", ctx) == "Ciao Mario"
    assert (
        render_free_text("Promemoria: {{appointment.datetime}}", ctx)
        == "Promemoria: 12/04 alle 10:00"
    )
    # Unknown {{key}} → "" (never left as raw braces); stray single braces untouched.
    assert render_free_text("x {{unknown.key}} {y}", ctx) == "x  {y}"
    assert render_free_text("", ctx) == ""


def test_decide_outbound_renders_free_text_placeholders() -> None:
    # D4: free text (MODE_TEXT) now resolves placeholders from the template context,
    # not only templates — {name} / {{appointment.datetime}} no longer go out raw.
    d = decide_outbound(
        within_window=True,
        fallback_text="",
        step=_approved_step(free_text="Ciao {name}, promemoria {{appointment.datetime}}"),
        context={"contact.name": "Anna", "appointment.datetime": "10:00"},
    )
    assert d.mode == MODE_TEXT
    assert d.text == "Ciao Anna, promemoria 10:00"
