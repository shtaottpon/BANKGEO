"""
Ядро рішення для УКРСИББАНКу: розподіл нових клієнтів між відділеннями
за географічним принципом.

Безпека:
    На геокодування надсилається ТІЛЬКИ адреса. Назва клієнта, ЄДРПОУ та
    інші персональні дані ніколи не залишають комп'ютер банку.

Архітектура (конвеєр):
    Excel → читання → очистка адрес → геокодування (Nominatim/OSM) →
    формула гаверсинуса (відстань до 6 відділень) → вибір мінімуму →
    запис результату.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

# geopy імпортується ліниво — щоб модуль можна було тестувати без мережі/залежностей.
try:
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError  # type: ignore
    from geopy.geocoders import Nominatim  # type: ignore
    _HAS_GEOPY = True
except ImportError:  # pragma: no cover
    Nominatim = None  # type: ignore
    GeocoderTimedOut = GeocoderServiceError = Exception  # type: ignore
    _HAS_GEOPY = False

# ---------------------------------------------------------------------------
# Константи
# ---------------------------------------------------------------------------

BRANCHES_CSV = Path(__file__).parent / "branches.csv"
NOMINATIM_USER_AGENT = "ukrsibbank-client-router/1.0 (hackathon)"
NOMINATIM_TIMEOUT_S = 10
RATE_LIMIT_S = 1.1  # політика Nominatim: не більше 1 req/sec
DEFAULT_THRESHOLD_KM = 15.0  # клієнти далі за поріг потребують ручної перевірки
EARTH_RADIUS_KM = 6371.0088

# Простий in-memory кеш геокодування — щоб ту саму адресу не питати двічі
_GEOCACHE: dict[str, tuple[Optional[float], Optional[float], str]] = {}


# ---------------------------------------------------------------------------
# Дані відділень
# ---------------------------------------------------------------------------

def load_branches(path: Path | str = BRANCHES_CSV) -> pd.DataFrame:
    """Завантажує довідник відділень банку (id, назва, координати)."""
    df = pd.read_csv(path)
    required = {"branch_id", "branch_name", "city", "address", "lat", "lon"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"У branches.csv бракує колонок: {missing}")
    return df


# ---------------------------------------------------------------------------
# Геометрія
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Відстань між двома точками на земній кулі (формула гаверсинуса), км."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# Очистка адрес
# ---------------------------------------------------------------------------

_SPACE_FIX = re.compile(r"\s+")
_ABBR_MAP = {
    r"\bм\.\s*": "м. ",
    r"\bсмт\.\s*": "смт ",
    r"\bс\.\s*": "с. ",
    r"\bвул\.\s*": "вул. ",
    r"\bпров\.\s*": "провулок ",
    r"\bпросп\.\s*": "проспект ",
    r"\bпр\-?т\.?\s*": "проспект ",
    r"\bбуд\.\s*": "буд. ",
}


def clean_address(raw: str) -> str:
    """Базова regex-очистка адреси — без AI, але вже сильно допомагає Nominatim.

    - забирає подвійні пробіли;
    - вставляє пробіл після скорочень (м.Вінниця → м. Вінниця);
    - прибирає «буд.» (Nominatim не любить);
    - додає «, Україна» якщо немає.
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s:
        return ""

    # вставляємо пробіл після крапки у скороченнях: «м.Вінниця» → «м. Вінниця»
    s = re.sub(r"\b(м|смт|с|вул|пров|просп|пр-т|пр\.т|буд|обл)\.([А-ЯҐЄІЇа-яґєії])", r"\1. \2", s)

    # розривач коми без пробілу: «Незалежності,14» → «Незалежності, 14»
    s = re.sub(r",(\S)", r", \1", s)

    # явні форми скорочень
    for pat, repl in _ABBR_MAP.items():
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)

    # прибираємо «буд.» — це майже завжди шумить
    s = re.sub(r"\bбуд\.\s*", "", s, flags=re.IGNORECASE)

    # стискаємо пробіли
    s = _SPACE_FIX.sub(" ", s).strip(" ,")

    # додаємо країну для Nominatim
    if "україн" not in s.lower():
        s = f"{s}, Україна"
    return s


_CITY_RE = re.compile(
    r"(?:^|[, ])(?:м\.?\s*|смт\s*|с\.?\s*)?([А-ЯҐЄІЇ][А-ЯҐЄІЇа-яґєії'\-]+)",
)


