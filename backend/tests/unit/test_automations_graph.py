"""Unit tests for the automation graph logic — pure functions only (no DB/IO)."""

from ai_core.automations import (
    evaluate_condition,
    outgoing_targets,
    resolve_send_node_at,
    validate_graph,
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
