"""Automation graph logic — validation, condition evaluation, traversal.

Pure functions shared by the API (`routers/automations.py` validates a graph on
save and derives `trigger_type`) and the worker engine (`workers/automation/`
walks the graph and evaluates condition nodes at run time). No IO, so cheap to
unit-test.

A graph is `nodes` + `edges` as plain dicts:
    node = {"node_key", "kind", "type", "config", "position_x", "position_y"}
    edge = {"source_key", "target_key", "branch"}  # branch: default|true|false
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from db.models.automation import ACTION_TYPES, CONDITION_TYPES, NODE_KINDS, TRIGGER_TYPES

_VALID_TYPES: dict[str, set[str]] = {
    "trigger": set(TRIGGER_TYPES),
    "condition": set(CONDITION_TYPES),
    "action": set(ACTION_TYPES),
}

# Atomic conditions a `condition_group` clause may reference.
# Excludes `condition_group` (no nesting) and `ai_check` (async — not evaluable inline).
_ATOMIC_CONDITION_TYPES: frozenset[str] = frozenset(
    t for t in CONDITION_TYPES if t not in ("condition_group", "ai_check")
)

# ActionKinds an `ai_reply` node may let the AI dispatch (mirrors the orchestrator
# ActionKind set minus "none"). Kept here so graph validation stays IO-free.
AI_REPLY_DISPATCHABLE_ACTIONS: frozenset[str] = frozenset(
    {
        "propose_slots",
        "book_slot",
        "reschedule_slot",
        "cancel_slot",
        "move_pipeline",
        "update_score",
        "escalate_human",
    }
)


@dataclass(slots=True)
class GraphValidation:
    errors: list[str] = field(default_factory=list)
    trigger_type: str | None = None
    trigger_config: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _in_range(value: Any, lo: int, hi: int) -> bool:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def _opt_int(value: Any) -> int | None:
    """int(value) or None when it isn't int-convertible (no raise)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# A `wait` node's delay unit → minutes multiplier. `unit` is optional on the node
# (default "minutes"); legacy nodes carry only {"minutes": N}.
_WAIT_UNIT_FACTORS: dict[str, int] = {"minutes": 1, "hours": 60, "days": 1440}


def wait_minutes(config: dict[str, Any]) -> int:
    """Normalise a `wait` node's delay to minutes (0 on a non-numeric value)."""
    try:
        value = int(config.get("minutes", 0))
    except (TypeError, ValueError):
        return 0
    unit = str(config.get("unit", "minutes"))
    return max(0, value) * _WAIT_UNIT_FACTORS.get(unit, 1)


def validate_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> GraphValidation:
    """Validate an automation graph; return errors + the derived trigger.

    Enforces: unique node keys, valid kind/type, exactly one trigger with no
    incoming edges, edges that reference real nodes, required action config, and
    that the graph is acyclic (the engine must terminate).
    """
    errors: list[str] = []

    keys = [str(n.get("node_key", "")) for n in nodes]
    if "" in keys:
        errors.append("every node needs a node_key")
    if len(keys) != len(set(keys)):
        errors.append("node keys must be unique")
    keyset = set(keys)

    for n in nodes:
        kind = n.get("kind")
        if kind not in NODE_KINDS:
            errors.append(f"node {n.get('node_key')!r}: invalid kind {kind!r}")
            continue
        if n.get("type") not in _VALID_TYPES[kind]:
            errors.append(f"node {n.get('node_key')!r}: invalid {kind} type {n.get('type')!r}")
        if kind == "action":
            errors.extend(_action_config_errors(n))
        if kind == "condition":
            errors.extend(_condition_config_errors(n))

    triggers = [n for n in nodes if n.get("kind") == "trigger"]
    if not triggers:
        errors.append("a flow needs exactly one trigger node")
    elif len(triggers) > 1:
        errors.append("a flow can have only one trigger node")

    for e in edges:
        if str(e.get("source_key")) not in keyset or str(e.get("target_key")) not in keyset:
            errors.append(
                f"edge {e.get('source_key')!r}→{e.get('target_key')!r} references a missing node"
            )

    trigger_key = triggers[0].get("node_key") if len(triggers) == 1 else None
    if trigger_key is not None and any(str(e.get("target_key")) == str(trigger_key) for e in edges):
        errors.append("the trigger node cannot have incoming connections")

    if _has_cycle(keyset, edges):
        errors.append("the flow contains a loop — connections must not cycle back")

    trigger_type = str(triggers[0].get("type")) if len(triggers) == 1 else None
    trigger_config = (triggers[0].get("config") or {}) if len(triggers) == 1 else {}
    return GraphValidation(errors=errors, trigger_type=trigger_type, trigger_config=trigger_config)


