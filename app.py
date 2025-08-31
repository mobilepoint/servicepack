
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="ServicePack – Produse, Mișcări, Recomandări (v2.1)", layout="wide")
st.title("ServicePack – Produse, Mișcări & Recomandări (v2.1)")
st.caption("Fix: separare chei Streamlit pentru upload vs. DataFrame + validări robuste.")

def to_num(s):
    return pd.to_numeric(s, errors="coerce")

def clean_cols(df):
    cols = df.columns.astype(str).str.strip().str.replace("\n"," ", regex=True).str.lower()
    df.columns = cols
    return df

def ffill_merged(df, name_cols=("nume","name","denumire","produs")):
    for c in df.columns:
        if c in name_cols or df[c].dtype==object:
            df[c] = df[c].replace(r"^\s*$", np.nan, regex=True).ffill()
    return df

def import_products_excel(file) -> pd.DataFrame:
    raw = pd.read_excel(file, sheet_name=0)
    raw = raw.loc[:, ~raw.columns.astype(str).str.contains("^Unnamed", case=False)]
    raw = clean_cols(raw)
    raw = ffill_merged(raw)
    def find_col(cands):
        for c in cands:
            if c in raw.columns: return c
        return None
    df = pd.DataFrame()
    df["name"] = raw.get(find_col(["nume","name","denumire","produs"]), pd.Series(dtype="object")).astype(str).str.strip()
    df["code"] = raw.get(find_col(["cod","sku","cod.1","product code","id"]), pd.Series(dtype="object")).astype(str).str.strip()
    df["purchase_price_no_vat"] = to_num(raw.get(find_col(["pret intrare fara tva","pret achizitie","pret achiziție fara tva","pret achiziție"]), np.nan))
    df["purchase_price_with_vat"] = to_num(raw.get(find_col(["pret intrare cu tva","pret achizitie cu tva"]), np.nan))
    df["sale_price_no_vat"] = to_num(raw.get(find_col(["pret vanzare fara tva","pret vânzare fara tva","pret vanzare fara","pret fara tva"]), np.nan))
    df["sale_price_with_vat"] = to_num(raw.get(find_col(["pret vanzare cu tva","pret vânzare cu tva","pret cu tva"]), np.nan))
    df["sale_price_site_109"] = to_num(raw.get(find_col(["pret vanzare cu tva x 1,09","x1.09","pret vanzare 1.09","pret site 1.09","x1,09"]), np.nan))
    df["profit_lei"] = to_num(raw.get(find_col(["profit in lei","profit lei","profit"]), np.nan))
    df["profit_pct"] = to_num(raw.get(find_col(["profit in procente","profit %","profit procente"]), np.nan))
    for k, aliases in {
        "gsmnet":["gsmnet","pret concurenta gsmnet","pret gsmnet"],
        "moka":["moka","pret concurenta moka","pret moka"],
        "sep":["sep","pret concurenta sep"],
        "square":["square","pret concurenta square"],
        "ecranegsm":["ecranegsm","pret concurenta ecranegsm","ecranegsm pret"],
        "distrizone":["distrizone","pret concurenta distrizone"],
    }.items():
        df[f"competitor_{k}"] = to_num(raw.get(find_col(aliases), np.nan))
    df["purchase_price_with_vat"] = np.where(df["purchase_price_with_vat"].isna() & df["purchase_price_no_vat"].notna(),
                                             df["purchase_price_no_vat"] * 1.21, df["purchase_price_with_vat"])
    df["sale_price_no_vat"] = np.where(df["sale_price_no_vat"].isna() & df["sale_price_with_vat"].notna(),
                                       df["sale_price_with_vat"] / 1.21, df["sale_price_no_vat"])
    df["sale_price_site_109"] = np.where(df["sale_price_site_109"].isna() & df["sale_price_with_vat"].notna(),
                                         df["sale_price_with_vat"] * 1.09, df["sale_price_site_109"])
    df["profit_lei"] = np.where(df["profit_lei"].isna() & df["sale_price_no_vat"].notna() & df["purchase_price_no_vat"].notna(),
                                df["sale_price_no_vat"] - df["purchase_price_no_vat"], df["profit_lei"])
    df["profit_pct"] = np.where(df["profit_pct"].isna() & df["purchase_price_no_vat"].gt(0) & df["sale_price_no_vat"].notna(),
                                (df["sale_price_no_vat"] - df["purchase_price_no_vat"]) / df["purchase_price_no_vat"] * 100, df["profit_pct"])
    df = df[df["code"].str.len() > 0].copy()
    df["grup_sku"] = np.nan
    return df

