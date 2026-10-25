"""Reviewer (and fraud team) queue: lanes, incidents, case view, decisions."""
from __future__ import annotations

from datetime import date

import streamlit as st

from smartrefunds.engine import Decision
from smartrefunds.facts import ESCALATION_FACTS, FACT_LABELS, FACT_NAMES
from smartrefunds.preview import history_label
from smartrefunds.rules import LANES

REASON_CODES = ["", "Customer history", "Evidence in notes", "Policy exception approved by manager",
                "Flag incorrect", "Other"]
PARTIAL_PRESETS = ["shipping only", "25%", "30%", "50%", "custom"]
SEVERITY = {"S3": 5, "R1": 4, "R2": 3, "R5": 3, "R6": 3, "S2": 2, "S1": 2}


def _key(d: Decision) -> tuple[str, int]:
    return d.request.request_id, d.request.row_index


def _lane(ctx, d: Decision) -> str:
    return ctx.store.lane_override(*_key(d)) or d.outcome.lane


def _flags(d: Decision) -> list[tuple[str, str, str]]:
    if d.facts is None:
        return []
    out = []
    for name in FACT_NAMES:
        f = getattr(d.facts, name)
        if f.value not in ("not_mentioned", "no"):
            out.append((name, f.value, f.evidence))
    if d.facts.location_tag.value:
        out.append(("location_tag", d.facts.location_tag.value, d.facts.location_tag.evidence))
    return out


def _priority(d: Decision):
    flags = sum(1 for n, v, _ in _flags(d) if n in ESCALATION_FACTS and v == "yes")
    return (-SEVERITY.get(d.outcome.rule_id, 1), -flags, -(d.request.amount_usd or 0), d.request.requested_at or date.max)


def _label(d: Decision, copies: dict[str, list[int]]) -> str:
    r = d.request
    amount = f"${r.amount_usd:,.2f}".replace(".00", "") if r.amount_usd is not None else "$?"
    label = f"{r.request_id} · {amount} · {r.stated_reason or 'unknown reason'}"
    if len(copies[r.request_id]) > 1:
        label += f" · copy {copies[r.request_id].index(r.row_index) + 1}"
    return label


def _flash() -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])


def render(ctx, role: str) -> None:
    _flash()
    review = [d for d in ctx.decisions if d.outcome.outcome == "needs_review"]
    status = {_key(d): ctx.store.status(*_key(d)) for d in review}
    visible = ["Fraud team"] if role == "Fraud team" else list(LANES)
    open_by_lane = {lane: sorted([d for d in review if status[_key(d)] == "open" and _lane(ctx, d) == lane],
                                 key=_priority) for lane in visible}
    waiting = [d for d in review if status[_key(d)] == "waiting" and _lane(ctx, d) in visible]
    labels = [f"{lane} ({len(open_by_lane[lane])})" for lane in visible] + [f"Waiting ({len(waiting)})"]
    choice = st.radio("Lane", labels, horizontal=True, key="lane", label_visibility="collapsed")
    lane = choice.rsplit(" (", 1)[0]
    cases = waiting if lane == "Waiting" else open_by_lane[lane]

    if lane == "Delivery disputes":
        _incidents(ctx, {d.request.request_id for d in cases})
    if not cases:
        st.caption("Nothing in this lane.")
        return
    _table(ctx, cases)

    copies: dict[str, list[int]] = {}
    for d in ctx.decisions:
        copies.setdefault(d.request.request_id, []).append(d.request.row_index)
    by_label = {_label(d, copies): d for d in cases}
    picked = st.selectbox("Open a case", list(by_label), key="case")
    _case(ctx, by_label[picked], role, waiting=lane == "Waiting")


def _incidents(ctx, ids: set[str]) -> None:
    for inc in ctx.incidents:
        members = [i for i in inc.request_ids if i in ids]
        if len(members) < 3:
            continue
        with st.container(border=True):
            st.markdown(f"**Incident: {inc.tag.title()} backlog** · {len(members)} cases · "
                        f"${inc.total_amount:,.0f} · first seen {inc.first_seen:%d %b %Y}")
            st.caption("Cases: " + ", ".join(members) + " · Also written as: " + ", ".join(f"“{s}”" for s in inc.spellings))
            if st.button("Suggest a rule to the manager", key="suggest_rule"):
                st.session_state["prefill_change"] = (f"{inc.tag.title()} never-arrived cases under $100: "
                                                      "refund in full")
                st.session_state["flash"] = ("ok", "Sent to the manager's rule screen (switch role to Manager).")
                st.rerun()


