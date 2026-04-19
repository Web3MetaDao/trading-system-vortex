from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Vortex Dashboard - Professional Monitoring Panel
st.set_page_config(page_title="Vortex Dashboard", layout="wide")

st.title("🌪️ Vortex Professional Trading Dashboard")
st.sidebar.header("System Control")
st.sidebar.button("Restart All Workers")
st.sidebar.button("Force Stop All")


# Load Real-time Data
def load_live_positions() -> pd.DataFrame:
    # Dummy data for demonstration
    return pd.DataFrame(
        [
            {
                "Symbol": "BTCUSDT",
                "Side": "LONG",
                "Entry": 75800.0,
                "Current": 76100.0,
                "PnL%": 0.4,
                "Size": 500.0,
            },
            {
                "Symbol": "ETHUSDT",
                "Side": "SHORT",
                "Entry": 2400.0,
                "Current": 2390.0,
                "PnL%": 0.42,
                "Size": 300.0,
            },
        ]
    )


def load_equity_curve() -> pd.DataFrame:
    # Dummy equity curve
    dates = pd.date_range(end=datetime.now(), periods=10)
    equity = [1000, 1010, 1005, 1025, 1020, 1040, 1035, 1055, 1060, 1080]
    return pd.DataFrame({"Date": dates, "Equity": equity})


# --- UI Sections ---
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Equity", "$1,080.00", "+8.0%")
with col2:
    st.metric("Open Positions", "2", "Stable")
with col3:
    st.metric("Daily Win Rate", "65.4%", "+2.1%")

st.subheader("📊 Live Portfolio Performance")
positions = load_live_positions()
st.table(positions)

st.subheader("📈 Equity Curve")
equity_df = load_equity_curve()
fig = go.Figure()
fig.add_trace(
    go.Scatter(x=equity_df["Date"], y=equity_df["Equity"], mode="lines+markers", name="Equity")
)
fig.update_layout(xaxis_title="Date", yaxis_title="Equity (USDT)")
st.plotly_chart(fig, use_container_width=True)

st.subheader("📁 System Logs")
st.code(
    "INFO: [BTCUSDT] Signal A+ detected. ML Confidence: 0.89\nINFO: [BTCUSDT] Risk Approved. Size: 150.0 USDT\nINFO: [ETHUSDT] Position closed via ATR Trailing Stop. PnL: +12.5 USDT",
    language="log",
)

# Sidebar - Symbol Config
st.sidebar.subheader("Active Symbols")
st.sidebar.multiselect(
    "Monitoring", ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"], default=["BTCUSDT", "ETHUSDT"]
)

st.sidebar.subheader("Risk Parameters")
st.sidebar.slider("ATR Multiplier", 1.5, 5.0, 2.5)
st.sidebar.slider("Max Risk Per Trade (%)", 0.5, 5.0, 1.5)
