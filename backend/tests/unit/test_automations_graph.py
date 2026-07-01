"""Unit tests for the automation graph logic — pure functions only (no DB/IO)."""

from ai_core.automations import (
    _ATOMIC_CONDITION_TYPES,  # type: ignore[attr-defined]
    evaluate_condition,
    outgoing_targets,
    resolve_send_node_at,
    resolve_send_plan,
    system_flow_timing_errors,
    validate_graph,
    wait_minutes,
)


def _trigger(key: str = "t", type: str = "message_received") -> dict:
    return {"node_key": key, "kind": "trigger", "type": type, "config": {}}


def _action(key: str, text: str = "ciao") -> dict:
    return {"node_key": key, "kind": "action", "type": "send_message", "config": {"text": text}}


def test_validate_minimal_valid_graph() -> None:
    nodes = [_trigger(), _action("a")]
    edges = [{"source_key": "t", "target_key": "a", "branch": "default"}]
    result = validate_graph(nodes, edges)
    assert result.ok
    assert result.trigger_type == "message_received"


def test_validate_requires_exactly_one_trigger() -> None:
    assert "a flow needs exactly one trigger node" in validate_graph([_action("a")], []).errors
    two = validate_graph([_trigger("t1"), _trigger("t2")], []).errors
    assert "a flow can have only one trigger node" in two


def test_validate_rejects_unknown_types() -> None:
    bad = validate_graph(
        [{"node_key": "t", "kind": "trigger", "type": "telepathy", "config": {}}], []
    )
    assert any("invalid trigger type" in e for e in bad.errors)


def test_validate_rejects_duplicate_keys() -> None:
    nodes = [_trigger("x"), _action("x")]
    assert "node keys must be unique" in validate_graph(nodes, []).errors


def test_validate_rejects_dangling_edge() -> None:
    nodes = [_trigger(), _action("a")]
    edges = [{"source_key": "t", "target_key": "ghost", "branch": "default"}]
    assert any("missing node" in e for e in validate_graph(nodes, edges).errors)


def test_validate_rejects_incoming_to_trigger() -> None:
    nodes = [_trigger(), _action("a")]
    edges = [{"source_key": "a", "target_key": "t", "branch": "default"}]
    assert (
        "the trigger node cannot have incoming connections" in validate_graph(nodes, edges).errors
    )


def test_validate_rejects_cycle() -> None:
    nodes = [_trigger(), _action("a"), _action("b")]
    edges = [
        {"source_key": "t", "target_key": "a", "branch": "default"},
        {"source_key": "a", "target_key": "b", "branch": "default"},
        {"source_key": "b", "target_key": "a", "branch": "default"},
    ]
    assert any("loop" in e for e in validate_graph(nodes, edges).errors)


def test_validate_action_config_required() -> None:
    nodes = [
        _trigger(),
        {"node_key": "a", "kind": "action", "type": "send_template", "config": {}},
    ]
    assert any("send_template needs a template" in e for e in validate_graph(nodes, []).errors)

    waiting = [
        _trigger(),
        {"node_key": "w", "kind": "action", "type": "wait", "config": {"minutes": 0}},
    ]
    assert any("wait needs minutes > 0" in e for e in validate_graph(waiting, []).errors)


def test_validate_allows_branch_edges_from_condition() -> None:
    nodes = [
        _trigger(),
        {
            "node_key": "c",
            "kind": "condition",
            "type": "lead_score",
            "config": {"op": ">=", "value": 80},
        },
        _action("hot"),
        _action("cold"),
    ]
    edges = [
        {"source_key": "t", "target_key": "c", "branch": "default"},
        {"source_key": "c", "target_key": "hot", "branch": "true"},
        {"source_key": "c", "target_key": "cold", "branch": "false"},
    ]
    assert validate_graph(nodes, edges).ok


