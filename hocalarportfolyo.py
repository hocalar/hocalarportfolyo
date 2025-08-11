# app.py — Streamlit: Google Sheets + yfinance canlı fiyatlı VWAP hedef paneli
import time
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

st.set_page_config(page_title="Hocalar Portfolyo", layout="wide")
st.title("Hocalar Hisse Portfolyo Takip Uygulaması")

# ---------------- Helpers ----------------
def convert_to_csv_url(sheet_url: str) -> str:
    try:
        parts = sheet_url.split("/d/")
        if len(parts) < 2:
            return ""
        tail = parts[1]
        sheet_id = tail.split("/")[0]
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    except Exception:
        return ""

@st.cache_data(show_spinner=False)
def load_sheet_as_df(sheet_url: str) -> pd.DataFrame:
    csv_url = convert_to_csv_url(sheet_url)
    if not csv_url:
        raise ValueError("Geçersiz Google Sheets URL")
    df = pd.read_csv(csv_url)
    df.columns = [str(c).strip() for c in df.columns]
    return df

def to_yahoo_symbol(bist_code: str) -> str:
    # Basit eşleme: BIST → .IS
    code = (bist_code or "").strip().upper()
    if not code:
        return ""
    if code.endswith(".IS"):
        return code
    return f"{code}.IS"

def fetch_price_yf(symbol: str) -> float | None:
    """
    Önce fast_info.last_price, olmazsa 1dk bar, o da olmazsa günlük kapanışa düş.
    """
    try:
        tk = yf.Ticker(symbol)
        # 1) fast_info
        lp = None
        try:
            fi = tk.fast_info
            lp = fi.get("last_price", None)
            if lp is not None and np.isfinite(lp):
                return float(lp)
        except Exception:
            pass

        # 2) 1 dakikalık son bar
        try:
            m1 = tk.history(period="1d", interval="1m")
            if not m1.empty and "Close" in m1.columns:
                val = m1["Close"].dropna().iloc[-1]
                if np.isfinite(val):
                    return float(val)
        except Exception:
            pass

        # 3) günlük kapanış
        try:
            d1 = tk.history(period="5d", interval="1d")
            if not d1.empty and "Close" in d1.columns:
                val = d1["Close"].dropna().iloc[-1]
                if np.isfinite(val):
                    return float(val)
        except Exception:
            pass
    except Exception:
        return None
    return None

def fetch_latest_prices_yf(bist_tickers: list[str]) -> dict:
    prices = {}
    progress = st.progress(0.0, text="Canlı fiyatlar çekiliyor...")
    total = len(bist_tickers)
    for i, code in enumerate(bist_tickers, 1):
        sym = to_yahoo_symbol(code)
        px = fetch_price_yf(sym) if sym else None
        prices[code] = px
        progress.progress(i / total, text=f"{code} fiyatı alınıyor...")
        time.sleep(0.02)  # nazikçe
    progress.empty()
    return prices

def prepare_display(raw_df: pd.DataFrame, live_prices: dict) -> pd.DataFrame:
    # Beklenen kolonlar
    col_ticker   = "Ticker"
    col_vwap_try = "AVWAP (TRY)"
    col_vwap_eur = "AVWAP (EUR)"  # (TL karşılığı yazıldığını varsayıyoruz)

    missing = [c for c in [col_ticker, col_vwap_try, col_vwap_eur] if c not in raw_df.columns]
    if missing:
        raise KeyError(f"Beklenen kolonlar bulunamadı: {missing}")

    df = raw_df.copy()
    # sayısala çevir
    for c in [col_vwap_try, col_vwap_eur]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # canlı fiyatları ekle
    df["Hisse Fiyatı"] = df[col_ticker].map(live_prices)

    out = pd.DataFrame({
        "Hisse Adı": df[col_ticker],
        "Hisse Fiyatı": df["Hisse Fiyatı"],
        "VWAP Yüzde 30 Hedef": df[col_vwap_try] / 2.0,   # talebin: VWAP TL / 2
        "VWAP TL Hedef": df[col_vwap_try],
        "VWAP EURO HEDEF": df[col_vwap_eur],             # bu sütunda TL karşılığı var (senin hesaplama koduna göre)
    })
    return out

def style_targets(display_df: pd.DataFrame) -> pd.io.formats.style.Styler:
    price_col = "Hisse Fiyatı"
    target_cols = ["VWAP Yüzde 30 Hedef", "VWAP TL Hedef", "VWAP EURO HEDEF"]

    df = display_df.copy()
    styles = pd.DataFrame("", index=df.index, columns=df.columns)

    # hedefe ulaşma: Hisse Fiyatı >= hedef
    p = pd.to_numeric(df[price_col], errors="coerce")
    for tgt in target_cols:
        h = pd.to_numeric(df[tgt], errors="coerce")
        mask = (p.notna() & h.notna() & (p >= h))
        styles.loc[mask, tgt] = "background-color: #d9f7e3"  # açık yeşil

    styler = df.style.format({
        "Hisse Fiyatı": "{:,.2f}",
        "VWAP Yüzde 30 Hedef": "{:,.2f}",
        "VWAP TL Hedef": "{:,.2f}",
        "VWAP EURO HEDEF": "{:,.2f}",
    }).set_properties(subset=target_cols, **{"border": "1px solid #eee"}) \
     .set_table_styles([
        {"selector": "th", "props": [("text-align", "left")]},
        {"selector": "td", "props": [("text-align", "right")]},
        {"selector": "th.col_heading.level0", "props": [("text-align", "left")]}
     ])
    styler = styler.set_td_classes(styles)
    return styler

# ---------------- UI ----------------
sheet_url = st.text_input(
    "Google Sheets URL",
    value="",
    placeholder="https://docs.google.com/spreadsheets/d/...."
)
connect = st.button("Bağlan", type="primary")

if connect:
    if not sheet_url.strip():
        st.error("Lütfen geçerli bir Google Sheets URL girin.")
    else:
        with st.spinner("Sheet okunuyor..."):
            try:
                raw_df = load_sheet_as_df(sheet_url.strip())
            except Exception as e:
                st.error(f"Sheet yüklenemedi: {e}")
                st.stop()

        # gerekli kolonlar var mı kontrol
        needed_cols = ["Ticker", "AVWAP (TRY)", "AVWAP (EUR)"]
        miss = [c for c in needed_cols if c not in raw_df.columns]
        if miss:
            st.error(f"Sheet’te beklenen kolonlar yok: {miss}")
            st.stop()

        # canlı fiyatlar
        tickers = raw_df["Ticker"].astype(str).dropna().unique().tolist()
        if not tickers:
            st.warning("Sheet’te Ticker bulunamadı.")
            st.stop()

        prices = fetch_latest_prices_yf(tickers)

        # tabloyu kur
        try:
            display_df = prepare_display(raw_df, prices)
        except Exception as e:
            st.error(f"Kolon eşleme/hesaplama hatası: {e}")
            st.stop()

        st.success("Veri yüklendi ✓")
        st.caption("Hedefe ulaşan **hedef hücreleri** açık yeşil renkte vurgulanır.")
        styler = style_targets(display_df)
        st.dataframe(styler, use_container_width=True)
