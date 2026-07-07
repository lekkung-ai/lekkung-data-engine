import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 🔌 เชื่อมต่อระบบ Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR  # type: ignore
from utils import load_full_df  # type: ignore


def plot_kell_verification(ticker="SCB"):
    print(f"🔍 กำลังสร้างกราฟ X-Ray เพื่อตรวจสอบ Criteria ของ {ticker}...")

    file_path = HISTORY_DIR / f"{ticker}.csv"
    if not file_path.exists():
        print(f"❌ ไม่พบไฟล์ประวัติของ {ticker}")
        return

    df = load_full_df(file_path)

    # คำนวณเส้นเหมือนในบอทเป๊ะๆ
    df["EMA10"] = (
        pd.to_numeric(df["Close"], errors="coerce").ewm(span=10, adjust=False).mean()
    )
    df["EMA20"] = (
        pd.to_numeric(df["Close"], errors="coerce").ewm(span=20, adjust=False).mean()
    )
    df["SMA50"] = pd.to_numeric(df["Close"], errors="coerce").rolling(window=50).mean()
    df["SMA200"] = (
        pd.to_numeric(df["Close"], errors="coerce").rolling(window=200).mean()
    )

    # ตัดเอาแค่ 150 วันล่าสุดมาโชว์ จะได้เห็นชัดๆ
    df_plot = df.tail(150)

    # 💡 สร้างกราฟ Plotly แบบ 2 ชั้น (ราคา ด้านบน, Volume ด้านล่าง)
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=("Price & Trend", "Volume (Dry Up Check)"),
        row_width=[0.25, 0.75],
    )

    # แท่งเทียน (Candlestick)
    fig.add_trace(
        go.Candlestick(
            x=df_plot["Date"],
            open=df_plot["Open"],
            high=df_plot["High"],
            low=df_plot["Low"],
            close=df_plot["Close"],
            name="Price",
        ),
        row=1,
        col=1,
    )

    # เส้น MA ทั้ง 4 ด่าน
    fig.add_trace(
        go.Scatter(
            x=df_plot["Date"],
            y=df_plot["EMA10"],
            line=dict(color="fuchsia", width=2),
            name="EMA10",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df_plot["Date"],
            y=df_plot["EMA20"],
            line=dict(color="orange", width=2),
            name="EMA20",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df_plot["Date"],
            y=df_plot["SMA50"],
            line=dict(color="blue", width=2),
            name="SMA50",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df_plot["Date"],
            y=df_plot["SMA200"],
            line=dict(color="red", width=3, dash="dot"),
            name="SMA200",
        ),
        row=1,
        col=1,
    )

    # 📊 แท่ง Volume ด้านล่าง (แยกสีเขียว-แดง ตามราคาปิด)
    colors = [
        "green" if row["Close"] >= row["Open"] else "red"
        for index, row in df_plot.iterrows()
    ]
    fig.add_trace(
        go.Bar(
            x=df_plot["Date"], y=df_plot["Volume"], marker_color=colors, name="Volume"
        ),
        row=2,
        col=1,
    )

    # ตกแต่งกราฟให้สวยงาม
    fig.update_layout(
        title=f"🎯 {ticker} - Oliver Kell Criteria Verification",
        template="plotly_dark",
        height=800,
        xaxis_rangeslider_visible=False,
        showlegend=True,
    )
    fig.update_yaxes(title_text="Price (THB)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)

    # เปิดเบราว์เซอร์โชว์กราฟทันที
    fig.show()


if __name__ == "__main__":
    # ลูกพี่เปลี่ยนชื่อหุ้นตรงนี้เพื่อเช็คตัวอื่นได้เลย เช่น 'GULF', 'PTT'
    plot_kell_verification("SCB")