def test_evaluate_conditions() -> None:
    assert evaluate_condition("lead_score", {"op": ">=", "value": 80}, {"score": 90})
    assert not evaluate_condition("lead_score", {"op": ">=", "value": 80}, {"score": 50})
    assert evaluate_condition(
        "lead_temperature", {"op": "==", "value": "hot"}, {"temperature": "hot"}
    )
    assert evaluate_condition("within_24h_window", {}, {"within_24h_window": True})
    assert evaluate_condition(
        "message_contains", {"keywords": ["Prezzo"]}, {"last_message": "quanto è il PREZZO?"}
    )
    assert evaluate_condition(
        "time_of_day", {"from": "09:00", "to": "18:00"}, {"minutes_of_day": 600}
    )
    # Overnight window 22:00→06:00 includes 23:00 (1380) but not 12:00 (720).
    assert evaluate_condition(
        "time_of_day", {"from": "22:00", "to": "06:00"}, {"minutes_of_day": 1380}
    )
    assert not evaluate_condition(
        "time_of_day", {"from": "22:00", "to": "06:00"}, {"minutes_of_day": 720}
    )
    # Unknown condition type fails closed.
    assert not evaluate_condition("astrology", {}, {})


def test_condition_group_and_or() -> None:
    ctx = {"score": 90, "within_24h_window": False}
    and_cfg = {
        "operator": "and",
        "clauses": [
            {"type": "lead_score", "op": ">=", "value": 80},
            {"type": "within_24h_window"},
        ],
    }
    # AND with one false clause → False.
    assert not evaluate_condition("condition_group", and_cfg, ctx)
    or_cfg = {**and_cfg, "operator": "or"}
    # OR with one true clause → True.
    assert evaluate_condition("condition_group", or_cfg, ctx)
    # Empty clauses → fail closed.
    assert not evaluate_condition("condition_group", {"operator": "and", "clauses": []}, ctx)


def test_condition_group_negate() -> None:
    ctx = {"score": 50}
    cfg = {
        "operator": "and",
        "clauses": [{"type": "lead_score", "op": ">=", "value": 80, "negate": True}],
    }
    # score 50 is NOT >= 80, negate flips the False result to True.
    assert evaluate_condition("condition_group", cfg, ctx)


def test_condition_group_unknown_clause_fails_closed() -> None:
    ctx = {"score": 90}
    cfg = {
        "operator": "and",
        "clauses": [
            {"type": "lead_score", "op": ">=", "value": 80},
            {"type": "condition_group", "clauses": []},  # nesting not allowed → False
        ],
    }
    assert not evaluate_condition("condition_group", cfg, ctx)


def test_condition_group_reuses_atomic() -> None:
    ctx = {"score": 90, "within_24h_window": True}
    # A group of two clauses must match calling evaluate_condition on each atomic type.
    assert evaluate_condition("lead_score", {"op": ">=", "value": 80}, ctx)
    assert evaluate_condition("within_24h_window", {}, ctx)
    cfg = {
        "operator": "and",
        "clauses": [
            {"type": "lead_score", "op": ">=", "value": 80},
            {"type": "within_24h_window"},
        ],
    }
    assert evaluate_condition("condition_group", cfg, ctx)


def test_validate_condition_group_config() -> None:
    def _group(**cfg: object) -> dict:
        return {"node_key": "c", "kind": "condition", "type": "condition_group", "config": cfg}

    bad_op = validate_graph(
        [_trigger(), _group(operator="xor", clauses=[{"type": "lead_score"}])], []
    )
    assert any("operator must be" in e for e in bad_op.errors)
    empty = validate_graph([_trigger(), _group(operator="and", clauses=[])], [])
    assert any("at least one clause" in e for e in empty.errors)
    bad_clause = validate_graph(
        [_trigger(), _group(operator="and", clauses=[{"type": "telepathy"}])], []
    )
    assert any("invalid type" in e for e in bad_clause.errors)
    ok = validate_graph(
        [
            _trigger(),
            _group(operator="or", clauses=[{"type": "lead_score", "op": ">=", "value": 80}]),
        ],
        [],
    )
    assert ok.ok


