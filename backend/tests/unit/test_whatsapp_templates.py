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


def test_lint_rejects_adjacent_variables() -> None:
    # Two placeholders with only whitespace between them → Meta "Invalid Format".
    codes = {e.code for e in lint_template(body="Ciao {{1}} {{2}} a presto")}
    assert "VAR_ADJACENT" in codes
    glued = {e.code for e in lint_template(body="Ciao {{1}}{{2}} a presto")}
    assert "VAR_ADJACENT" in glued
    # Static text between variables is fine.
    ok = {e.code for e in lint_template(body="Ciao {{1}}, il tuo ordine {{2}} è pronto.")}
    assert "VAR_ADJACENT" not in ok


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
    # Meta allows up to 10 buttons total — 4 quick replies is fine now.
    ok = lint_template(
        body="testo valido qui",
        buttons=[{"type": "QUICK_REPLY", "text": f"b{i}"} for i in range(4)],
    )
    assert "BUTTONS_TOO_MANY" not in {e.code for e in ok}

    too_many = lint_template(
        body="testo valido qui",
        buttons=[{"type": "QUICK_REPLY", "text": f"b{i}"} for i in range(11)],
    )
    assert "BUTTONS_TOO_MANY" in {e.code for e in too_many}

    bad_url = lint_template(
        body="testo valido qui",
        buttons=[{"type": "URL", "text": "vai", "url": "https://x/{{1}}/{{2}}"}],
    )
    assert "BUTTON_URL_VARS" in {e.code for e in bad_url}


def test_lint_button_subtype_caps_and_format() -> None:
    codes = {
        e.code
        for e in lint_template(
            body="testo valido qui",
            buttons=[
                {"type": "URL", "text": "a", "url": "https://x.it"},
                {"type": "URL", "text": "b", "url": "https://y.it"},
                {"type": "URL", "text": "c", "url": "https://z.it"},  # 3rd URL → too many
                {"type": "PHONE_NUMBER", "text": "p1", "phone_number": "+39055123"},
                {"type": "PHONE_NUMBER", "text": "p2", "phone_number": "+39055124"},  # 2nd phone
            ],
        )
    }
    assert "BUTTONS_URL_TOO_MANY" in codes
    assert "BUTTONS_PHONE_TOO_MANY" in codes

    http_url = {
        e.code
        for e in lint_template(
            body="testo valido qui",
            buttons=[{"type": "URL", "text": "vai", "url": "http://insecure.it"}],
        )
    }
    assert "BUTTON_URL_NOT_HTTPS" in http_url

    bad_phone = {
        e.code
        for e in lint_template(
            body="testo valido qui",
            buttons=[{"type": "PHONE_NUMBER", "text": "chiama", "phone_number": "055123456"}],
        )
    }
    assert "BUTTON_PHONE_FORMAT" in bad_phone


def test_lint_language_format_and_unsupported() -> None:
    bad = {e.code for e in lint_template(body="testo valido qui", language="italiano")}
    assert "LANG_FORMAT" in bad

    valid = lint_template(body="testo valido qui", language="en_US")
    assert valid == []

    unknown = lint_template(body="testo valido qui", language="xx")
    assert any(e.code == "LANG_UNSUPPORTED" and e.severity == "warning" for e in unknown)


def test_lint_body_format_tabs_and_spaces_are_errors() -> None:
    codes = {e.code for e in lint_template(body="ciao\tmondo come va oggi")}
    assert "BODY_TAB" in codes
    runs = {e.code for e in lint_template(body="ciao      mondo come va")}
    assert "BODY_SPACE_RUN" in runs


def test_lint_example_count_is_warning_when_provided_short() -> None:
    issues = lint_template(body="Ciao {{1}}, ordine {{2}} pronto", body_examples=["Mario"])
    miss = [e for e in issues if e.code == "VAR_EXAMPLE_MISSING"]
    assert miss and miss[0].severity == "warning"
    # Omitting examples entirely is fine (caller fills generic placeholders).
    assert "VAR_EXAMPLE_MISSING" not in {
        e.code for e in lint_template(body="Ciao {{1}}, ordine {{2}} pronto")
    }


def test_lint_promo_wording_in_utility_is_warning() -> None:
    issues = lint_template(
        body="Ciao {{1}}, approfitta dello sconto del 20% solo oggi grazie",
        category="UTILITY",
    )
    promo = [e for e in issues if e.code == "CAT_PROMO_IN_UTILITY"]
    assert promo and promo[0].severity == "warning"
    # Same wording under MARKETING is not flagged.
    assert "CAT_PROMO_IN_UTILITY" not in {
        e.code
        for e in lint_template(
            body="Ciao {{1}}, approfitta dello sconto del 20% solo oggi grazie",
            category="MARKETING",
        )
    }


def test_lint_authentication_rejects_links() -> None:
    codes = {
        e.code
        for e in lint_template(
            body="Il tuo codice {{1}} su https://acme.it grazie",
            category="AUTHENTICATION",
        )
    }
    assert "AUTH_NO_URL" in codes


def test_lint_severity_split() -> None:
    # A clean template has no errors and no warnings.
    issues = lint_template(body="Ciao {{1}}, il tuo ordine {{2}} è pronto.", footer="Grazie")
    assert [e for e in issues if e.severity == "error"] == []
    assert [e for e in issues if e.severity == "warning"] == []


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