def _action_config_errors(node: dict[str, Any]) -> list[str]:
    cfg = node.get("config") or {}
    key = node.get("node_key")
    atype = node.get("type")
    if atype == "send":
        policy = str(cfg.get("window_policy", "auto"))
        if policy not in ("auto", "require_template", "freeform_only"):
            return [f"node {key!r}: send has an invalid window_policy"]
        if policy == "require_template" and not cfg.get("template_id"):
            return [f"node {key!r}: send with require_template needs a template"]
        return []
    if atype == "send_template" and not cfg.get("template_id"):
        return [f"node {key!r}: send_template needs a template"]
    if atype == "send_message" and not str(cfg.get("text", "")).strip():
        return [f"node {key!r}: send_message needs text"]
    if atype == "wait":
        if not _positive_int(cfg.get("minutes")):
            return [f"node {key!r}: wait needs minutes > 0"]
        if str(cfg.get("unit", "minutes")) not in _WAIT_UNIT_FACTORS:
            return [f"node {key!r}: wait has an invalid unit"]
        return []
    if atype == "wait_until_before":
        hours = _opt_int(cfg.get("hours"))
        if hours is None or not (1 <= hours <= 168):
            return [f"node {key!r}: wait_until_before needs hours between 1 and 168"]
        if str(cfg.get("anchor", "appointment.start_at")) != "appointment.start_at":
            return [f"node {key!r}: wait_until_before supports only anchor 'appointment.start_at'"]
        return []
    if atype == "ai_reply":
        if not str(cfg.get("objective", "")).strip():
            return [f"node {key!r}: ai_reply needs an objective"]
        policy = str(cfg.get("window_policy", "auto"))
        if policy not in ("auto", "require_template", "freeform_only"):
            return [f"node {key!r}: ai_reply has an invalid window_policy"]
        if policy == "require_template" and not cfg.get("fallback_template_id"):
            return [f"node {key!r}: ai_reply with require_template needs a fallback template"]
        allowed = cfg.get("allowed_actions")
        if allowed is not None and (
            not isinstance(allowed, list)
            or any(a not in AI_REPLY_DISPATCHABLE_ACTIONS for a in allowed)
        ):
            return [f"node {key!r}: ai_reply has an invalid allowed_actions entry"]
        return []
    if atype == "set_lead_field":
        field = str(cfg.get("field", ""))
        if field not in ("tag", "score_delta", "custom_field", "stage"):
            return [f"node {key!r}: set_lead_field has an invalid field"]
        if field == "custom_field" and not str(cfg.get("key", "")).strip():
            return [f"node {key!r}: set_lead_field custom_field needs a key"]
        if field == "score_delta" and not _is_int(cfg.get("value")):
            return [f"node {key!r}: set_lead_field score_delta needs an integer value"]
        return []
    return []


def _condition_config_errors(node: dict[str, Any]) -> list[str]:
    """Validate condition config. `ai_check` and `condition_group` are checked;
    other atomic conditions stay lax, matching the existing behaviour."""
    ntype = node.get("type")
    if ntype == "ai_check":
        cfg = node.get("config") or {}
        if not str(cfg.get("prompt", "")).strip():
            return [f"node {node.get('node_key')!r}: ai_check needs a prompt"]
        return []
    if ntype != "condition_group":
        return []
    cfg = node.get("config") or {}
    key = node.get("node_key")
    operator = str(cfg.get("operator", "and")).lower()
    if operator not in ("and", "or"):
        return [f"node {key!r}: condition_group operator must be 'and' or 'or'"]
    clauses = cfg.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        return [f"node {key!r}: condition_group needs at least one clause"]
    for clause in clauses:
        if (
            not isinstance(clause, dict)
            or str(clause.get("type", "")) not in _ATOMIC_CONDITION_TYPES
        ):
            return [f"node {key!r}: condition_group has a clause with an invalid type"]
    return []