def smartbill_read(file):
    try:
        df = pd.read_excel(file, sheet_name=0, header=1)
    except Exception:
        df = pd.read_excel(file, sheet_name=0, header=0)
    df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed", case=False)]
    df = clean_cols(df)
    c_prod = None
    for name in ["produs", "denumire", "nume"]:
        if name in df.columns: c_prod = name; break
    c_cod = "cod" if "cod" in df.columns else None
    cand = {k: None for k in ["stoc_initial","intrari","iesiri","stoc_final"]}
    for col in df.columns:
        low = col
        if "stoc" in low and "initial" in low and not cand["stoc_initial"]: cand["stoc_initial"]=col
        if "intrari" in low and not cand["intrari"]: cand["intrari"]=col
        if "iesiri" in low or "ieșiri" in low:
            if not cand["iesiri"]: cand["iesiri"]=col
        if "stoc" in low and "final" in low and not cand["stoc_final"]: cand["stoc_final"]=col
    if c_prod is None and len(df.columns)>=7: c_prod = df.columns[0]
    if c_cod is None and len(df.columns)>=7: c_cod = df.columns[1]
    if cand["stoc_initial"] is None and len(df.columns)>=7: cand["stoc_initial"]=df.columns[3]
    if cand["intrari"] is None and len(df.columns)>=7: cand["intrari"]=df.columns[4]
    if cand["iesiri"] is None and len(df.columns)>=7: cand["iesiri"]=df.columns[5]
    if cand["stoc_final"] is None and len(df.columns)>=7: cand["stoc_final"]=df.columns[6]
    clean = pd.DataFrame()
    clean["cod"] = df[c_cod].astype(str).str.strip()
    clean["produs"] = df[c_prod].astype(str).str.strip()
    for k in ["stoc_initial","intrari","iesiri","stoc_final"]:
        clean[k] = to_num(df[cand[k]])
    g = clean.groupby(["cod","produs"], as_index=False).agg(
        stoc_initial=("stoc_initial","max"),
        intrari=("intrari","sum"),
        iesiri=("iesiri","sum"),
        stoc_final=("stoc_final","max")
    )
    return g

def export_excel(df: pd.DataFrame, filename: str, sheet="Sheet1"):
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet)
    st.download_button(f"📥 Descarcă {filename}", data=out.getvalue(), file_name=filename)

tabs = st.tabs(["📦 Produse", "🔁 Mișcări SmartBill", "🧩 Mapare grup_sku", "📊 Recomandări"])

with tabs[0]:
    st.subheader("Import fișier produse (Excel A..R)")
    up_prod = st.file_uploader("Încarcă Excel produse", type=["xlsx"], key="prodfile")
    if up_prod is not None:
        dfp = import_products_excel(up_prod)
        st.session_state["products_df"] = dfp
        st.success(f"Import produse: {len(dfp)} rânduri.")
        st.dataframe(dfp.head(50), use_container_width=True)
        export_excel(dfp, "master_produse_normalizat.xlsx", "produse")

with tabs[1]:
    st.subheader("Import mișcări SmartBill (an + 30 zile)")
    c1, c2 = st.columns(2)
    with c1:
        up_all_file = st.file_uploader("Anul în curs (.xlsx)", type=["xlsx"], key="moves_all_file")
    with c2:
        up_30_file = st.file_uploader("Ultimele 30 zile (.xlsx)", type=["xlsx"], key="moves_30_file")

    if up_all_file is not None:
        df_all = smartbill_read(up_all_file)
        st.session_state["moves_all_df"] = df_all
        st.success(f"An în curs: {len(df_all)} rânduri consolidate.")
        st.dataframe(df_all.head(20), use_container_width=True)
    if up_30_file is not None:
        df_30 = smartbill_read(up_30_file)
        st.session_state["moves_30_df"] = df_30
        st.success(f"Ultimele 30 zile: {len(df_30)} rânduri consolidate.")
        st.dataframe(df_30.head(20), use_container_width=True)

