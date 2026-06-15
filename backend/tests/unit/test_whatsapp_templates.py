"""Unit tests for the WhatsApp template engine — pure functions only (no DB/IO)."""

from integrations.whatsapp.templates import (
    build_send_components,
    build_submit_components,
    extract_variables,
    lint_template,
    resolve_body_params,
)


def test_extract_variables_ordered_and_deduped() -> None:
    assert extract_variables("Ciao {{1}}, ordine {{2}} ({{1}})") == ["1", "2"]
    assert extract_variables("nessuna variabile") == []
    assert extract_variables("{{ 3 }} e {{1}}") == ["3", "1"]


def test_lint_accepts_valid_template() -> None:
    errors = lint_template(
        body="Ciao {{1}}, il tuo ordine {{2}} è pronto.",
        category="UTILITY",
        footer="Grazie",
    )
    assert errors == []


def test_lint_rejects_non_sequential_variables() -> None:
    codes = {e.code for e in lint_template(body="Ciao {{1}} e {{3}} grazie")}
    assert "VAR_NON_SEQUENTIAL" in codes


def test_lint_rejects_variable_at_start_and_end() -> None:
    codes_start = {e.code for e in lint_template(body="{{1}} benvenuto da noi oggi")}
    assert "VAR_AT_START" in codes_start
    codes_end = {e.code for e in lint_template(body="Il tuo codice è {{1}}")}
    assert "VAR_AT_END" in codes_end


def test_lint_rejects_long_footer_and_footer_variable() -> None:
    codes = {e.code for e in lint_template(body="testo valido qui", footer="x" * 70)}
    assert "FOOTER_TOO_LONG" in codes
    codes2 = {e.code for e in lint_template(body="testo valido qui", footer="ciao {{1}}")}
    assert "FOOTER_HAS_VARIABLE" in codes2


def test_lint_rejects_bad_category_and_empty_body() -> None:
    codes = {e.code for e in lint_template(body="", category="PROMO")}
    assert "BODY_EMPTY" in codes
    assert "CATEGORY_INVALID" in codes


def test_lint_button_rules() -> None:
    too_many = lint_template(
        body="testo valido qui",
        buttons=[{"type": "QUICK_REPLY", "text": f"b{i}"} for i in range(4)],
    )
    assert "BUTTONS_TOO_MANY" in {e.code for e in too_many}

    bad_url = lint_template(
        body="testo valido qui",
        buttons=[{"type": "URL", "text": "vai", "url": "https://x/{{1}}/{{2}}"}],
    )
    assert "BUTTON_URL_VARS" in {e.code for e in bad_url}


def test_build_submit_components_includes_example_for_variables() -> None:
    comps = build_submit_components(
        body="Ciao {{1}}, ordine {{2}}",
        body_examples=["Mario"],
        footer="Grazie",
        header_type="TEXT",
        header_text="Conferma",
    )
    types = [c["type"] for c in comps]
    assert types == ["HEADER", "BODY", "FOOTER"]
    body = next(c for c in comps if c["type"] == "BODY")
    # One example per variable; missing example falls back to a placeholder.
    assert body["example"]["body_text"] == [["Mario", "esempio2"]]


def test_build_submit_components_image_header() -> None:
    comps = build_submit_components(
        body="testo statico", header_type="IMAGE", header_image_url="https://x/y.jpg"
    )
    header = next(c for c in comps if c["type"] == "HEADER")
    assert header["format"] == "IMAGE"
    assert header["example"]["header_handle"] == ["https://x/y.jpg"]


def test_build_send_components_body_params() -> None:
    comps = build_send_components(body_params=["Mario", "ORD-1"])
    assert comps == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": "Mario"},
                {"type": "text", "text": "ORD-1"},
            ],
        }
    ]
    assert build_send_components() == []


def test_resolve_body_params_maps_and_defaults_missing() -> None:
    params = resolve_body_params(
        variables=["1", "2", "3"],
        variable_mapping={"1": "lead.first_name", "2": "order.number"},
        context={"lead.first_name": "Mario", "order.number": "ORD-1"},
    )
    # slot 3 has no mapping → empty string, never crashes.
    assert params == ["Mario", "ORD-1", ""]


def test_resolve_body_params_is_positional_not_first_seen() -> None:
    # Body "{{2}} {{1}}" → extract_variables yields ["2", "1"]; params must still
    # be POSITIONAL (index 0 = {{1}}, index 1 = {{2}}), not swapped.
    params = resolve_body_params(
        variables=["2", "1"],
        variable_mapping={"1": "a", "2": "b"},
        context={"a": "first", "b": "second"},
    )
    assert params == ["first", "second"]


def test_lint_rejects_image_header_in_v1() -> None:
    codes = {e.code for e in lint_template(body="testo valido qui", header_type="IMAGE")}
    assert "HEADER_IMAGE_UNSUPPORTED" in codes
