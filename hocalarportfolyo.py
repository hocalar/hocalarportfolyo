import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(layout="wide")
st.title("Hocalar Portfolyo")

# 1️⃣ URL input box en başta
sheet_url = st.text_input(
    "Google Sheets URL",
    value="",
    placeholder="https://docs.google.com/spreadsheets/d/...."
)
connect = st.button("Bağlan", type="primary")

# 2️⃣ URL yoksa kodu durdur
if not connect or not sheet_url.strip():
    st.warning("Lütfen Google Sheets URL'sini girin ve 'Bağlan' butonuna tıklayın.")
    st.stop()

# Buradan sonrası sadece bağlantı kurulunca çalışır
def convert_edit_url_to_csv(url):
    return url.split("/edit")[0] + "/export?format=csv"

try:
    df = pd.read_csv(convert_edit_url_to_csv(sheet_url))
except Exception as e:
    st.error(f"Google Sheets verisi alınamadı: {e}")
    st.stop()

# Hisse fiyatlarını yfinance üzerinden çekelim
if 'Ticker' in df.columns:
    tickers = [f"{t}.IS" for t in df['Ticker'].dropna()]
    fiyatlar = {}
    for t in tickers:
        try:
            fiyatlar[t] = yf.Ticker(t).history(period="1d")['Close'].iloc[-1]
        except:
            fiyatlar[t] = None
    df['Hisse Fiyatı'] = [fiyatlar.get(f"{t}.IS") for t in df['Ticker']]

# VWAP %30 hedef
df['VWAP %30 Hedef'] = df['VWAP TL Hedef'] / 2

# Renk koşulları
def renk_ver(val, fiyat):
    if pd.notna(val) and pd.notna(fiyat) and fiyat >= val:
        return 'background-color: lightgreen'
    return ''

df_style = df.style.apply(
    lambda row: [
        renk_ver(row['VWAP %30 Hedef'], row['Hisse Fiyatı']),
        renk_ver(row['VWAP TL Hedef'], row['Hisse Fiyatı']),
        renk_ver(row['VWAP EURO HEDEF'], row['Hisse Fiyatı'])
    ] + [''] * (len(row) - 4),
    axis=1
)

st.dataframe(df_style, use_container_width=True)