def _table(ctx, cases: list[Decision]) -> None:
    rows = []
    for d in cases:
        r = d.request
        rows.append({
            "Request": r.request_id, "$": r.amount_usd, "Reason": r.stated_reason,
            "Why it's here": d.outcome.reasons[0] if d.outcome.reasons else "",
            "Suggested": "Reject (draft)" if d.outcome.draft_rejection else "Review",
            "Requested": r.requested_at.isoformat() if r.requested_at else "?",
        })
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _similar(ctx, d: Decision) -> list[dict]:
    top = next((n for n in ESCALATION_FACTS if d.facts is not None and getattr(d.facts, n).value == "yes"), None)
    if top is None:
        return []
    out = []
    for h in ctx.history:
        if h.stated_reason != d.request.stated_reason:
            continue
        hf = ctx.history_provider.get(h)
        if hf is not None and getattr(hf, top).value == "yes":
            out.append({"Request": h.request_id, "$": h.amount_usd, "Decision": h.decision,
                        "Afterwards": h.outcome_note or "—", "Outcome": history_label(h)})
    out.sort(key=lambda x: x["Outcome"] == "ok")
    return out[:3]


CASE_KEYS = ("reason_code", "note", "partial_preset", "partial_amount", "draft_text", "info_what", "move_to")


def _reset_case_widgets(d: Decision) -> None:
    """A reason code or message typed on one case must never carry over to the next."""
    key = (d.request.request_id, d.request.row_index)
    if st.session_state.get("_open_case") != key:
        for k in list(st.session_state.keys()):
            if k in CASE_KEYS or (isinstance(k, str) and k.startswith("fb_")):
                del st.session_state[k]
        st.session_state["_open_case"] = key


def _case(ctx, d: Decision, role: str, waiting: bool) -> None:
    _reset_case_widgets(d)
    r, o = d.request, d.outcome
    left, right = st.columns([1.4, 1])
    with left:
        st.subheader(f"{r.request_id} · {r.stated_reason} · {r.category} · {r.payment_method}")
        c = st.columns(4)
        c[0].metric("Amount", f"${r.amount_usd:,.2f}" if r.amount_usd is not None else "?")
        c[1].metric("Customer", r.customer_id or "?")
        c[2].metric("Tenure / prior refunds", f"{r.tenure_months:g} mo / {r.prior_refunds_12m}"
                    if r.tenure_months is not None else "?")
        c[3].metric("Days since delivery", f"{r.days_since_delivery:g}" if r.days_since_delivery is not None else "?")
        if r.cleaning_changes:
            st.caption("Cleaned on load: " + " · ".join(r.cleaning_changes))
        if r.invalid_reasons:
            st.caption("Problems in the data: " + " · ".join(r.invalid_reasons))
        if o.warning:
            st.error(o.warning)

        st.markdown("**Why this case needs review**")
        titles = {rule.id: rule.title for rule in ctx.live.rules}
        for rid in o.matched_rule_ids:
            if rid != "A1" and titles.get(rid):
                st.markdown(f"- `{rid}` {titles[rid]}")

        with st.container(border=True):
            st.markdown("**Notes analysis**")
            if d.facts is None:
                st.markdown("_The notes could not be analysed automatically. Review them below._")
            else:
                st.markdown(d.facts.summary)
                for name, value, evidence in _flags(d):
                    st.checkbox(f"{FACT_LABELS[name]}: **{value.replace('_', ' ')}** — “{evidence}”", value=True,
                                key=f"fb_{name}", help="Untick if this flag is incorrect")
                st.caption("Flags only ever send a case for review; they never approve a refund. "
                           "Untick any flag that is incorrect.")

        st.markdown("**Original notes**")
        st.caption("Shown exactly as written by the customer and the support agent.")
        st.text(f"Support note: {r.support_note or '(empty)'}")
        st.text(f"Customer note: {r.customer_note or '(empty)'}")
        similar = _similar(ctx, d)
        if similar:
            st.markdown("**Similar past cases**")
            st.dataframe(similar, hide_index=True, use_container_width=True)

    with right:
        if waiting:
            st.markdown(f"**Waiting for:** {ctx.store.waiting_for(*_key(d))}")
            if st.button("Carrier/customer replied (simulated)", key="resume"):
                ctx.store.resume(*_key(d))
                st.session_state["flash"] = ("ok", f"{r.request_id} is back in its lane.")
                st.rerun()
            return
        _actions(ctx, d, role)


