
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="ServicePack – Produse, Mișcări, Recomandări", layout="wide")
st.title("ServicePack – Produse, Mișcări & Recomandări")
st.caption("MVP complet: import produse (cu grup_sku), import mișcări SmartBill (an + 30 zile) și recomandări de comandă.")

# -----------------------------
# Helpers
# -----------------------------

PRODUCT_ALIASES = {
    "code": ["cod", "sku", "product code", "id", "cod.1"],
    "name": ["nume", "denumire", "produs", "name", "title"],
    "purchase_price": ["pret achizitie", "purchase price", "buy price", "cost"],
    "sale_price": ["pret vanzare", "price", "sale price", "selling price"],
    "profit_abs": ["profit"],
    "sale_price_minus20": ["pret vanzare -20%"],
    "profit_minus20": ["profit -20%"],
    "competitor_price": ["concurenta", "pret concurenta", "competitor price"],
    "grup_sku": ["grup_sku", "grup sku", "sku grup", "grup"]
}

def _lower_clean_cols(df):
    cols = df.columns.astype(str).str.strip()
    cols = cols.str.replace("\n", " ", regex=True)
    return cols.str.lower()

def normalize_products(df: pd.DataFrame) -> pd.DataFrame:
    # Drop unnamed
    df = df.loc[:, ~df.columns.astype(str).str.contains("^unnamed", case=False)]
    df.columns = _lower_clean_cols(df)

    # Build normalized dataframe
    out = pd.DataFrame()
    def first_match(*keys):
        for k in keys:
            for c in PRODUCT_ALIASES[k]:
                if c in df.columns:
                    return c
        return None

    m_code = first_match("code")
    m_name = first_match("name")
    m_pur  = first_match("purchase_price")
    m_sale = first_match("sale_price")
    m_prof = first_match("profit_abs")
    m_sale20 = first_match("sale_price_minus20")
    m_prof20 = first_match("profit_minus20")
    m_comp = first_match("competitor_price")
    m_group = first_match("grup_sku")

    out["code"] = df.get(m_code, pd.Series(dtype="object")).astype(str).str.strip()
    out["name"] = df.get(m_name, pd.Series(dtype="object")).astype(str).str.strip()
    out["purchase_price"] = pd.to_numeric(df.get(m_pur, pd.Series(dtype="float")), errors="coerce")
    out["sale_price"] = pd.to_numeric(df.get(m_sale, pd.Series(dtype="float")), errors="coerce")
    out["profit_abs"] = pd.to_numeric(df.get(m_prof, pd.Series(dtype="float")), errors="coerce")
    out["sale_price_minus20"] = pd.to_numeric(df.get(m_sale20, pd.Series(dtype="float")), errors="coerce")
    out["profit_minus20"] = pd.to_numeric(df.get(m_prof20, pd.Series(dtype="float")), errors="coerce")
    out["competitor_price"] = pd.to_numeric(df.get(m_comp, pd.Series(dtype="float")), errors="coerce")

    # grup_sku: must exist; if missing, default to code
    if m_group and m_group in df.columns:
        out["grup_sku"] = df[m_group].astype(str).str.strip().replace({"": np.nan})
    else:
        out["grup_sku"] = np.nan
    out["grup_sku"] = out["grup_sku"].fillna(out["code"])

    # drop rows without code
    out = out[out["code"].str.len() > 0]
    return out

