# app.py — Streamlit: Google Sheets + tvdatafeed canlı fiyatlı VWAP hedef paneli
import time
import streamlit as st
import pandas as pd
from tvDatafeed import TvDatafeed, Interval

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

@st.cache_resource(show_spinner=False)
def get_tv_client(username: str | None, password: str | None) -> TvDatafeed:
    # Kullanıcı vermediyse misafir modda dene
    if username and password:
        return TvDatafeed(username=username, password=password)
    return TvDatafeed()

def fetch_latest_prices(tv: TvDatafeed, tickers: list[str], exchange: str = "BIST") -> dict:
    """
    Her sembol için mümkünse 1 dakikalık son bar kapanışını al.
    Olmazsa günlük son kapanışa düş.
    """
    prices = {}
    progress = st.progress(0.0, text="Canlı fiyatlar çekiliyor...")
    total = len(tickers)
    for i, sym in enumerate(tickers, 1):
        px = None
        try:
            df1 = tv.get_hist(symbol=sym, exchange=exchange, interval=Interval.in_1_minute, n_bars=1)
            if df1 is not None and not df1.empty and "close" in df1.columns:
                px = float(df1["close"].iloc[-1])
        except Exception:
            px = None
        if px is None:
            try:
                dfd = tv.get_hist(symbol=sym, exchange=exchange, interval=Interval.in_daily, n_bars=1)
                if dfd is not None and not dfd.empty and "close" in dfd.columns:
                    px = float(dfd["close"].iloc[-1])
            except Exception:
                px = None
        prices[sym] = px
        progress.progress(i / total, text=f"{sym} fiyatı alınıyor...")
        # hafif gecikme; çok hızlı isteklerde throttle olabilir
        time.sleep(0.05)
    progress.empty()
    return prices

def prepare_display(raw_df: pd.DataFrame, live_prices: dict) -> pd.DataFrame:
    # Beklenen kolonlar
    col_ticker = "Ticker"
    col_vwap_try = "AVWAP (TRY)"
    col_vwap_eur = "AVWAP (EUR)"  # (TL olarak yazıldığını varsayıyoruz)

    missing = [c for c in [col_ticker, col_vwap_try, col_vwap_eur] if c not in raw_df.columns]
    if missing:
        raise KeyError(f"Beklenen kolonlar bulunamadı: {missing}")

    df = raw_df.copy()
    # sayısala çevir
    for c in [col_vwap_try, col_vwap_eur]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # canlı fiyatları ekle
    df["Hisse Fiyatı"] = df[col_ticker].map(live_prices)

    # görünüm tablosu
    out = pd.DataFrame({
        "Hisse Adı": df[col_ticker],
        "Hisse Fiyatı": df["Hisse Fiyatı"],
        "VWAP Yüzde 30 Hedef": df[col_vwap_try] / 2.0,   # talebin: VWAP TL / 2
        "VWAP TL Hedef": df[col_vwap_try],
        "VWAP EURO HEDEF": df[col_vwap_eur],             # bu sütunda TL karşılığı var
    })
    return out

def style_targets(display_df: pd.DataFrame) -> pd.io.formats.style.Styler:
    price_col = "Hisse Fiyatı"
    target_cols = ["VWAP Yüzde 30 Hedef", "VWAP TL Hedef", "VWAP EURO HEDEF"]

    df = display_df.copy()
    styles = pd.DataFrame("", index=df.index, columns=df.columns)

    # hedefe ulaşma: Hisse Fiyatı >= hedef
    for tgt in target_cols:
        # sayısal karşılaştırma, NaN güvenli
        p = pd.to_numeric(df[price_col], errors="coerce")
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
with st.sidebar:
    st.subheader("TradingView Bağlantısı (İsteğe Bağlı)")
    tv_user = st.text_input("Kullanıcı adı", value="", type="default", help="Boş bırakılırsa misafir modda denenir")
    tv_pass = st.text_input("Şifre", value="", type="password")
    login = st.button("Giriş Yap")

sheet_url = st.text_input(
    "Google Sheets URL",
    value="",
    placeholder="https://docs.google.com/spreadsheets/d/...."
)
connect = st.button("Bağlan", type="primary")

# tv client
tv = None
if login:
    tv = get_tv_client(tv_user.strip() or None, tv_pass.strip() or None)
elif "tv_client" not in st.session_state:
    # ilk kez: misafir modda deneyelim
    tv = get_tv_client(None, None)
else:
    tv = st.session_state["tv_client"]

if tv is not None:
    st.session_state["tv_client"] = tv

if connect:
    if not sheet_url.strip():
        st.error("Lütfen geçerli bir Google Sheets URL girin.")
    elif tv is None:
        st.error("TradingView istemcisi başlatılamadı.")
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

        prices = fetch_latest_prices(tv, tickers, exchange="BIST")

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
