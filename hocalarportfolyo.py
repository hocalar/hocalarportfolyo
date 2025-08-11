# app.py — Streamlit: Google Sheets + yfinance (Python 3.13 uyumlu, esnek başlık eşleşmesi)
from typing import TYPE_CHECKING
import io, re
import requests
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

if TYPE_CHECKING:
    from pandas.io.formats.style import Styler  # sadece type-check için

st.set_page_config(page_title="Hocalar Portföy", layout="wide")
st.title("Hocalar Portföy")

# ----------------- 0) URL + bağlan (kalıcı state) -----------------
if "connected" not in st.session_state:
    st.session_state.connected = False

sheet_url = st.text_input(
    "Google Sheets URL",
    value=st.session_state.get("sheet_url", ""),
    placeholder="https://docs.google.com/spreadsheets/d/....",
    key="sheet_url_input"
)

if st.button("Bağlan", type="primary"):
    st.session_state.connected = True
    st.session_state.sheet_url = sheet_url.strip()

if not st.session_state.connected:
    st.info("Lütfen Google Sheets URL’sini girip **Bağlan**’a tıklayın.")
    st.stop()

if not st.session_state.get("sheet_url"):
    st.error("Geçerli bir Google Sheets URL girin.")
    st.stop()

# ----------------- Helpers -----------------
def convert_to_csv_url(sheet_url: str) -> str:
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

def _normalize_cols(cols):
    norm = []
    for c in cols:
        s = str(c).replace("\u00A0", " ")           # NBSP -> normal boşluk
        s = re.sub(r"\s+", " ", s)                  # çoklu boşlukları tek boşluk
        s = re.sub(r"\(\s+", "(", s)                # '(' sonrası boşlukları sil
        s = re.sub(r"\s+\)", ")", s)                # ')' öncesi boşlukları sil
        s = s.strip()
        norm.append(s)
    return norm

def _to_float_series_tr(s: pd.Series) -> pd.Series:
    # str'e çevir, gizli boşlukları ve para/işaret metinlerini temizle
    s = (s.astype(str)
           .str.replace("\u00A0", " ", regex=False)
           .str.strip()
           .str.replace(r"[^\d,.\-]", "", regex=True))  # sadece 0-9 . , - kalsın

    def _one(v: str) -> float | None:
        if v == "" or v == "-" or v == "." or v == ",":
            return np.nan
        # Hem nokta hem virgül varsa: son görüneni ondalık say
        if "," in v and "." in v:
            if v.rfind(",") > v.rfind("."):  # TR tarzı: 1.234,56
                v = v.replace(".", "").replace(",", ".")
            else:                            # US tarzı: 1,234.56
                v = v.replace(",", "")
        elif "," in v and "." not in v:      # Sadece virgül: 123,45 -> 123.45
            v = v.replace(",", ".")
        else:                                # Sadece nokta ya da düz rakam
            v = v
        try:
            return float(v)
        except Exception:
            return np.nan

    return s.map(_one)
    