def smartbill_read(file):
    # primary attempt: header on row 1 (index)
    try:
        df = pd.read_excel(file, sheet_name=0, header=1)
    except Exception:
        df = pd.read_excel(file, sheet_name=0, header=0)
    df_cols = _lower_clean_cols(df)
    df.columns = df_cols

    # We expect something like: produs|cod|...|stoc initial|intrari|iesiri|stoc final
    # Find by position if necessary
    # Prefer exact known names
    c_prod = None
    for name in ["produs", "denumire", "nume"]:
        if name in df.columns: c_prod = name; break
    c_cod = "cod" if "cod" in df.columns else None

    # numeric columns detection by proximity / keywords
    candidates = {k: None for k in ["stoc_initial","intrari","iesiri","stoc_final"]}
    for col in df.columns:
        low = col.lower()
        if "stoc" in low and "initial" in low and not candidates["stoc_initial"]:
            candidates["stoc_initial"] = col
        if "intrari" in low and not candidates["intrari"]:
            candidates["intrari"] = col
        if ("iesiri" in low or "ieșiri" in low) and not candidates["iesiri"]:
            candidates["iesiri"] = col
        if "stoc" in low and "final" in low and not candidates["stoc_final"]:
            candidates["stoc_final"] = col

    # Fallback by positional indices as seen in your sample export
    if c_prod is None and len(df.columns) >= 7:
        c_prod = df.columns[0]
    if c_cod is None and len(df.columns) >= 7:
        c_cod = df.columns[1]
    if candidates["stoc_initial"] is None and len(df.columns) >= 7:
        candidates["stoc_initial"] = df.columns[3]
    if candidates["intrari"] is None and len(df.columns) >= 7:
        candidates["intrari"] = df.columns[4]
    if candidates["iesiri"] is None and len(df.columns) >= 7:
        candidates["iesiri"] = df.columns[5]
    if candidates["stoc_final"] is None and len(df.columns) >= 7:
        candidates["stoc_final"] = df.columns[6]

    clean = pd.DataFrame()
    clean["cod"] = df[c_cod].astype(str).str.strip()
    clean["produs"] = df[c_prod].astype(str).str.strip()
    for k in ["stoc_initial","intrari","iesiri","stoc_final"]:
        col = candidates[k]
        clean[k] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # group at code level
    g = clean.groupby(["cod","produs"], as_index=False).agg(
        stoc_initial=("stoc_initial","max"),
        intrari=("intrari","sum"),
        iesiri=("iesiri","sum"),
        stoc_final=("stoc_final","max")
    )
    return g

def list_missing_skus(moves_df, products_df):
    if products_df is None or products_df.empty:
        return moves_df.assign(reason="products file empty")
    prod_codes = set(products_df["code"].astype(str))
    miss = moves_df[~moves_df["cod"].astype(str).isin(prod_codes)].copy()
    return miss

# -----------------------------
# Tabs
# -----------------------------
tabs = st.tabs(["📦 Produse", "🔁 Mișcări stoc", "📊 Recomandări & Export"])

with tabs[0]:
    st.subheader("Import fișier produse (site) – include coloana 'grup_sku'")
    st.caption("• Acceptă XLSX/CSV. • Dacă 'grup_sku' lipsește, îl setăm temporar egal cu 'code'. Doar codurile din SmartBill trebuie trecute ca 'grup_sku' pentru gruparea corectă.")

    up_prod = st.file_uploader("Încarcă fișier produse", type=["xlsx","csv"], key="prod")
    if up_prod is not None:
        try:
            if up_prod.name.endswith(".xlsx"):
                dfp_raw = pd.read_excel(up_prod, sheet_name=0)
            else:
                dfp_raw = pd.read_csv(up_prod)
            dfp = normalize_products(dfp_raw)
            st.session_state["products_df"] = dfp
            st.success(f"Import produse: {len(dfp)} rânduri.")
            st.dataframe(dfp.head(50), use_container_width=True)
        except Exception as e:
            st.error(f"Eroare la import produse: {e}")

    if "products_df" in st.session_state:
        dfp = st.session_state["products_df"]
        # sumar pe grupuri
        groups = dfp.groupby("grup_sku").agg(
            skus=("code", lambda s: ", ".join(sorted(set(map(str,s))))),
            num_skus=("code","nunique"),
            cheapest_purchase=("purchase_price","min"),
        ).reset_index().rename(columns={"grup_sku":"grup"})
        st.markdown("**Grupuri detectate (grup_sku)**")
        st.dataframe(groups, use_container_width=True)

with tabs[1]:
    st.subheader("Import mișcări SmartBill")
    c1, c2 = st.columns(2)
    with c1:
        up_all = st.file_uploader("Fișier mișcări – tot anul", type=["xlsx"], key="all")
    with c2:
        up_30 = st.file_uploader("Fișier mișcări – ultimele 30 zile", type=["xlsx"], key="last30")

    if up_all is not None:
        try:
            df_all = smartbill_read(up_all)
            st.session_state["moves_all"] = df_all
            st.success(f"An în curs: {len(df_all)} rânduri consolidate.")
            st.dataframe(df_all.head(30), use_container_width=True)
        except Exception as e:
            st.error(f"Eroare la citirea fișierului anual: {e}")

    if up_30 is not None:
        try:
            df_30 = smartbill_read(up_30)
            st.session_state["moves_30"] = df_30
            st.success(f"Ultimele 30 zile: {len(df_30)} rânduri consolidate.")
            st.dataframe(df_30.head(30), use_container_width=True)
        except Exception as e:
            st.error(f"Eroare la citirea fișierului 30 zile: {e}")

    # show missing skus if products present
    if "products_df" in st.session_state and "moves_all" in st.session_state:
        miss = list_missing_skus(st.session_state["moves_all"], st.session_state["products_df"])
        if not miss.empty:
            st.warning("SKU-uri din SmartBill (an) care NU apar în fișierul de produse:")
            st.dataframe(miss, use_container_width=True)

