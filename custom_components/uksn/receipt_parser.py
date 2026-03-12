from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pdfplumber


LEFT_BOUNDS = {
    "service": (0, 80),
    "fee_rate": (80, 108),
    "cost": (108, 140),
    "discount": (140, 164),
    "recalculation": (164, 197),
    "total": (197, 230),
}

RIGHT_BOUNDS = {
    "service": (230, 268),
    "unit": (268, 286),
    "tariff": (286, 312),
    "usage": (312, 342),
    "charge_by_tariff": (342, 371),
    "discount": (371, 394),
    "recalculation": (394, 431),
    "total": (431, 460),
}

UNITS = (
    "куб.м",
    "гкал",
    "гКал",
    "Гкал",
    "Гкал.",
    "квт*ч",
    "кВт*ч",
    "шт.",
    "шт",
    "кв.м",
)

SERVICE_KEY_MAP = {
    "хвс": "hvs",
    "хол.вода": "hvs",
    "холодная вода": "hvs",
    "холодное водоснабжение": "hvs",

    "гвс: вода": "gvs_water",
    "гвс вода": "gvs_water",
    "горячая вода": "gvs_water",

    "гвс: тепло": "gvs_heat",
    "гвс тепло": "gvs_heat",
    "горячвода": "gvs_heat",

    "отопление": "otoplenie",
    "сои ээ": "soi_ee",
    "сои водоотведения": "soi_water_disposal",
    "то т/прием": "to_priem",

    "содержание.общ.имущ.": "maintenance_common",
    "содержание жилья": "maintenance",
    "капремонт": "capital_repair",
    "обращение с тко": "waste",
    "антенна": "antenna",
    "радио": "radio",
    "домофон": "intercom",
}


def _clean_text(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())