def extract_locality(raw: str) -> Optional[str]:
    """Витягує назву населеного пункту як fallback, якщо повна адреса не знайшлась."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    # шукаємо «м. X», «смт X», «с. X»
    m = re.search(r"\b(?:м\.?|смт|с\.?)\s*([А-ЯҐЄІЇ][А-ЯҐЄІЇа-яґєії'\-\s]+?)(?:[,\s]|вул|просп|пров|$)",
                  raw, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().split(",")[0].strip()
    # інакше — перше слово з великої літери
    m = _CITY_RE.search(raw)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Геокодування
# ---------------------------------------------------------------------------

@dataclass
class GeocodeResult:
    lat: Optional[float]
    lon: Optional[float]
    source: str  # "full" | "locality" | "cache" | "manual_review"
    matched_address: Optional[str] = None
    error: Optional[str] = None


def _make_geocoder():
    if not _HAS_GEOPY:
        raise RuntimeError(
            "Не встановлено geopy. Виконайте: pip install -r requirements.txt"
        )
    return Nominatim(user_agent=NOMINATIM_USER_AGENT, timeout=NOMINATIM_TIMEOUT_S)


def geocode_address(
    raw_address: str,
    geocoder=None,
    *,
    use_locality_fallback: bool = True,
) -> GeocodeResult:
    """Геокодує адресу. Стратегія: спершу повна, потім лише населений пункт."""
    cleaned = clean_address(raw_address)
    if not cleaned:
        return GeocodeResult(None, None, "manual_review", error="порожня адреса")

    if cleaned in _GEOCACHE:
        lat, lon, src = _GEOCACHE[cleaned]
        return GeocodeResult(lat, lon, "cache", matched_address=cleaned)

    geocoder = geocoder or _make_geocoder()

    # Спроба 1: повна адреса з прив'язкою до Вінницької області
    query = f"{cleaned}, Вінницька область" if "вінниц" not in cleaned.lower() else cleaned
    try:
        loc = geocoder.geocode(query, country_codes="ua", addressdetails=False, language="uk")
        time.sleep(RATE_LIMIT_S)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        return GeocodeResult(None, None, "manual_review", error=f"мережа: {e}")

    if loc:
        _GEOCACHE[cleaned] = (loc.latitude, loc.longitude, "full")
        return GeocodeResult(loc.latitude, loc.longitude, "full", matched_address=loc.address)

    # Спроба 2: лише населений пункт
    if use_locality_fallback:
        locality = extract_locality(raw_address)
        if locality:
            fallback_query = f"{locality}, Вінницька область, Україна"
            try:
                loc = geocoder.geocode(fallback_query, country_codes="ua", language="uk")
                time.sleep(RATE_LIMIT_S)
            except (GeocoderTimedOut, GeocoderServiceError) as e:
                return GeocodeResult(None, None, "manual_review", error=f"мережа: {e}")
            if loc:
                _GEOCACHE[cleaned] = (loc.latitude, loc.longitude, "locality")
                return GeocodeResult(loc.latitude, loc.longitude, "locality",
                                     matched_address=loc.address)

    _GEOCACHE[cleaned] = (None, None, "manual_review")
    return GeocodeResult(None, None, "manual_review", error="адресу не знайдено")


# ---------------------------------------------------------------------------
# Розподіл на відділення
# ---------------------------------------------------------------------------

@dataclass
class Assignment:
    branch_id: str
    branch_name: str
    distance_km: float
    needs_review: bool  # True якщо клієнт далі за поріг


def assign_branch(
    client_lat: float,
    client_lon: float,
    branches: pd.DataFrame,
    threshold_km: float = DEFAULT_THRESHOLD_KM,
) -> Assignment:
    """Знаходить найближче відділення. Якщо далі за поріг — позначає для ревʼю."""
    distances = branches.apply(
        lambda r: haversine_km(client_lat, client_lon, r["lat"], r["lon"]),
        axis=1,
    )
    idx = distances.idxmin()
    dist = float(distances.loc[idx])
    return Assignment(
        branch_id=branches.loc[idx, "branch_id"],
        branch_name=branches.loc[idx, "branch_name"],
        distance_km=round(dist, 2),
        needs_review=dist > threshold_km,
    )


# ---------------------------------------------------------------------------
# Високорівневий процесинг
# ---------------------------------------------------------------------------

ADDRESS_COL_CANDIDATES = ["Адреса реєстрації", "Адреса", "адреса", "address", "Address"]


def _detect_address_column(df: pd.DataFrame) -> str:
    for col in ADDRESS_COL_CANDIDATES:
        if col in df.columns:
            return col
    # fallback: перша колонка з «адрес» у назві
    for col in df.columns:
        if "адрес" in col.lower() or "address" in col.lower():
            return col
    raise ValueError(
        f"Не знайшов колонку з адресою. Очікую одну з: {ADDRESS_COL_CANDIDATES}"
    )


def process_dataframe(
    clients: pd.DataFrame,
    branches: pd.DataFrame,
    *,
    threshold_km: float = DEFAULT_THRESHOLD_KM,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """Обробляє DataFrame клієнтів і повертає його з доданими колонками.

    Безпека: у Nominatim іде ТІЛЬКИ значення колонки з адресою.
    """
    addr_col = _detect_address_column(clients)
    # Якщо geopy не встановлено, працюємо в режимі stub (для юніт-тестів):
    # подальші виклики geocode_address можуть бути замоканими.
    try:
        geocoder = _make_geocoder()
    except RuntimeError:
        geocoder = None

    result = clients.copy()
    extra = {
        "lat": [], "lon": [],
        "geocoding_status": [],
        "branch_id": [], "branch_name": [],
        "distance_km": [], "needs_review": [],
        "notes": [],
    }

    total = len(result)
    for i, raw in enumerate(result[addr_col].fillna("").astype(str), start=1):
        if progress_cb:
            progress_cb(i, total, raw[:60])

        geo = geocode_address(raw, geocoder=geocoder)
        if geo.lat is None or geo.lon is None:
            extra["lat"].append(None)
            extra["lon"].append(None)
            extra["geocoding_status"].append(geo.source)
            extra["branch_id"].append("MANUAL_REVIEW")
            extra["branch_name"].append("⚠ Ручна перевірка")
            extra["distance_km"].append(None)
            extra["needs_review"].append(True)
            extra["notes"].append(geo.error or "не геокодовано")
            continue

        assign = assign_branch(geo.lat, geo.lon, branches, threshold_km=threshold_km)
        notes_parts = []
        if geo.source == "locality":
            notes_parts.append("прив'язано до населеного пункту")
        if assign.needs_review:
            notes_parts.append(f"далі за {threshold_km:.0f} км — перевірити")

        extra["lat"].append(geo.lat)
        extra["lon"].append(geo.lon)
        extra["geocoding_status"].append(geo.source)
        extra["branch_id"].append(assign.branch_id)
        extra["branch_name"].append(assign.branch_name)
        extra["distance_km"].append(assign.distance_km)
        extra["needs_review"].append(assign.needs_review)
        extra["notes"].append("; ".join(notes_parts))

    for k, v in extra.items():
        result[k] = v

    return result


def process_file(
    input_path: Path | str,
    output_path: Path | str,
    *,
    threshold_km: float = DEFAULT_THRESHOLD_KM,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """CLI-обгортка: читає Excel, обробляє, зберігає Excel."""
    branches = load_branches()
    clients = pd.read_excel(input_path)
    result = process_dataframe(
        clients, branches, threshold_km=threshold_km, progress_cb=progress_cb
    )
    result.to_excel(output_path, index=False, sheet_name="Розподіл")
    return result


# ---------------------------------------------------------------------------
# CLI для швидкої перевірки
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Розподіл клієнтів між відділеннями УКРСИББАНКу")
    ap.add_argument("input", help="Excel з новими клієнтами (vkursi.pro вивантаження)")
    ap.add_argument("-o", "--output", default="result.xlsx", help="Куди зберегти результат")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_KM,
                    help=f"Поріг ручної перевірки, км (default: {DEFAULT_THRESHOLD_KM})")
    args = ap.parse_args()

    def _print_progress(i, total, addr):
        print(f"[{i}/{total}] {addr}")

    df = process_file(args.input, args.output, threshold_km=args.threshold,
                      progress_cb=_print_progress)
    print(f"\nГотово. Збережено: {args.output}")
    print(df.groupby("branch_name").size().to_string())
