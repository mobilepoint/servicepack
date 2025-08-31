import pandas as pd
import numpy as np
from typing import Tuple, List

PRODUCT_COLUMN_ALIASES = {
    "code": ["cod", "product code", "sku", "id"],
    "name": ["nume", "name", "denumire", "title"],
    "purchase_price": ["pret achizitie", "purchase price", "buy price", "cost"],
    "sale_price": ["pret vanzare", "price", "sale price", "selling price"],
    "profit_abs": ["profit"],
    "sale_price_minus20": ["pret vanzare -20%"],
    "profit_minus20": ["profit -20%"],
    "competitor_price": ["concurenta", "pret concurenta", "competitor price"],
}

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Drop unnamed columns
    df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed", case=False)]
    # Lowercase and strip
    df.columns = df.columns.str.strip().str.lower()
    # Coerce numerics
    for col in ["pret achizitie","purchase price","buy price","cost",
                "pret vanzare","price","sale price","selling price",
                "profit","pret vanzare -20%","profit -20%","concurenta","pret concurenta","competitor price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def map_product_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for std, aliases in PRODUCT_COLUMN_ALIASES.items():
        for a in aliases:
            if a in df.columns:
                mapping[std] = a
                break
    # Build normalized frame
    out = pd.DataFrame()
    out["code"] = df.get(mapping.get("code", ""), pd.Series(dtype="object")).astype(str).str.strip()
    out["name"] = df.get(mapping.get("name", ""), pd.Series(dtype="object")).astype(str).str.strip()
    out["purchase_price"] = pd.to_numeric(df.get(mapping.get("purchase_price",""), pd.Series(dtype="float")), errors="coerce")
    out["sale_price"] = pd.to_numeric(df.get(mapping.get("sale_price",""), pd.Series(dtype="float")), errors="coerce")
    out["profit_abs"] = pd.to_numeric(df.get(mapping.get("profit_abs",""), pd.Series(dtype="float")), errors="coerce")
    out["sale_price_minus20"] = pd.to_numeric(df.get(mapping.get("sale_price_minus20",""), pd.Series(dtype="float")), errors="coerce")
    out["profit_minus20"] = pd.to_numeric(df.get(mapping.get("profit_minus20",""), pd.Series(dtype="float")), errors="coerce")
    out["competitor_price"] = pd.to_numeric(df.get(mapping.get("competitor_price",""), pd.Series(dtype="float")), errors="coerce")
    # Drop empty codes
    out = out[out["code"].str.len() > 0]
    # Compute margins if missing
    out["gross_margin_pct"] = np.where((out["sale_price"]>0) & (out["purchase_price"]>=0),
                                       (out["sale_price"] - out["purchase_price"]) / out["sale_price"] * 100, np.nan)
    out["markup_pct"] = np.where((out["purchase_price"]>0),
                                 (out["sale_price"] - out["purchase_price"]) / out["purchase_price"] * 100, np.nan)
    return out

def normalize_stock_moves(df: pd.DataFrame) -> pd.DataFrame:
    # Attempts to map common SmartBill exports: Date, Product Code/SKU, Qty, Type (in/out)
    cols = df.columns.str.lower().str.strip()
    df.columns = cols
    candidates_date = [c for c in cols if "data" in c or "date" in c]
    candidates_code = [c for c in cols if "cod" in c or "sku" in c or "product" in c]
    candidates_qty = [c for c in cols if "cant" in c or "qty" in c or "buc" in c or "quantity" in c]
    date_col = candidates_date[0] if candidates_date else None
    code_col = candidates_code[0] if candidates_code else None
    qty_col = candidates_qty[0] if candidates_qty else None
    out = pd.DataFrame()
    if date_col: out["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    if code_col: out["product_code"] = df[code_col].astype(str).str.strip()
    if qty_col: out["qty"] = pd.to_numeric(df[qty_col], errors="coerce")
    # Heuristic: negative qty for outputs (if there is a "tip" column)
    if "tip" in cols or "type" in cols:
        tcol = "tip" if "tip" in cols else "type"
        mask_out = df[tcol].astype(str).str.lower().str.contains("iesire|out")
        out.loc[mask_out, "qty"] = -out.loc[mask_out, "qty"].abs()
    return out.dropna(subset=["product_code","date","qty"])