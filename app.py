
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Recomandări comandă – ServicePack", layout="wide")

st.title("🔁 Recomandări comandă produse")
st.caption("Încarcă două fișiere SmartBill: unul cu mișcări pe tot anul și unul cu ultimele 30 de zile")

col1, col2 = st.columns(2)
with col1:
    file_all = st.file_uploader("📂 Fișier complet (anul în curs)", type=["xlsx"], key="all")
with col2:
    file_30z = st.file_uploader("📂 Fișier ultimele 30 zile", type=["xlsx"], key="recent")

def load_smartbill_df(file):
    try:
        df_raw = pd.read_excel(file, sheet_name=0, header=1)
        df = df_raw.rename(columns={
            df_raw.columns[0]: "produs",
            df_raw.columns[1]: "cod",
            df_raw.columns[3]: "stoc_initial",
            df_raw.columns[4]: "intrari",
            df_raw.columns[5]: "iesiri",
            df_raw.columns[6]: "stoc_final"
        })
        df = df[["cod", "produs", "stoc_initial", "intrari", "iesiri", "stoc_final"]]
        df["cod"] = df["cod"].astype(str).str.strip()
        for col in ["stoc_initial", "intrari", "iesiri", "stoc_final"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"❌ Eroare la citirea fișierului: {e}")
        return pd.DataFrame()

if file_all and file_30z:
    df_all = load_smartbill_df(file_all)
    df_30 = load_smartbill_df(file_30z)

    if not df_all.empty and not df_30.empty:
        st.success("✅ Ambele fișiere au fost încărcate cu succes")

        vanzari_recent = df_30.groupby("cod").agg({
            "iesiri": "sum"
        }).rename(columns={"iesiri": "vanzari_30zile"}).reset_index()

        df_all["vanzari_total"] = df_all["iesiri"]
        df = df_all.merge(vanzari_recent, on="cod", how="left")
        df["vanzari_30zile"] = df["vanzari_30zile"].fillna(0)

        st.subheader("⚙️ Setări analiză")
        col1, col2 = st.columns(2)
        with col1:
            coef_recent = st.number_input("Coeficient perioadă recentă (30 zile)", value=1.5, step=0.1)
        with col2:
            coef_total = st.number_input("Coeficient anual", value=0.2, step=0.1)

        df["recomandat_de_comandat"] = (
            df["vanzari_30zile"] * coef_recent +
            df["vanzari_total"] * coef_total -
            df["stoc_final"]
        ).clip(lower=0).round()

        df_out = df[df["recomandat_de_comandat"] > 0].sort_values("recomandat_de_comandat", ascending=False)

        st.subheader("📦 Produse recomandate la comandă")
        st.write(f"{len(df_out)} produse recomandate:")
        st.dataframe(df_out[[
            "cod", "produs", "vanzari_total", "vanzari_30zile", "stoc_final", "recomandat_de_comandat"
        ]], use_container_width=True)

        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df_out.to_excel(writer, index=False, sheet_name="recomandari")
        st.download_button("📥 Descarcă Excel", data=out.getvalue(), file_name="recomandari_comenzi.xlsx")
    else:
        st.warning("⚠️ Fișierele sunt goale sau nu s-au putut citi corect.")