def _actions(ctx, d: Decision, role: str) -> None:
    r, o = d.request, d.outcome
    suggestion = "reject (draft message ready)" if o.draft_rejection else "review"
    st.markdown(f"**Decision** · recommendation: {suggestion}")
    reason_code = st.selectbox("Reason code (required when you go against the suggestion)", REASON_CODES,
                               key="reason_code")
    note = st.text_input("Note (optional)", key="note")
    feedback = {k[3:]: v for k, v in st.session_state.items() if isinstance(k, str) and k.startswith("fb_")}

    def record(action: str, amount: float | None) -> None:
        ctx.store.record_decision(r.request_id, r.row_index, role, action, amount, reason_code or None, note,
                                  ctx.live.version, f"{o.outcome}: {o.lane} ({o.rule_id})", feedback)

    def pay_and_record(action: str, amount: float) -> None:
        if o.draft_rejection and not reason_code:
            st.error("Approving goes against the suggested rejection: pick a reason code first.")
            return
        result = ctx.store.pay(r.request_id, amount, r.payment_method or "original method")
        if not result.ok:
            st.error(result.message)
            return
        record(action, amount)
        st.session_state["flash"] = ("ok", result.message)
        st.rerun()

    if r.amount_usd is None:
        st.caption("The amount couldn't be read, so this request can't be approved in full. Use a partial "
                   "refund with the correct amount, or reject.")
    if st.button("Approve in full", key="approve_full", type="primary", disabled=r.amount_usd is None):
        pay_and_record("approve_full", r.amount_usd or 0.0)

    with st.expander("Approve partial"):
        preset = st.radio("Preset", PARTIAL_PRESETS, horizontal=True, key="partial_preset")
        pct = {"25%": 0.25, "30%": 0.30, "50%": 0.50}.get(preset)
        default = round((r.amount_usd or 0) * pct, 2) if pct else 0.0
        cap = r.amount_usd if r.amount_usd is not None else 10_000.0
        amount = st.number_input("Amount to refund ($)", min_value=0.0, max_value=float(cap), value=min(default, cap),
                                 key="partial_amount")
        if st.button("Approve partial", key="approve_partial"):
            pay_and_record("approve_partial", amount)

    with st.expander("Reject", expanded=bool(o.draft_rejection)):
        text = st.text_area("Message to the customer (not sent in this demo)", value=o.draft_rejection or "",
                            key="draft_text")
        if st.button("Reject", key="reject"):
            if not o.draft_rejection and not reason_code:
                st.error("Rejecting without a policy suggestion: pick a reason code first.")
            elif not text.strip():
                st.error("Write the message the customer will get.")
            else:
                record("reject", None)
                st.session_state["flash"] = ("ok", f"{r.request_id} rejected. Message drafted (not sent).")
                st.rerun()

    with st.expander("Request info"):
        what = st.radio("Ask for", ["photo", "carrier investigation"], key="info_what")
        if st.button("Request info", key="request_info"):
            ctx.store.set_waiting(r.request_id, r.row_index, what)
            record("request_info", None)
            st.session_state["flash"] = ("ok", f"{r.request_id} is waiting for: {what}.")
            st.rerun()

    with st.expander("Move to another lane"):
        target = st.selectbox("Lane", [l for l in LANES if l != _lane(ctx, d)], key="move_to")
        if st.button("Move", key="move_lane"):
            ctx.store.set_lane(r.request_id, r.row_index, target)
            record("move_lane", None)
            st.session_state["flash"] = ("ok", f"{r.request_id} moved to {target}.")
            st.rerun()