def _to_float(value: str) -> Optional[float]:
    if not value:
        return None
    v = value.strip().replace(" ", "")
    if v in {"-", "—", "–"}:
        return 0.0
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _normalize_numeric_cell(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return ""

    parts = text.split()
    merged: list[str] = []
    i = 0
    while i < len(parts):
        cur = parts[i]

        if cur in {"-", "+"} and i + 1 < len(parts) and re.fullmatch(r"\d+(?:[.,]\d+)?", parts[i + 1]):
            merged.append(cur + parts[i + 1])
            i += 2
            continue

        if re.fullmatch(r"\d{1,3}", cur) and i + 1 < len(parts) and re.fullmatch(r"\d{3}[.,]\d+", parts[i + 1]):
            merged.append(cur + " " + parts[i + 1])
            i += 2
            continue

        merged.append(cur)
        i += 1

    return " ".join(merged)


def _normalize_service_name(name: str) -> str:
    name = _clean_text(name)
    name = name.replace("ё", "е").replace("Ё", "Е")

    replacements = {
        "водоотведеникяуб.м": "СОИ водоотведения",
        "пов.коэфк.уб.м": "ГВС-вода пов.коэф.",
    }
    low_name = name.lower()
    for bad, good in replacements.items():
        if bad.lower() in low_name:
            return good

    for unit_name in UNITS:
        if low_name.endswith(unit_name.lower()) and len(name) > len(unit_name):
            name = name[: -len(unit_name)].strip()
            break

    return name


def _normalize_service_key(name: str) -> str:
    base = _normalize_service_name(name).lower()
    base = base.replace("ё", "е")
    base = re.sub(r"\s+", " ", base).strip()

    if base in SERVICE_KEY_MAP:
        return SERVICE_KEY_MAP[base]

    slug = re.sub(r"[^a-zA-Z0-9а-яА-Я]+", "_", base, flags=re.UNICODE).strip("_").lower()
    translit = (
        slug.replace("хвс", "hvs")
        .replace("гвс", "gvs")
        .replace("отопление", "otoplenie")
        .replace("сои", "soi")
        .replace("вода", "voda")
        .replace("тепло", "teplo")
        .replace("ээ", "ee")
        .replace("прием", "priem")
        .replace("содержание", "maintenance")
        .replace("жилья", "housing")
        .replace("капремонт", "capital_repair")
    )
    translit = re.sub(r"_+", "_", translit)
    return translit or "unknown_service"


def _extract_period(text: str) -> Optional[str]:
    m = re.search(r"Начислено за\s+([А-ЯA-ZЁ]+)\s+(\d{4})\s+г", text, flags=re.I)
    return f"{m.group(1).upper()} {m.group(2)}" if m else None


def _extract_total_to_pay(text: str) -> Optional[float]:
    m = re.search(r"К оплате за .*?:\s*([\d\s]+)\s*р\.\s*([\d]{2})\s*к\.", text, flags=re.S)
    if not m:
        return None
    return float(f"{m.group(1).replace(' ', '')}.{m.group(2)}")


def _collect_lines(page) -> list[list[dict[str, Any]]]:
    words = page.extract_words(x_tolerance=1, y_tolerance=2, keep_blank_chars=False)
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_y = None

    for w in sorted(words, key=lambda z: (z["top"], z["x0"])):
        if w["x1"] >= 460:
            continue
        if current_y is None or abs(w["top"] - current_y) <= 1.2:
            current.append(w)
            current_y = w["top"] if current_y is None else (current_y + w["top"]) / 2
        else:
            lines.append(sorted(current, key=lambda z: z["x0"]))
            current = [w]
            current_y = w["top"]

    if current:
        lines.append(sorted(current, key=lambda z: z["x0"]))
    return lines


def _slice_columns(words: list[dict[str, Any]], bounds: dict[str, tuple[float, float]]) -> dict[str, str]:
    cols: dict[str, list[str]] = {k: [] for k in bounds}

    for w in words:
        x = (w["x0"] + w["x1"]) / 2
        for col, (lo, hi) in bounds.items():
            if lo <= x < hi:
                cols[col].append(w["text"])
                break

    out = {k: _clean_text(" ".join(v)) for k, v in cols.items()}

    if "unit" in out and not out.get("unit") and out.get("service"):
        svc = out["service"]
        low = svc.lower()
        for unit in UNITS:
            if low.endswith(unit.lower()):
                out["service"] = svc[: -len(unit)].strip()
                out["unit"] = svc[-len(unit):].strip()
                break

    for key in (
        "fee_rate",
        "cost",
        "tariff",
        "usage",
        "charge_by_tariff",
        "discount",
        "recalculation",
        "total",
    ):
        if key in out:
            out[key] = _normalize_numeric_cell(out[key])

    return out


def _parse_left(words: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not words:
        return None

    cols = _slice_columns(words, LEFT_BOUNDS)
    service_raw = _clean_text(cols["service"].strip())
    service = _normalize_service_name(service_raw)
    if not service or service.startswith("Вид платежа") or service == "ИТОГО:":
        return None

    return {
        "side": "left",
        "service_raw": service_raw,
        "service": service,
        "service_key": _normalize_service_key(service_raw),
        "fee_rate": _to_float(cols["fee_rate"]),
        "cost": _to_float(cols["cost"]),
        "discount": _to_float(cols["discount"]),
        "recalculation": _to_float(cols["recalculation"]),
        "total": _to_float(cols["total"]),
    }


def _parse_right(words: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not words:
        return None

    cols = _slice_columns(words, RIGHT_BOUNDS)
    service_raw = _clean_text(cols["service"].strip())
    service = _normalize_service_name(service_raw)
    if not service or service.startswith("Вид платежа") or service == "ИТОГО:":
        return None

    return {
        "side": "right",
        "service_raw": service_raw,
        "service": service,
        "service_key": _normalize_service_key(service_raw),
        "unit": cols["unit"] or None,
        "tariff": _to_float(cols["tariff"]),
        "usage": _to_float(cols["usage"]),
        "charge_by_tariff": _to_float(cols["charge_by_tariff"]),
        "discount": _to_float(cols["discount"]),
        "recalculation": _to_float(cols["recalculation"]),
        "total": _to_float(cols["total"]),
    }


def parse_invoice_pdf(pdf_path: str | Path) -> dict[str, Any]:
    pdf_path = Path(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        full_text = page.extract_text() or ""
        lines = _collect_lines(page)

    table_started = False
    rows: list[dict[str, Any]] = []

    for line in lines:
        line_text = _clean_text(" ".join(w["text"] for w in line))

        if not table_started:
            if "Содержание.Общ.Имущ." in line_text or re.search(r"\bИТОГО:\b", line_text):
                table_started = True
            else:
                continue

        if "ИТОГО:" in line_text:
            break

        left_words = [w for w in line if w["x1"] < 230]
        right_words = [w for w in line if 230 <= w["x0"] < 460]

        left_row = _parse_left(left_words)
        right_row = _parse_right(right_words)

        if left_row:
            rows.append(left_row)
        if right_row:
            rows.append(right_row)

    services: list[dict[str, Any]] = []
    for row in rows:
        if row.get("service") in (None, "", "ИТОГО:"):
            continue

        if row.get("side") == "right":
            services.append(
                {
                    "service_raw": row.get("service_raw") or row.get("service"),
                    "service": row.get("service"),
                    "service_key": row.get("service_key"),
                    "unit": row.get("unit"),
                    "tariff": row.get("tariff"),
                    "usage": row.get("usage"),
                    "total": row.get("total"),
                    "side": "right",
                }
            )
        elif row.get("side") == "left":
            services.append(
                {
                    "service_raw": row.get("service_raw") or row.get("service"),
                    "service": row.get("service"),
                    "service_key": row.get("service_key"),
                    "unit": "кв.м",
                    "tariff": row.get("fee_rate"),
                    "usage": None,
                    "total": row.get("total"),
                    "side": "left",
                }
            )

    return {
        "period": _extract_period(full_text),
        "total_to_pay": _extract_total_to_pay(full_text),
        "services": services,
    }