with tabs[2]:
    st.subheader("Mapare grup_sku = SKU din SmartBill + completare produse lipsă")
    have_prod = isinstance(st.session_state.get("products_df"), pd.DataFrame)
    have_all = isinstance(st.session_state.get("moves_all_df"), pd.DataFrame)
    if not (have_prod and have_all):
        st.info("Încarcă fișierul de produse (tab 1) și mișcările pe tot anul (tab 2).")
    else:
        dfp = st.session_state["products_df"].copy()
        m_all = st.session_state["moves_all_df"].copy()

        prod_codes = set(dfp["code"])
        missing = m_all[~m_all["cod"].isin(prod_codes)].copy()
        add_rows = pd.DataFrame()
        if not missing.empty:
            add_rows = pd.DataFrame({
                "name": missing["produs"],
                "code": missing["cod"],
                "purchase_price_no_vat": np.nan,
                "purchase_price_with_vat": np.nan,
                "sale_price_no_vat": np.nan,
                "sale_price_with_vat": np.nan,
                "sale_price_site_109": np.nan,
                "profit_lei": np.nan,
                "profit_pct": np.nan,
                "competitor_gsmnet": np.nan,
                "competitor_moka": np.nan,
                "competitor_sep": np.nan,
                "competitor_square": np.nan,
                "competitor_ecranegsm": np.nan,
                "competitor_distrizone": np.nan,
                "grup_sku": missing["cod"],
            })
            dfp = pd.concat([dfp, add_rows], ignore_index=True)

        code_to_name = dict(zip(dfp["code"], dfp["name"]))
        for _, r in m_all.iterrows():
            sb_code = r["cod"]
            if sb_code in code_to_name:
                prod_name = code_to_name[sb_code]
                dfp.loc[dfp["name"] == prod_name, "grup_sku"] = sb_code

        dfp["grup_sku"] = dfp["grup_sku"].fillna(dfp["code"])

        st.session_state["products_df"] = dfp
        st.success(f"Mapare finalizată. Adăugate {len(add_rows)} SKU-uri noi din SmartBill. Grupuri setate pe nume comun.")
        st.dataframe(dfp.head(50), use_container_width=True)
        export_excel(dfp, "master_actualizat_cu_grupuri.xlsx", "produse")

with tabs[3]:
    st.subheader("Recomandări pe grup_sku")
    have_prod = isinstance(st.session_state.get("products_df"), pd.DataFrame)
    have_all = isinstance(st.session_state.get("moves_all_df"), pd.DataFrame)
    have_30 = isinstance(st.session_state.get("moves_30_df"), pd.DataFrame)
    if not (have_prod and have_all and have_30):
        st.info("Încarcă toate fișierele și rulează maparea din tabul anterior.")
    else:
        dfp = st.session_state["products_df"].copy()
        m_all = st.session_state["moves_all_df"].copy()
        m_30 = st.session_state["moves_30_df"].copy()

        code_to_group = dfp.set_index("code")["grup_sku"].to_dict()
        m_all["grup_sku"] = m_all["cod"].map(code_to_group)
        m_30["grup_sku"] = m_30["cod"].map(code_to_group)

        m_all = m_all.dropna(subset=["grup_sku"])
        m_30 = m_30.dropna(subset=["grup_sku"])

        g_all = m_all.groupby("grup_sku", as_index=False).agg(
            vanzari_total=("iesiri","sum"),
            stoc_final=("stoc_final","max"),
            produs_smartbill=("produs", lambda s: s.mode().iat[0] if len(s)>0 else ""),
        )
        g_30 = m_30.groupby("grup_sku", as_index=False).agg(vanzari_30zile=("iesiri","sum"))

        group_skus = dfp.groupby("grup_sku").apply(lambda d: ", ".join(sorted(set(d["code"].astype(str))))).rename("skus_in_group").reset_index()
        cheapest = dfp.sort_values(["grup_sku","purchase_price_no_vat"]).groupby("grup_sku").first().reset_index()
        cheapest = cheapest[["grup_sku","code","purchase_price_no_vat"]].rename(columns={"code":"cheapest_sku","purchase_price_no_vat":"cheapest_purchase_price_no_vat"})

        name_lookup = dfp.set_index("code")["name"].to_dict()

        rep = g_all.merge(g_30, on="grup_sku", how="left").merge(group_skus, on="grup_sku", how="left").merge(cheapest, on="grup_sku", how="left")
        rep["vanzari_30zile"] = rep["vanzari_30zile"].fillna(0)
        rep["product_name"] = rep["grup_sku"].map(name_lookup).fillna(rep["produs_smartbill"])

        c1, c2 = st.columns(2)
        with c1:
            coef_recent = st.number_input("Coeficient 30 zile", value=1.5, step=0.1)
        with c2:
            coef_total = st.number_input("Coeficient anual", value=0.2, step=0.1)

        rep["recomandat_de_comandat"] = (
            rep["vanzari_30zile"] * coef_recent +
            rep["vanzari_total"] * coef_total -
            rep["stoc_final"]
        ).clip(lower=0).round()

        rep = rep.sort_values("recomandat_de_comandat", ascending=False)

        st.dataframe(rep[["grup_sku","product_name","skus_in_group","stoc_final","vanzari_30zile","vanzari_total","recomandat_de_comandat","cheapest_sku","cheapest_purchase_price_no_vat"]], use_container_width=True)

        export_excel(rep, "recomandari_comenzi_pe_grup.xlsx", "recomandari")