with tabs[2]:
    st.subheader("Recomandări de comandă (pe grup_sku)")
    if not all(k in st.session_state for k in ["products_df","moves_all","moves_30"]):
        st.info("Încarcă întâi fișierul de produse și cele două fișiere SmartBill (an + 30 zile).")
    else:
        dfp = st.session_state["products_df"].copy()
        m_all = st.session_state["moves_all"].copy()
        m_30 = st.session_state["moves_30"].copy()

        # Build group mapping from products
        code_to_group = dfp.set_index("code")["grup_sku"].to_dict()

        # Join moves to products to get group for each SmartBill code
        m_all["grup_sku"] = m_all["cod"].map(code_to_group)
        m_30["grup_sku"] = m_30["cod"].map(code_to_group)

        # Filter out moves that don't map to any product (grup_sku NaN)
        m_all = m_all.dropna(subset=["grup_sku"])
        m_30 = m_30.dropna(subset=["grup_sku"])

        # Aggregate by group_sku
        g_all = m_all.groupby("grup_sku", as_index=False).agg(
            vanzari_total=("iesiri","sum"),
            stoc_final=("stoc_final","max"),
            num_coduri_sb=("cod","nunique"),
            produs_smartbill=("produs", lambda s: s.mode().iat[0] if len(s)>0 else ""),
        )
        g_30 = m_30.groupby("grup_sku", as_index=False).agg(
            vanzari_30zile=("iesiri","sum")
        )

        # Build list of all SKUs in each group from products
        group_skus = dfp.groupby("grup_sku").apply(
            lambda d: ", ".join(sorted(set(map(str, d["code"].tolist()))))
        ).rename("skus_in_group").reset_index()

        # Cheapest SKU in group by purchase_price
        cheapest = dfp.sort_values(["grup_sku","purchase_price"]).groupby("grup_sku").first().reset_index()
        cheapest = cheapest[["grup_sku","code","purchase_price"]].rename(columns={
            "code":"cheapest_sku",
            "purchase_price":"cheapest_purchase_price"
        })

        # Merge all
        rep = g_all.merge(g_30, on="grup_sku", how="left").merge(group_skus, on="grup_sku", how="left").merge(cheapest, on="grup_sku", how="left")
        rep["vanzari_30zile"] = rep["vanzari_30zile"].fillna(0)

        st.markdown("**Setări calcule recomandare**")
        c1, c2 = st.columns(2)
        with c1:
            coef_recent = st.number_input("Coeficient perioadă recentă (30 zile)", value=1.5, step=0.1)
        with c2:
            coef_total = st.number_input("Coeficient anual", value=0.2, step=0.1)

        rep["recomandat_de_comandat"] = (
            rep["vanzari_30zile"] * coef_recent +
            rep["vanzari_total"] * coef_total -
            rep["stoc_final"]
        ).clip(lower=0).round()

        # Attach a friendly product name: if we have a product row with code==grup_sku, use its name; else fall back to produs_smartbill
        name_lookup = dfp.set_index("code")["name"].to_dict()
        rep["product_name"] = rep["grup_sku"].map(name_lookup).fillna(rep["produs_smartbill"])

        # Order and show
        cols = ["grup_sku","product_name","skus_in_group","stoc_final","vanzari_30zile","vanzari_total","recomandat_de_comandat","cheapest_sku","cheapest_purchase_price"]
        rep = rep[cols].sort_values("recomandat_de_comandat", ascending=False)

        st.dataframe(rep, use_container_width=True)

        # Export
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            rep.to_excel(writer, index=False, sheet_name="recomandari")
        st.download_button("📥 Descarcă Excel", data=out.getvalue(), file_name="recomandari_comenzi_grup_sku.xlsx")