def test_validate_ai_check_config() -> None:
    def _ai_check(**cfg: object) -> dict:
        return {"node_key": "c", "kind": "condition", "type": "ai_check", "config": cfg}

    # Missing prompt → error.
    missing = validate_graph([_trigger(), _ai_check()], [])
    assert any("ai_check needs a prompt" in e for e in missing.errors)

    # Empty prompt → error.
    empty = validate_graph([_trigger(), _ai_check(prompt="  ")], [])
    assert any("ai_check needs a prompt" in e for e in empty.errors)

    # Valid prompt → accepted.
    ok = validate_graph([_trigger(), _ai_check(prompt="Il lead ha chiesto il prezzo?")], [])
    assert ok.ok

    # ai_check stays out of the *sync* atomic set (scheduler path can't run it)…
    assert "ai_check" not in _ATOMIC_CONDITION_TYPES
    # …but it IS a valid clause inside condition_group (evaluated async by the engine).

    def _group_with_ai(**clause: object) -> dict:
        return {
            "node_key": "g",
            "kind": "condition",
            "type": "condition_group",
            "config": {"operator": "and", "clauses": [{"type": "ai_check", **clause}]},
        }

    # ai_check clause with a prompt → accepted.
    ok_clause = validate_graph([_trigger(), _group_with_ai(prompt="Ha chiesto il prezzo?")], [])
    assert ok_clause.ok

    # ai_check clause without a prompt → error.
    bad_clause = validate_graph([_trigger(), _group_with_ai()], [])
    assert any("ai_check clause needs a prompt" in e for e in bad_clause.errors)

    # A genuinely unknown clause type is still rejected.
    unknown_clause = validate_graph(
        [
            _trigger(),
            {
                "node_key": "g",
                "kind": "condition",
                "type": "condition_group",
                "config": {"operator": "and", "clauses": [{"type": "telepathy"}]},
            },
        ],
        [],
    )
    assert any("invalid type" in e for e in unknown_clause.errors)


def test_validate_ai_reply_config() -> None:
    def _ai(**cfg: object) -> dict:
        return {"node_key": "a", "kind": "action", "type": "ai_reply", "config": cfg}

    missing_obj = validate_graph([_trigger(), _ai(window_policy="auto")], [])
    assert any("ai_reply needs an objective" in e for e in missing_obj.errors)
    bad_policy = validate_graph(
        [_trigger(), _ai(objective="recupera", window_policy="whenever")], []
    )
    assert any("invalid window_policy" in e for e in bad_policy.errors)
    needs_tpl = validate_graph(
        [_trigger(), _ai(objective="recupera", window_policy="require_template")], []
    )
    assert any("needs a fallback template" in e for e in needs_tpl.errors)
    bad_action = validate_graph(
        [_trigger(), _ai(objective="recupera", allowed_actions=["fly"])], []
    )
    assert any("invalid allowed_actions" in e for e in bad_action.errors)
    ok = validate_graph(
        [_trigger(), _ai(objective="recupera", allowed_actions=["update_score"])], []
    )
    assert ok.ok


def test_validate_set_lead_field_and_handoff_config() -> None:
    def _slf(**cfg: object) -> dict:
        return {"node_key": "a", "kind": "action", "type": "set_lead_field", "config": cfg}

    assert any(
        "invalid field" in e for e in validate_graph([_trigger(), _slf(field="laser")], []).errors
    )
    needs_key = validate_graph([_trigger(), _slf(field="custom_field")], [])
    assert any("needs a key" in e for e in needs_key.errors)
    bad_delta = validate_graph([_trigger(), _slf(field="score_delta", value="lots")], [])
    assert any("integer value" in e for e in bad_delta.errors)
    assert validate_graph([_trigger(), _slf(field="score_delta", value=10)], []).ok
    # human_handoff needs no config — a bare node is valid.
    handoff = validate_graph(
        [_trigger(), {"node_key": "h", "kind": "action", "type": "human_handoff", "config": {}}],
        [],
    )
    assert handoff.ok


def test_outgoing_targets_branch_filter() -> None:
    edges = [
        {"source_key": "c", "target_key": "hot", "branch": "true"},
        {"source_key": "c", "target_key": "cold", "branch": "false"},
        {"source_key": "t", "target_key": "c", "branch": "default"},
    ]
    assert outgoing_targets(edges, "c", branch="true") == ["hot"]
    assert outgoing_targets(edges, "c", branch="false") == ["cold"]
    assert outgoing_targets(edges, "t") == ["c"]


