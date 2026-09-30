"""
Digital Wallet - User Risk Profile dashboard.

Run:  streamlit run app.py
Uses PostgreSQL if DATABASE_URL is set, otherwise the generated CSV files.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import risk

st.set_page_config(page_title="Wallet Risk Profile", page_icon="🛡️", layout="wide")

TIER_COLORS = {"Low": "#2e9e6b", "Medium": "#e0a526", "High": "#d64545"}


@st.cache_data(show_spinner="Loading data...")
def get_data():
    return risk.load_data()


metrics, txns = get_data()

# ------------------------------------------------------------------ sidebar
st.sidebar.title("🛡️ Wallet Risk")
st.sidebar.caption("Data source: " + ("PostgreSQL (`user_risk_metrics` view)" if risk.using_postgres()
                                      else "CSV files (pandas)"))
tier_filter = st.sidebar.multiselect("Risk tier", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
pool = metrics[metrics["risk_tier"].isin(tier_filter)].sort_values("risk_score", ascending=False)
if pool.empty:
    st.warning("No users match the selected tiers.")
    st.stop()

labels = {
    int(r.user_id): f"#{int(r.user_id)} · {r.full_name} · {r.risk_tier} ({r.risk_score:.0f})"
    for r in pool.itertuples()
}
selected_id = st.sidebar.selectbox("Select user", list(labels), format_func=labels.get,
                                   help="Sorted by risk score, highest first.")
st.sidebar.markdown("---")
st.sidebar.metric("Users", f"{len(metrics):,}")
st.sidebar.metric("Transactions", f"{len(txns):,}")
st.sidebar.metric("High-risk users", int((metrics["risk_tier"] == "High").sum()))

tab_profile, tab_watch = st.tabs(["👤 User risk profile", "🔎 Watchlist filter"])

# ------------------------------------------------------------ profile tab
with tab_profile:
    u = metrics.loc[metrics["user_id"] == selected_id].iloc[0]
    u_txns = txns[txns["user_id"] == selected_id].sort_values("txn_timestamp")
    color = TIER_COLORS[u.risk_tier]

    head, gauge_col = st.columns([3, 2])
    with head:
        st.title(u.full_name)
        st.markdown(
            f"User **#{int(u.user_id)}** · {u.country} · {u.account_tier.title()} tier · "
            f"<span style='background:{color};color:white;padding:2px 10px;border-radius:12px;"
            f"font-weight:600'>{u.risk_tier} risk</span>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Txns (last 30d)", int(u.txns_30d),
                  help="Transaction frequency, measured up to the latest transaction in the data.")
        c2.metric("Avg balance", f"{u.avg_balance:,.0f}")
        c3.metric("Failure rate", f"{u.failure_rate:.1%}")
        c4.metric("Failed payments", f"{int(u.failed_txns)} / {int(u.total_txns)}")
    with gauge_col:
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=float(u.risk_score),
            number={"suffix": " / 100"},
            title={"text": "Risk score"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": color},
                "steps": [
                    {"range": [0, 30], "color": "#e3f4ec"},
                    {"range": [30, 55], "color": "#fbf0d4"},
                    {"range": [55, 100], "color": "#f8dcdc"},
                ],
            },
        ))
        fig.update_layout(height=230, margin=dict(l=20, r=20, t=50, b=10))
        st.plotly_chart(fig, width="stretch")

    st.divider()

    # --- score breakdown + peer comparison
    left, right = st.columns(2)
    with left:
        st.subheader("What drives the score")
        comp = risk.score_components(u.failure_rate, u.avg_balance, u.txns_30d, u.night_share)
        comp_df = pd.DataFrame({
            "Signal": list(comp),
            "Points": [round(v, 1) for v in comp.values()],
            "Max": [risk.WEIGHTS[k] for k in comp],
        })
        bar = go.Figure()
        bar.add_bar(y=comp_df["Signal"], x=comp_df["Max"], orientation="h",
                    marker_color="#ececec", name="Max points", hoverinfo="skip")
        bar.add_bar(y=comp_df["Signal"], x=comp_df["Points"], orientation="h",
                    marker_color=color, name="Points", text=comp_df["Points"], textposition="inside")
        bar.update_layout(barmode="overlay", height=280, showlegend=False,
                          margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))
        st.plotly_chart(bar, width="stretch")

    with right:
        st.subheader("How they compare to all users")
        rows = []
        for label, col, fmt in [
            ("Failure rate", "failure_rate", "{:.1%}"),
            ("Avg balance", "avg_balance", "{:,.0f}"),
            ("Txns (last 30d)", "txns_30d", "{:.0f}"),
            ("Night-time share", "night_share", "{:.1%}"),
        ]:
            rows.append({
                "Metric": label,
                "This user": fmt.format(u[col]),
                "Median user": fmt.format(metrics[col].median()),
                "Percentile": f"{risk.percentile_of(metrics[col], u[col]):.0f}th",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Percentile = share of users with an equal or lower value. "
                   "For balance, a *low* percentile means a thin balance (riskier).")

    st.divider()

    # --- time-based charts
    a, b = st.columns(2)
    with a:
        st.subheader("Balance over time")
        line = px.line(u_txns, x="txn_timestamp", y="balance_after", labels={"txn_timestamp": "", "balance_after": "Balance"})
        line.update_traces(line_color="#3b6fd4")
        failed_pts = u_txns[u_txns["status"] == "failed"]
        line.add_scatter(x=failed_pts["txn_timestamp"], y=failed_pts["balance_after"], mode="markers",
                         marker=dict(color="#d64545", size=9, symbol="x"), name="Failed payment")
        line.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                           legend=dict(orientation="h", y=1.1))
        st.plotly_chart(line, width="stretch")

    with b:
        st.subheader("Monthly activity")
        monthly = (u_txns.assign(month=u_txns["txn_timestamp"].dt.to_period("M").dt.to_timestamp())
                   .groupby(["month", "status"]).size().reset_index(name="count"))
        mbar = px.bar(monthly, x="month", y="count", color="status",
                      color_discrete_map={"success": "#2e9e6b", "failed": "#d64545"},
                      labels={"month": "", "count": "Transactions"})
        mbar.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.1))
        st.plotly_chart(mbar, width="stretch")

    c, d = st.columns(2)
    with c:
        st.subheader("Why payments failed")
        reasons = u_txns[u_txns["status"] == "failed"]["failure_reason"].value_counts().reset_index()
        reasons.columns = ["reason", "count"]
        if reasons.empty:
            st.success("No failed payments for this user.")
        else:
            rfig = px.bar(reasons, x="count", y="reason", orientation="h", labels={"reason": "", "count": "Failures"})
            rfig.update_traces(marker_color="#d64545")
            rfig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))
            st.plotly_chart(rfig, width="stretch")
    with d:
        st.subheader("Recent failed payments")
        recent_fail = (u_txns[u_txns["status"] == "failed"]
                       .sort_values("txn_timestamp", ascending=False)
                       .head(8)[["txn_timestamp", "txn_type", "amount", "failure_reason", "channel"]])
        st.dataframe(recent_fail, hide_index=True, width="stretch")

# ---------------------------------------------------------- watchlist tab
with tab_watch:
    st.subheader("Filter users by frequency, balance and failed payments")
    st.caption("Same logic as query Q4 in `sql/03_queries.sql`, but interactive.")
    f1, f2, f3, f4 = st.columns(4)
    min_freq = f1.slider("Min txns in last 30d", 0, 20, 3)
    max_bal = f2.slider("Max avg balance", 0, 300_000, 25_000, step=1_000)
    min_fail = f3.slider("Min failure rate", 0.0, 0.6, 0.15, step=0.01, format="%.2f")
    min_txns = f4.slider("Min total txns", 1, 50, 10)

    hits = metrics[
        (metrics["txns_30d"] >= min_freq)
        & (metrics["avg_balance"] <= max_bal)
        & (metrics["failure_rate"] >= min_fail)
        & (metrics["total_txns"] >= min_txns)
    ].sort_values("risk_score", ascending=False)

    m1, m2 = st.columns(2)
    m1.metric("Users matching", len(hits))
    m2.metric("Share of all users", f"{len(hits) / len(metrics):.1%}")

    show_cols = ["user_id", "full_name", "total_txns", "txns_30d", "avg_balance",
                 "failed_txns", "failure_rate", "risk_score", "risk_tier"]
    st.dataframe(
        hits[show_cols],
        hide_index=True,
        width="stretch",
        column_config={
            "failure_rate": st.column_config.ProgressColumn("failure_rate", min_value=0.0, max_value=1.0, format="%.2f"),
            "avg_balance": st.column_config.NumberColumn("avg_balance", format="%.0f"),
            "risk_score": st.column_config.ProgressColumn("risk_score", min_value=0, max_value=100, format="%.1f"),
        },
    )

    st.subheader("All users: balance vs failure rate")
    scatter = px.scatter(
        metrics, x="avg_balance", y="failure_rate", color="risk_tier", size="total_txns",
        color_discrete_map=TIER_COLORS, category_orders={"risk_tier": ["High", "Medium", "Low"]},
        hover_data=["user_id", "full_name", "risk_score"],
        labels={"avg_balance": "Average balance", "failure_rate": "Failure rate"},
        log_x=True,
    )
    sel = metrics[metrics["user_id"] == selected_id]
    scatter.add_scatter(x=sel["avg_balance"], y=sel["failure_rate"], mode="markers",
                        marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(color="black", width=3)),
                        name="Selected user")
    scatter.update_layout(height=450, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(scatter, width="stretch")