def _has_cycle(keys: set[str], edges: list[dict[str, Any]]) -> bool:
    adj: dict[str, list[str]] = {k: [] for k in keys}
    for e in edges:
        s, t = str(e.get("source_key")), str(e.get("target_key"))
        if s in adj and t in adj:
            adj[s].append(t)
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(keys, white)

    def visit(u: str) -> bool:
        color[u] = gray
        for v in adj[u]:
            if color[v] == gray:
                return True
            if color[v] == white and visit(v):
                return True
        color[u] = black
        return False

    return any(color[k] == white and visit(k) for k in keys)


def outgoing_targets(
    edges: list[dict[str, Any]], from_key: str, *, branch: str = "default"
) -> list[str]:
    """Target node keys reachable from `from_key`.

    For a condition node pass `branch="true"|"false"` to follow only that side;
    for any other node `branch="default"` follows every outgoing edge.
    """
    out: list[str] = []
    for e in edges:
        if str(e.get("source_key")) != from_key:
            continue
        edge_branch = str(e.get("branch", "default"))
        if branch == "default" or edge_branch == branch:
            out.append(str(e.get("target_key")))
    return out


def evaluate_condition(node_type: str, config: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a condition node against a run-time context.

    `context` keys: temperature (str), score (int), within_24h_window (bool),
    minutes_of_day (int), last_message (str). Unknown types fail closed (False).
    `condition_group` combines atomic clauses with AND/OR (+ per-clause negate).
    """
    cfg = config or {}
    if node_type == "condition_group":
        return _evaluate_group(cfg, context)
    return _evaluate_atomic(node_type, cfg, context)


def _evaluate_atomic(node_type: str, cfg: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate one of the 5 atomic conditions. Unknown types fail closed (False)."""
    if node_type == "lead_temperature":
        op = str(cfg.get("op", "=="))
        value = str(cfg.get("value", ""))
        actual = str(context.get("temperature", ""))
        return actual == value if op == "==" else actual != value
    if node_type == "lead_score":
        return _compare_number(context.get("score"), cfg.get("op", ">="), cfg.get("value"))
    if node_type == "within_24h_window":
        return bool(context.get("within_24h_window"))
    if node_type == "time_of_day":
        return _within_time_window(context.get("minutes_of_day"), cfg.get("from"), cfg.get("to"))
    if node_type == "message_contains":
        text = str(context.get("last_message", "")).lower()
        keywords = [str(k).lower() for k in (cfg.get("keywords") or [])]
        return any(k and k in text for k in keywords)
    return False


def _evaluate_group(cfg: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate a composite condition: flat clauses combined with AND/OR.

    Each clause is `{"type": <atomic type>, "negate": bool, ...atomic cfg keys}` —
    the clause dict *is* the atomic config, so we pass it straight to
    `_evaluate_atomic`. An empty group or a clause with a non-atomic `type` (e.g. a
    nested `condition_group`) fails closed, matching the unknown-type behaviour.
    """
    clauses = cfg.get("clauses") or []
    if not clauses:
        return False
    operator = str(cfg.get("operator", "and")).lower()
    results: list[bool] = []
    for clause in clauses:
        ctype = str(clause.get("type", ""))
        if ctype not in _ATOMIC_CONDITION_TYPES:
            results.append(False)
            continue
        value = _evaluate_atomic(ctype, clause, context)
        results.append(not value if clause.get("negate") else value)
    return any(results) if operator == "or" else all(results)


def _compare_number(actual: Any, op: Any, expected: Any) -> bool:
    try:
        a = float(actual)
        b = float(expected)
    except (TypeError, ValueError):
        return False
    op = str(op)
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return False


def _minutes(hhmm: Any) -> int | None:
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _within_time_window(now_min: Any, frm: Any, to: Any) -> bool:
    start, end = _minutes(frm), _minutes(to)
    if now_min is None or start is None or end is None:
        return False
    now = int(now_min)
    # Same-day window, or one that wraps past midnight (e.g. 22:00→06:00).
    return start <= now <= end if start <= end else now >= start or now <= end


def resolve_send_node_at(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    attempt_index: int,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Walk from the trigger and return the config of the attempt_index-th `send`.

    The bridge that lets a scheduler resolve a lifecycle "step" from a graph:
    starting at the single trigger, follow the path — evaluating `condition` nodes
    against `context` (reusing `evaluate_condition`), skipping `wait` nodes (the
    scheduler owns timing) — and count `send` action nodes. Return the matching
    node's config dict, or None when the resolved path has fewer than
    `attempt_index + 1` sends. Side-effect free. The graph is validated acyclic,
    so the visited-set guarantees termination.

    For system lifecycle flows each non-condition node has one outgoing edge and
    each condition has one edge per branch, so the walk traces a single path.
    """
    by_key = {str(n.get("node_key")): n for n in nodes}
    trigger = next((n for n in nodes if n.get("kind") == "trigger"), None)
    if trigger is None:
        return None

    seen = 0
    visited: set[str] = set()
    frontier: list[str] = outgoing_targets(edges, str(trigger.get("node_key")))
    while frontier:
        key = frontier.pop(0)
        if key in visited:
            continue
        visited.add(key)
        node = by_key.get(key)
        if node is None:
            continue
        kind = node.get("kind")
        if kind == "condition":
            passed = evaluate_condition(str(node.get("type")), node.get("config") or {}, context)
            frontier.extend(outgoing_targets(edges, key, branch="true" if passed else "false"))
        elif kind == "action" and node.get("type") == "send":
            if seen == attempt_index:
                return dict(node.get("config") or {})
            seen += 1
            frontier.extend(outgoing_targets(edges, key))
        else:
            # wait / non-send actions / stray trigger: traverse, don't count.
            frontier.extend(outgoing_targets(edges, key))
    return None


# --------------------------------------------------------------------------- #
# Send plan — timing sourced from the graph (ADR 0011 / audit A2)
# --------------------------------------------------------------------------- #

# Max number of `send` nodes a system flow may chain — mirrors the config_resolver
# schema ranges (max_followups 1-4, max_attempts 1-5, reminder_schedule max 5) and
# the schedulers' hardcoded scan ceilings.
NO_ANSWER_MAX_SENDS = 4
REACTIVATION_MAX_SENDS = 5
BOOKING_MAX_REMINDERS = 5


@dataclass(slots=True)
class PlannedSend:
    """One `send` in a resolved plan, with the timing accumulated before it."""

    attempt_index: int
    # Relative delay (minutes) before this send, measured from the PREVIOUS send
    # (or from the trigger for the first) — the sum of the `wait` nodes in between.
    delay_minutes: int
    # Hours-before-the-appointment for a send preceded by `wait_until_before`
    # (booking reminders); None for relative-timed sends.
    anchor_hours_before: int | None
    config: dict[str, Any]


@dataclass(slots=True)
class SendPlan:
    sends: list[PlannedSend] = field(default_factory=list)

    @property
    def max_attempts(self) -> int:
        return len(self.sends)


def resolve_send_plan(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
) -> SendPlan:
    """Walk from the trigger and return the ordered plan of `send` nodes with the
    timing accumulated from `wait` / `wait_until_before` nodes between them.

    Unlike `resolve_send_node_at` (which *skips* waits and resolves a single
    attempt's content), this **accumulates** the relative `wait` minutes since the
    previous send into `delay_minutes`, and carries a preceding `wait_until_before`
    node's hours into `anchor_hours_before`. The gap is per-step incremental (not
    cumulative), matching the schedulers' `last_*_at` arithmetic.

    Side-effect free; condition nodes are evaluated against `context` exactly like
    `resolve_send_node_at`. The graph is validated acyclic, so the visited-set
    guarantees termination.
    """
    ctx = context or {}
    by_key = {str(n.get("node_key")): n for n in nodes}
    trigger = next((n for n in nodes if n.get("kind") == "trigger"), None)
    plan = SendPlan()
    if trigger is None:
        return plan

    pending_minutes = 0
    pending_anchor: int | None = None
    visited: set[str] = set()
    frontier: list[str] = outgoing_targets(edges, str(trigger.get("node_key")))
    while frontier:
        key = frontier.pop(0)
        if key in visited:
            continue
        visited.add(key)
        node = by_key.get(key)
        if node is None:
            continue
        kind = node.get("kind")
        ntype = str(node.get("type"))
        if kind == "condition":
            passed = evaluate_condition(ntype, node.get("config") or {}, ctx)
            frontier.extend(outgoing_targets(edges, key, branch="true" if passed else "false"))
        elif kind == "action" and ntype == "send":
            plan.sends.append(
                PlannedSend(
                    attempt_index=len(plan.sends),
                    delay_minutes=pending_minutes,
                    anchor_hours_before=pending_anchor,
                    config=dict(node.get("config") or {}),
                )
            )
            pending_minutes = 0
            pending_anchor = None
            frontier.extend(outgoing_targets(edges, key))
        elif kind == "action" and ntype == "wait":
            pending_minutes += wait_minutes(node.get("config") or {})
            frontier.extend(outgoing_targets(edges, key))
        elif kind == "action" and ntype == "wait_until_before":
            pending_anchor = _opt_int((node.get("config") or {}).get("hours"))
            frontier.extend(outgoing_targets(edges, key))
        else:
            frontier.extend(outgoing_targets(edges, key))
    return plan


def system_flow_timing_errors(
    system_key: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[str]:
    """Compliance/anti-spam bounds for a *system* flow whose timing now lives in
    the graph (ADR 0011). Mirrors the config_resolver schema ranges; enforced by
    the router only when **enabling** a system flow. Custom flows are unaffected.
    """
    errors: list[str] = []
    sends = [n for n in nodes if n.get("kind") == "action" and n.get("type") == "send"]
    waits = [n for n in nodes if n.get("kind") == "action" and n.get("type") == "wait"]
    anchors = [
        n for n in nodes if n.get("kind") == "action" and n.get("type") == "wait_until_before"
    ]
    trigger = next((n for n in nodes if n.get("kind") == "trigger"), None)
    trig_cfg = (trigger.get("config") or {}) if trigger else {}

    if system_key == "no_answer":
        if len(sends) > NO_ANSWER_MAX_SENDS:
            errors.append(
                f"no_answer: al massimo {NO_ANSWER_MAX_SENDS} invii (trovati {len(sends)})"
            )
        delay = trig_cfg.get("delay_minutes")
        if delay is not None and not _in_range(delay, 30, 480):
            errors.append("no_answer: il ritardo iniziale (delay_minutes) deve essere tra 30 e 480")
        if any(not (30 <= wait_minutes(w.get("config") or {}) <= 2880) for w in waits):
            errors.append("no_answer: ogni attesa tra invii deve essere tra 30 minuti e 48 ore")
    elif system_key == "reactivation":
        if len(sends) > REACTIVATION_MAX_SENDS:
            errors.append(
                f"reactivation: al massimo {REACTIVATION_MAX_SENDS} invii (trovati {len(sends)})"
            )
        days = trig_cfg.get("days")
        if days is not None and not _in_range(days, 30, 180):
            errors.append("reactivation: i giorni di dormienza (days) devono essere tra 30 e 180")
        if any(not (3 * 1440 <= wait_minutes(w.get("config") or {}) <= 30 * 1440) for w in waits):
            errors.append(
                "reactivation: ogni intervallo tra tentativi deve essere tra 3 e 30 giorni"
            )
    elif system_key == "booking_reminder":
        if len(sends) > BOOKING_MAX_REMINDERS:
            errors.append(f"booking_reminder: al massimo {BOOKING_MAX_REMINDERS} promemoria")
        if waits:
            errors.append(
                "booking_reminder: usa «attendi fino a X ore prima» invece di attese relative"
            )
        for a in anchors:
            hours = _opt_int((a.get("config") or {}).get("hours"))
            if hours is None or not (1 <= hours <= 168):
                errors.append("booking_reminder: le ore di anticipo devono essere tra 1 e 168")
                break
    return errors