def _send(key: str, **cfg: object) -> dict:
    return {"node_key": key, "kind": "action", "type": "send", "config": cfg}


def test_validate_send_action_config() -> None:
    ok = validate_graph([_trigger(), _send("s", window_policy="auto", free_text="ciao")], [])
    assert not any("send" in e for e in ok.errors)
    bad = validate_graph([_trigger(), _send("s", window_policy="require_template")], [])
    assert any("require_template needs a template" in e for e in bad.errors)
    invalid_policy = validate_graph([_trigger(), _send("s", window_policy="whenever")], [])
    assert any("invalid window_policy" in e for e in invalid_policy.errors)


def test_resolve_send_node_at_linear_chain() -> None:
    nodes = [_trigger(), _send("s0", free_text="primo"), _send("s1", free_text="secondo")]
    edges = [
        {"source_key": "t", "target_key": "s0", "branch": "default"},
        {"source_key": "s0", "target_key": "s1", "branch": "default"},
    ]
    assert resolve_send_node_at(nodes, edges, attempt_index=0, context={})["free_text"] == "primo"
    assert resolve_send_node_at(nodes, edges, attempt_index=1, context={})["free_text"] == "secondo"
    # Path shorter than the requested attempt → None (scheduler falls back).
    assert resolve_send_node_at(nodes, edges, attempt_index=2, context={}) is None


def test_resolve_send_node_at_skips_wait() -> None:
    nodes = [
        _trigger(),
        {"node_key": "w", "kind": "action", "type": "wait", "config": {"minutes": 30}},
        _send("s0", free_text="dopo-attesa"),
    ]
    edges = [
        {"source_key": "t", "target_key": "w", "branch": "default"},
        {"source_key": "w", "target_key": "s0", "branch": "default"},
    ]
    # wait is traversed but not counted: the send is still attempt 0.
    assert (
        resolve_send_node_at(nodes, edges, attempt_index=0, context={})["free_text"]
        == "dopo-attesa"
    )


def test_resolve_send_node_at_follows_condition_branch() -> None:
    nodes = [
        _trigger(),
        {"node_key": "c", "kind": "condition", "type": "within_24h_window", "config": {}},
        _send("yes", free_text="dentro-finestra"),
        _send("no", free_text="fuori-finestra"),
    ]
    edges = [
        {"source_key": "t", "target_key": "c", "branch": "default"},
        {"source_key": "c", "target_key": "yes", "branch": "true"},
        {"source_key": "c", "target_key": "no", "branch": "false"},
    ]
    open_window = resolve_send_node_at(
        nodes, edges, attempt_index=0, context={"within_24h_window": True}
    )
    assert open_window["free_text"] == "dentro-finestra"
    closed = resolve_send_node_at(
        nodes, edges, attempt_index=0, context={"within_24h_window": False}
    )
    assert closed["free_text"] == "fuori-finestra"
    # Missing score data → lead_score condition fails closed (false branch).
    score_nodes = [
        _trigger(),
        {
            "node_key": "c",
            "kind": "condition",
            "type": "lead_score",
            "config": {"op": ">=", "value": 80},
        },
        _send("hot", free_text="caldo"),
        _send("cold", free_text="freddo"),
    ]
    score_edges = [
        {"source_key": "t", "target_key": "c", "branch": "default"},
        {"source_key": "c", "target_key": "hot", "branch": "true"},
        {"source_key": "c", "target_key": "cold", "branch": "false"},
    ]
    assert (
        resolve_send_node_at(score_nodes, score_edges, attempt_index=0, context={})["free_text"]
        == "freddo"
    )


# --------------------------------------------------------------------------- #
# wait normalisation + new block validation (D2/D3)
# --------------------------------------------------------------------------- #


