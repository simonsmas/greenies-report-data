import json
import sys
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

CAMPAIGN = "Greenies"
TIMEZONE = "America/New_York"

VARIANT_ORDER = ["TEENIE", "Petite", "Regular", "Large"]
STOCK = {
    "TEENIE": 21248,
    "Petite": 17430,
    "Regular": 32785,
    "Large": 11537,
}

RETAILER_ORDER = [
    "Walmart",
    "PetSmart",
    "Chewy",
    "Amazon",
    "Target",
    "Petco",
    "Dollar General",
    "Other",
    "Unknown",
]

AUDIENCE_ORDER = [
    "meta",
    "facebook",
    "instagram",
    "tiktok",
    "google",
    "email",
    "Unknown",
]

def find_column(df, possible_names):
    cols = {str(c).strip(): c for c in df.columns}
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in possible_names:
        if name in cols:
            return cols[name]
        if name.lower() in lowered:
            return lowered[name.lower()]
    raise ValueError(f"Missing required column. Tried: {', '.join(possible_names)}")

def normalize_variant(value):
    text = str(value or "").strip()
    upper = text.upper()

    # Exact or full-product-name matching.
    if "TEENIE" in upper or "TEENY" in upper:
        return "TEENIE"
    if "PETITE" in upper:
        return "Petite"
    if "REGULAR" in upper:
        return "Regular"
    if "LARGE" in upper:
        return "Large"

    return "Unknown"

def parse_order_datetime_utc(df, date_col, time_col):
    raw_date = df[date_col].astype(str).str.strip()
    raw_time = df[time_col].astype(str).str.strip()

    parsed_iso = pd.to_datetime(raw_date, format="%Y-%m-%d", errors="coerce")
    parsed_us = pd.to_datetime(raw_date, format="%m/%d/%Y", errors="coerce")
    parsed_us_short = pd.to_datetime(raw_date, format="%m/%d/%y", errors="coerce")

    parsed_date = parsed_iso.copy()
    parsed_date = parsed_date.fillna(parsed_us)
    parsed_date = parsed_date.fillna(parsed_us_short)

    missing = parsed_date.isna()
    if missing.any():
        fallback = pd.to_datetime(raw_date[missing], errors="coerce", dayfirst=False)
        parsed_date.loc[missing] = fallback

    if parsed_date.isna().any():
        examples = raw_date[parsed_date.isna()].head(5).tolist()
        raise ValueError(f"Could not parse orderDate. Example failed values: {examples}")

    date_text = parsed_date.dt.strftime("%Y-%m-%d")
    combined = date_text + " " + raw_time

    parsed_dt = pd.to_datetime(combined, errors="coerce", utc=True)

    if parsed_dt.isna().any():
        examples = combined[parsed_dt.isna()].head(5).tolist()
        raise ValueError(f"Could not parse orderDate/orderTime. Example failed values: {examples}")

    return parsed_dt.dt.tz_convert(TIMEZONE)

def pct(value):
    return round(float(value), 1)

def value_counts_percent(series, preferred_order=None):
    cleaned = series.fillna("").astype(str).str.strip()
    cleaned = cleaned.replace("", "Unknown")

    counts = cleaned.value_counts().to_dict()
    total = sum(counts.values()) or 1

    labels = []
    if preferred_order:
        for label in preferred_order:
            if label in counts:
                labels.append(label)

    for label in counts:
        if label not in labels:
            labels.append(label)

    return [
        {
            "label": label,
            "count": int(counts[label]),
            "percent": pct(counts[label] / total * 100),
        }
        for label in labels
    ]

