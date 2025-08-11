# app.py — Streamlit: Google Sheets + yfinance (Python 3.13 uyumlu)
from typing import TYPE_CHECKING
import io
import requests
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

if TYPE_CHECKING:
    from pandas.io.formats.style import Styler  # sadece type-check için

st.set_page_config(page_title="VWAP Hedef Kontrol", layout="wide")
st.title("VWAP Hedef Kontrol Paneli (Google Sheets + yfinance)")

# --------- 0) EN BAŞTA: URL input + bağlan ----------
sheet_url = st.text_input(
    "Google Sheets URL",
    value="",
    placeholder="https://docs.google.com/spreadsheets/d/...."
)
connect = st.button("Bağlan", type="primary")

if not connect:
    st.info("Lütfen Google Sheets URL’sini girip **Bağlan**’a tıklayın.")
    st.stop()
if not sheet_url.strip():
    st.error("Geçerli bir Google Sheets URL girin.")
    st.stop()

# ================== Helpers ==================
def convert_to_csv_url(sheet_url: str) -> str:
    """Google Sheets edit URL -> CSV export URL (gid varsa taşır)."""
    try:
        parts = sheet_url.split("/d/")
        if len(parts) < 2:
            return ""
        tail = parts[1]
        sheet_id = tail.split("/")[0]
        gid = None
        if "gid=" in sheet_url:
            try:
                gid = sheet_url.split("gid=")[1].split("&")[0]
            except Exception:
                gid = None
        base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        if gid:
            base += f"&gid={gid}"
        return base
    except Exception:
        return ""