def _wait(key: str, minutes: int, unit: str | None = None) -> dict:
    cfg: dict = {"minutes": minutes}
    if unit is not None:
        cfg["unit"] = unit
    return {"node_key": key, "kind": "action", "type": "wait", "config": cfg}


def test_wait_minutes_units() -> None:
    assert wait_minutes({"minutes": 30}) == 30  # legacy node, no unit
    assert wait_minutes({"minutes": 30, "unit": "minutes"}) == 30
    assert wait_minutes({"minutes": 2, "unit": "hours"}) == 120
    assert wait_minutes({"minutes": 7, "unit": "days"}) == 7 * 1440
    assert wait_minutes({"minutes": "x"}) == 0  # non-numeric → 0
    assert wait_minutes({"minutes": 5, "unit": "weeks"}) == 5  # unknown unit -> x1


def test_validate_wait_unit() -> None:
    bad = validate_graph([_trigger(), _wait("w", 30, unit="fortnights")], [])
    assert any("wait has an invalid unit" in e for e in bad.errors)
    ok = validate_graph([_trigger(), _wait("w", 7, unit="days")], [])
    assert ok.ok


def test_validate_wait_until_before() -> None:
    def _wub(**cfg: object) -> dict:
        return {"node_key": "w", "kind": "action", "type": "wait_until_before", "config": cfg}

    assert any(
        "hours between 1 and 168" in e
        for e in validate_graph([_trigger(), _wub(hours=0)], []).errors
    )
    assert any(
        "hours between 1 and 168" in e
        for e in validate_graph([_trigger(), _wub(hours=200)], []).errors
    )
    assert any(
        "anchor" in e
        for e in validate_graph([_trigger(), _wub(hours=24, anchor="lead.created")], []).errors
    )
    assert validate_graph([_trigger(), _wub(hours=24)], []).ok


# --------------------------------------------------------------------------- #
# resolve_send_plan (AR1) — timing accumulated from the graph
# --------------------------------------------------------------------------- #


def _chain(*pairs: tuple[str, str]) -> list[dict]:
    return [{"source_key": s, "target_key": t, "branch": "default"} for s, t in pairs]


def test_resolve_send_plan_accumulates_waits() -> None:
    nodes = [
        _trigger("t", "no_answer"),
        _wait("w0", 35),
        _send("s0", free_text="primo"),
        _wait("w1", 1, unit="days"),
        _send("s1", free_text="secondo"),
    ]
    edges = _chain(("t", "w0"), ("w0", "s0"), ("s0", "w1"), ("w1", "s1"))
    plan = resolve_send_plan(nodes, edges, context={})
    assert plan.max_attempts == 2
    assert plan.sends[0].attempt_index == 0
    assert plan.sends[0].delay_minutes == 35  # leading wait before first send
    assert plan.sends[0].config["free_text"] == "primo"
    assert plan.sends[1].delay_minutes == 1440  # 1 day between sends
    assert plan.sends[1].anchor_hours_before is None


def test_resolve_send_plan_anchor_hours() -> None:
    nodes = [
        _trigger("t", "booking_created"),
        {"node_key": "w", "kind": "action", "type": "wait_until_before", "config": {"hours": 24}},
        _send("s0", free_text="promemoria"),
    ]
    edges = _chain(("t", "w"), ("w", "s0"))
    plan = resolve_send_plan(nodes, edges, context={})
    assert plan.max_attempts == 1
    assert plan.sends[0].anchor_hours_before == 24
    assert plan.sends[0].delay_minutes == 0


def test_resolve_send_plan_no_sends() -> None:
    nodes = [_trigger("t", "no_answer"), _wait("w", 30)]
    edges = _chain(("t", "w"))
    assert resolve_send_plan(nodes, edges, context={}).max_attempts == 0