def build_report(input_csv="orders.csv", output_json="greenies_totals.json"):
    input_path = Path(input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"Could not find {input_csv}")

    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]

    variant_col = find_column(df, ["variant", "Product", "Product_Sampled", "Variant"])
    date_col = find_column(df, ["orderDate", "Date"])
    time_col = find_column(df, ["orderTime", "Order Time", "order time"])
    tried_col = find_column(df, ["previously_tried_greenies", "isNewToBrandProductsYN"])
    retailer_col = find_column(df, ["preferred_retailer", "Retailer_Preference"])
    audience_col = find_column(df, ["audience", "utm_source"])

    original_variant_examples = df[variant_col].dropna().astype(str).str.strip().unique().tolist()[:20]

    df["Variant"] = df[variant_col].map(normalize_variant)
    df["OrderEastern"] = parse_order_datetime_utc(df, date_col, time_col)
    df["EasternDate"] = df["OrderEastern"].dt.date
    df["PreviouslyTriedGreeniesDentalTreats"] = df[tried_col].astype(str).str.strip().replace("", "Unknown")
    df["PreferredRetailer"] = df[retailer_col].astype(str).str.strip().replace("", "Unknown")
    df["Audience"] = df[audience_col].astype(str).str.strip().replace("", "Unknown")

    website_df = df.copy()

    latest_order_eastern = website_df["OrderEastern"].max()
    latest_order_eastern_iso = latest_order_eastern.isoformat()
    updated_to = max(website_df["EasternDate"])

    claimed_by_variant = website_df.groupby("Variant").size().to_dict()

    summary_rows = []
    total_available = 0
    total_claimed = 0

    for variant in VARIANT_ORDER:
        available = int(STOCK[variant])
        claimed = int(claimed_by_variant.get(variant, 0))
        remaining = available - claimed
        percent_remaining = pct(remaining / available * 100) if available else 0

        summary_rows.append({
            "variant": variant,
            "available": available,
            "claimed": claimed,
            "remaining": remaining,
            "percent_remaining": percent_remaining,
            "percent_remaining_display": f"{percent_remaining:g}%",
        })

        total_available += available
        total_claimed += claimed

    total_remaining = total_available - total_claimed
    total_percent_remaining = pct(total_remaining / total_available * 100) if total_available else 0

    summary_rows.append({
        "variant": "TOTAL",
        "available": total_available,
        "claimed": total_claimed,
        "remaining": total_remaining,
        "percent_remaining": total_percent_remaining,
        "percent_remaining_display": f"{total_percent_remaining:g}%",
    })

    all_dates = sorted(website_df["EasternDate"].unique())
    date_labels = [pd.Timestamp(d).strftime("%m/%d") for d in all_dates]

    redemptions_rows = []
    line_series = []

    for variant in VARIANT_ORDER:
        subset = website_df[website_df["Variant"] == variant]
        counts = subset.groupby("EasternDate").size().to_dict()
        values = [int(counts.get(d, 0)) for d in all_dates]
        redemptions_rows.append({
            "variant": variant,
            "values": values,
            "total": int(sum(values)),
        })
        line_series.append({
            "variant": variant,
            "values": values,
        })

    total_values = []
    for d in all_dates:
        total_values.append(int(website_df[website_df["Variant"].isin(VARIANT_ORDER)]["EasternDate"].eq(d).sum()))

    redemptions_rows.append({
        "variant": "Total",
        "values": total_values,
        "total": int(sum(total_values)),
    })

    previously_tried = value_counts_percent(
        website_df["PreviouslyTriedGreeniesDentalTreats"],
        preferred_order=["Yes", "No", "Unknown"]
    )

    preferred_retailer = value_counts_percent(
        website_df["PreferredRetailer"],
        preferred_order=RETAILER_ORDER
    )

    audience = value_counts_percent(
        website_df["Audience"],
        preferred_order=AUDIENCE_ORDER
    )

    data = {
        "campaign": CAMPAIGN,
        "timezone": TIMEZONE,
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "last_updated_source": "GitHub Actions processing time",
        "latest_order_eastern": latest_order_eastern_iso,
        "includes_orders_up_to_eastern": latest_order_eastern_iso,
        "updated_to": str(updated_to),
        "excluded_current_eastern_date": None,
        "includes_current_eastern_date": True,
        "rows": summary_rows,
        "redemptions_by_day": {
            "dates": date_labels,
            "date_iso": [str(d) for d in all_dates],
            "rows": redemptions_rows,
            "max_columns_per_table": 20,
        },
        "charts": {
            "redemptions_line": {
                "title": "Redemptions By Day",
                "dates": date_labels,
                "series": line_series,
            },
            "previously_tried": {
                "title": "Previously Tried Greenies Dental Treats",
                "items": previously_tried,
            },
            "preferred_retailer": {
                "title": "Preferred Retailer",
                "items": preferred_retailer,
            },
            "audience": {
                "title": "Redemptions by Audience",
                "items": audience,
            },
        },
        "_debug": {
            "rows_processed": int(len(df)),
            "variant_column_used": str(variant_col),
            "original_variant_examples": original_variant_examples,
            "mapped_variant_counts": {str(k): int(v) for k, v in claimed_by_variant.items()},
        }
    }

    Path(output_json).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Created {output_json}")
    print(f"Rows processed: {len(df):,}")
    print(f"Mapped variant counts: {claimed_by_variant}")
    print(f"Includes orders up to Eastern time: {latest_order_eastern_iso}")

def main():
    input_csv = sys.argv[1] if len(sys.argv) > 1 else "orders.csv"
    output_json = sys.argv[2] if len(sys.argv) > 2 else "greenies_totals.json"
    build_report(input_csv, output_json)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