@st.cache_data(show_spinner=False, ttl=300)
def load_sheet_as_df(sheet_url: str, timeout: float = 15.0) -> pd.DataFrame:
    csv_url = convert_to_csv_url(sheet_url)
    if not csv_url:
        raise ValueError("Geçersiz Google Sheets URL")
    r = requests.get(csv_url, timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = _normalize_cols(df.columns)
    return df

def to_yahoo_symbol(bist_code: str) -> str:
    code = (bist_code or "").strip().upper()
    if not code:
        return ""
    return code if code.endswith(".IS") else f"{code}.IS"

@st.cache_data(show_spinner=False, ttl=60)
def download_prices_batch(bist_tickers: list[str]) -> dict:
    prices: dict[str, float | None] = {t: None for t in bist_tickers}
    symbols = [to_yahoo_symbol(t) for t in bist_tickers if t]

    # 1) 1m toplu
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

    # 2) 1D kapanış toplu (eksikler)
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

    # 3) fast_info tek tek
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

# --- Esnek sütun bulucu (regex ile) ---
def find_col(cols, patterns: list[str]) -> str | None:
    for p in patterns:
        rx = re.compile(p, flags=re.IGNORECASE)
        for c in cols:
            if rx.fullmatch(c):
                return c
    return None

def prepare_display(raw_df: pd.DataFrame, live_prices: dict) -> pd.DataFrame:
    """
    Zorunlu: 'Ticker', 'AVWAP HEDEF+4 (TRY)', 'AVWAP HEDEF+4 (EUR)'
    """
    cols = list(raw_df.columns)

    # Zorunlu kolon kontrolü
    required_cols = ["Ticker", "AVWAP HEDEF+4 (TRY)", "AVWAP HEDEF+4 (EUR)"]
    missing = [c for c in required_cols if c not in cols]
    if missing:
        raise KeyError(f"Beklenen kolonlar bulunamadı: {missing}")

    df = raw_df.copy()
    df["AVWAP HEDEF+4 (TRY)"] = _to_float_series_tr(df["AVWAP HEDEF+4 (TRY)"])
    df["AVWAP HEDEF+4 (EUR)"] = _to_float_series_tr(df["AVWAP HEDEF+4 (EUR)"])
    #df["AVWAP HEDEF+4 (TRY)"] = pd.to_numeric(df["AVWAP HEDEF+4 (TRY)"], errors="coerce")
    #df["AVWAP HEDEF+4 (EUR)"] = pd.to_numeric(df["AVWAP HEDEF+4 (EUR)"], errors="coerce")
    df["Hisse Fiyatı"] = df["Ticker"].map(live_prices)

    out = pd.DataFrame({
        "Hisse Adı": df["Ticker"],
        "Hisse Fiyatı": df["Hisse Fiyatı"],
        "VWAP Yüzde 30 Hedef": df["AVWAP HEDEF+4 (TRY)"] / 2.0,
        "VWAP TL Hedef": df["AVWAP HEDEF+4 (TRY)"],
        "VWAP EURO HEDEF": df["AVWAP HEDEF+4 (EUR)"],
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

# ----------------- 1) Sheet'i oku -----------------
with st.spinner("Sheet okunuyor..."):
    try:
        raw_df = load_sheet_as_df(st.session_state.sheet_url)
    except Exception as e:
        st.error(f"Sheet yüklenemedi: {e}")
        st.stop()

# ----------------- 2) Hisseler listesi -----------------
if "Ticker" not in raw_df.columns:
    st.error("Sheet’te 'Ticker' kolonunu bulamadım.")
    st.stop()

all_tickers = (raw_df["Ticker"].astype(str).dropna().drop_duplicates().tolist())
if not all_tickers:
    st.warning("Sheet’te Ticker bulunamadı.")
    st.stop()

# ----------------- 3) Sidebar: çoklu seçim -----------------
with st.sidebar:
    st.subheader("Hisse seçimi")
    options = sorted(all_tickers)
    selected = st.multiselect(
        "Hisse seç (çoklu):",
        options=options,
        default=options,
        help="Boş bırakırsanız tüm hisseler gösterilir."
    )

tickers = selected if selected else options  # boşsa hepsi

# ----------------- 4) Fiyat indir + tablo -----------------
with st.spinner("Canlı fiyatlar indiriliyor..."):
    prices = download_prices_batch(tickers)

try:
    filtered_df = raw_df[raw_df["Ticker"].astype(str).isin(tickers)].copy()
    display_df = prepare_display(filtered_df, prices)
except Exception as e:
    st.error(f"Kolon eşleme/hesaplama hatası: {e}")
    st.stop()

st.success(f"Veri yüklendi ✓  (Toplam {len(display_df)} hisse)")
st.caption("Hedefe ulaşan **hücreler** açık yeşil renkte vurgulanır.")
styler = style_targets(display_df)
st.dataframe(styler, use_container_width=True)