def test_resolve_send_plan_follows_condition() -> None:
    nodes = [
        _trigger("t", "lead_dormant"),
        {
            "node_key": "c",
            "kind": "condition",
            "type": "lead_score",
            "config": {"op": ">=", "value": 80},
        },
        _send("hot", free_text="caldo"),
        _send("cold", free_text="freddo"),
    ]
    edges = [
        {"source_key": "t", "target_key": "c", "branch": "default"},
        {"source_key": "c", "target_key": "hot", "branch": "true"},
        {"source_key": "c", "target_key": "cold", "branch": "false"},
    ]
    hot = resolve_send_plan(nodes, edges, context={"score": 90})
    assert hot.sends[0].config["free_text"] == "caldo"
    cold = resolve_send_plan(nodes, edges, context={"score": 10})
    assert cold.sends[0].config["free_text"] == "freddo"


# --------------------------------------------------------------------------- #
# system_flow_timing_errors (D6) — compliance bounds on system flows
# --------------------------------------------------------------------------- #


def test_system_flow_timing_no_answer() -> None:
    # delay out of range + too many sends.
    nodes = [
        {"node_key": "t", "kind": "trigger", "type": "no_answer", "config": {"delay_minutes": 5}},
        _send("s0"),
        _send("s1"),
        _send("s2"),
        _send("s3"),
        _send("s4"),
    ]
    errors = system_flow_timing_errors("no_answer", nodes, [])
    assert any("delay_minutes" in e for e in errors)
    assert any("al massimo 4 invii" in e for e in errors)
    # A valid no_answer flow: delay 35, one wait of 1440 between two sends.
    ok = [
        {"node_key": "t", "kind": "trigger", "type": "no_answer", "config": {"delay_minutes": 35}},
        _send("s0"),
        _wait("w", 1, unit="days"),
        _send("s1"),
    ]
    assert system_flow_timing_errors("no_answer", ok, []) == []


def test_system_flow_timing_reactivation() -> None:
    nodes = [
        {"node_key": "t", "kind": "trigger", "type": "lead_dormant", "config": {"days": 10}},
        _send("s0"),
        _wait("w", 1, unit="hours"),  # 1h is well below the 3-day floor
        _send("s1"),
    ]
    errors = system_flow_timing_errors("reactivation", nodes, [])
    assert any("dormienza" in e for e in errors)
    assert any("3 e 30 giorni" in e for e in errors)


def test_default_system_graphs_are_enableable() -> None:
    """Fix #1 / ADR 0011: a freshly-seeded system flow must be valid, within the
    compliance ranges (so it can be enabled), and mirror the config default count."""
    from db.repositories.automation import _DEFAULT_SYSTEM_GRAPH  # type: ignore[attr-defined]

    expected_sends = {"no_answer": 2, "reactivation": 3, "booking_reminder": 1, "first_contact": 1}
    for key, spec in _DEFAULT_SYSTEM_GRAPH.items():
        nodes: list[dict] = []
        edges: list[dict] = []
        prev: str | None = None
        for i, (kind, ntype, config) in enumerate(spec):
            node_key = "t" if kind == "trigger" else f"n{i}"
            nodes.append({"node_key": node_key, "kind": kind, "type": ntype, "config": config})
            if prev is not None:
                edges.append({"source_key": prev, "target_key": node_key, "branch": "default"})
            prev = node_key
        assert validate_graph(nodes, edges).ok, f"{key} graph invalid"
        assert system_flow_timing_errors(key, nodes, edges) == [], f"{key} out of range"
        assert resolve_send_plan(nodes, edges, context={}).max_attempts == expected_sends[key], key


def test_system_flow_timing_booking() -> None:
    # relative wait not allowed for booking; hours out of range.
    nodes = [
        {"node_key": "t", "kind": "trigger", "type": "booking_created", "config": {}},
        {"node_key": "wb", "kind": "action", "type": "wait_until_before", "config": {"hours": 300}},
        _wait("w", 30),
        _send("s0"),
    ]
    errors = system_flow_timing_errors("booking_reminder", nodes, [])
    assert any("attese relative" in e for e in errors)
    assert any("1 e 168" in e for e in errors)
    ok = [
        {"node_key": "t", "kind": "trigger", "type": "booking_created", "config": {}},
        {"node_key": "wb", "kind": "action", "type": "wait_until_before", "config": {"hours": 24}},
        _send("s0"),
    ]
    assert system_flow_timing_errors("booking_reminder", ok, []) == []
