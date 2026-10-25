"""Smart Refunds demo: streamlit run app/main.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import manager  # noqa: E402
import reviewer  # noqa: E402
from state import load_context  # noqa: E402

st.set_page_config(page_title="Smart Refunds", layout="wide")

with st.sidebar:
    st.title("Smart Refunds")
    role = st.radio("Viewing as", ["Reviewer", "Fraud team", "Manager"], key="role")
    st.caption("Demo environment: choose a role (no sign-in).")
    if st.button("Reset demo", key="reset_demo", help="Clears decisions, payments and published rules"):
        load_context().store.reset()
        st.session_state.clear()
        st.rerun()

ctx = load_context()
with st.sidebar:
    st.caption(f"Rules v{ctx.live.version} · {ctx.facts_source}")
st.info("Demo environment: refunds are simulated and no messages are sent to customers.")

if role == "Manager":
    manager.render(ctx)
else:
    reviewer.render(ctx, role)