@st.cache_data(show_spinner=False, ttl=300)
def load_sheet_as_df(sheet_url: str, timeout: float = 15.0) -> pd.DataFrame:
    """CSV'yi requests ile indirir ve DataFrame'e yükler."""
    csv_url = convert_to_csv_url(sheet_url)
    if not csv_url:
        raise ValueError("Geçersiz Google Sheets URL")
    r = requests.get(csv_url, timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [str(c).strip() for c in df.columns]
    return df

def to_yahoo_symbol(bist_code: str) -> str:
    code = (bist_code or "").strip().upper()
    if not code:
        return ""
    return code if code.endswith(".IS") else f"{code}.IS"

@st.cache_data(show_spinner=False, ttl=60)
def download_prices_batch(bist_tickers: list[str]) -> dict:
    """yfinance ile hızlı fiyat indirme (1m toplu → 1d toplu → fast_info tek tek)."""
    prices: dict[str, float | None] = {t: None for t in bist_tickers}
    symbols = [to_yahoo_symbol(t) for t in bist_tickers if t]

    # 1) 1m son bar — toplu
    try:
        df_m1 = yf.download(
            tickers=symbols, period="1d", interval="1m",
            group_by="ticker", threads=True, auto_adjust=False, progress=False,
        )
        if isinstance(df_m1.columns, pd.MultiIndex):
            for bist, sym in zip(bist_tickers, symbols):
                try:
                    sub = df_m1[sym]
                    val = sub["Close"].dropna().iloc[-1]
                    if np.isfinite(val):
                        prices[bist] = float(val)
                except Exception:
                    pass
        elif isinstance(df_m1, pd.DataFrame) and not df_m1.empty and len(bist_tickers) == 1:
            try:
                val = df_m1["Close"].dropna().iloc[-1]
                prices[bist_tickers[0]] = float(val)
            except Exception:
                pass
    except Exception:
        pass

    # 2) 1D kapanış — toplu (eksikler için)
    missing = [b for b, px in prices.items() if px is None]
    if missing:
        try:
            sym_mis = [to_yahoo_symbol(b) for b in missing]
            df_d1 = yf.download(
                tickers=sym_mis, period="5d", interval="1d",
                group_by="ticker", threads=True, auto_adjust=False, progress=False,
            )
            if isinstance(df_d1.columns, pd.MultiIndex):
                for bist, sym in zip(missing, sym_mis):
                    try:
                        sub = df_d1[sym]
                        val = sub["Close"].dropna().iloc[-1]
                        if np.isfinite(val):
                            prices[bist] = float(val)
                    except Exception:
                        pass
            elif isinstance(df_d1, pd.DataFrame) and not df_d1.empty and len(missing) == 1:
                try:
                    val = df_d1["Close"].dropna().iloc[-1]
                    if np.isfinite(val):
                        prices[missing[0]] = float(val)
                except Exception:
                    pass
        except Exception:
            pass

    # 3) fast_info — tek tek
    still = [b for b, px in prices.items() if px is None]
    for bist in still:
        sym = to_yahoo_symbol(bist)
        try:
            tk = yf.Ticker(sym)
            lp = None
            try:
                fi = tk.fast_info
                lp = fi.get("last_price", None)
            except Exception:
                lp = None
            if lp is None:
                hist = tk.history(period="5d", interval="1d")
                if not hist.empty and "Close" in hist.columns:
                    lp = hist["Close"].dropna().iloc[-1]
            if lp is not None and np.isfinite(lp):
                prices[bist] = float(lp)
        except Exception:
            prices[bist] = None

    return prices

def prepare_display(raw_df: pd.DataFrame, live_prices: dict) -> pd.DataFrame:
    """
    Beklenen kolonlar: Ticker, AVWAP (TRY), AVWAP (EUR)
    Çıktı: Hisse Adı, Hisse Fiyatı, VWAP Yüzde 30 Hedef, VWAP TL Hedef, VWAP EURO HEDEF
    """
    col_ticker   = "Ticker"
    col_vwap_try = "AVWAP (TRY)"
    col_vwap_eur = "AVWAP (EUR)"  # TL karşılığı yazıldığı varsayımı

    missing = [c for c in [col_ticker, col_vwap_try, col_vwap_eur] if c not in raw_df.columns]
    if missing:
        raise KeyError(f"Beklenen kolonlar bulunamadı: {missing}")

    df = raw_df.copy()
    for c in [col_vwap_try, col_vwap_eur]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Hisse Fiyatı"] = df[col_ticker].map(live_prices)

    out = pd.DataFrame({
        "Hisse Adı": df[col_ticker],
        "Hisse Fiyatı": df["Hisse Fiyatı"],
        "VWAP Yüzde 30 Hedef": df[col_vwap_try] / 2.0,   # istek: VWAP TL / 2
        "VWAP TL Hedef": df[col_vwap_try],
        "VWAP EURO HEDEF": df[col_vwap_eur],
    })
    return out

def style_targets(display_df: pd.DataFrame) -> "Styler":
    price_col = "Hisse Fiyatı"
    target_cols = ["VWAP Yüzde 30 Hedef", "VWAP TL Hedef", "VWAP EURO HEDEF"]
    df = display_df.copy()

    def _row_style(row: pd.Series):
        styles = []
        price = pd.to_numeric(row.get(price_col), errors="coerce")
        for col in df.columns:
            if col in target_cols:
                tgt = pd.to_numeric(row.get(col), errors="coerce")
                if pd.notna(price) and pd.notna(tgt) and price >= tgt:
                    styles.append("background-color: #d9f7e3")
                else:
                    styles.append("")
            else:
                styles.append("")
        return styles

    styler = (
        df.style
          .format({
              "Hisse Fiyatı": "{:,.2f}",
              "VWAP Yüzde 30 Hedef": "{:,.2f}",
              "VWAP TL Hedef": "{:,.2f}",
              "VWAP EURO HEDEF": "{:,.2f}",
          })
          .set_properties(subset=target_cols, **{"border": "1px solid #eee"})
          .set_table_styles([
              {"selector": "th", "props": [("text-align", "left")]},
              {"selector": "td", "props": [("text-align", "right")]},
              {"selector": "th.col_heading.level0", "props": [("text-align", "left")]},
          ])
          .apply(lambda r: _row_style(r), axis=1)
    )
    return styler

# --------- 1) Sidebar ve diğer UI şimdi geliyor ----------
with st.sidebar:
    st.subheader("Seçenekler")
    max_rows = st.number_input(
        "En fazla kaç hisse oku?",
        min_value=5, max_value=300, value=80, step=5,
        help="Çok fazla hisse yavaşlatabilir. Gerekirse düşürün."
    )

# --------- 2) İş akışı ----------
with st.spinner("Sheet okunuyor..."):
    try:
        raw_df = load_sheet_as_df(sheet_url.strip())
    except Exception as e:
        st.error(f"Sheet yüklenemedi: {e}")
        st.stop()

needed_cols = ["Ticker", "AVWAP (TRY)", "AVWAP (EUR)"]
miss = [c for c in needed_cols if c not in raw_df.columns]
if miss:
    st.error(f"Sheet’te beklenen kolonlar yok: {miss}")
    st.stop()

tickers = (raw_df["Ticker"].astype(str)
           .dropna()
           .drop_duplicates()
           .head(int(max_rows))
           .tolist())
if not tickers:
    st.warning("Sheet’te Ticker bulunamadı.")
    st.stop()

with st.spinner("Canlı fiyatlar indiriliyor..."):
    prices = download_prices_batch(tickers)

try:
    display_df = prepare_display(raw_df, prices)
except Exception as e:
    st.error(f"Kolon eşleme/hesaplama hatası: {e}")
    st.stop()

st.success("Veri yüklendi ✓")
st.caption("Hedefe ulaşan **hedef hücreleri** açık yeşil renkte vurgulanır.")
styler = style_targets(display_df)
st.dataframe(styler, use_container_width=True)
