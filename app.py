"""
Streamlit-інтерфейс для розподілу клієнтів між відділеннями УКРСИББАНКу.

Запуск:
    streamlit run app.py
"""
from __future__ import annotations

import io
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from geocoder import (
    DEFAULT_THRESHOLD_KM,
    load_branches,
    process_dataframe,
)

# ---------------------------------------------------------------------------
# Конфігурація сторінки
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="УКРСИББАНК · Розподіл клієнтів",
    page_icon="🏦",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Палітра кольорів для відділень (folium підтримує обмежений набір)
# ---------------------------------------------------------------------------
BRANCH_COLORS = {
    "B01": "red",
    "B02": "blue",
    "B03": "green",
    "B04": "purple",
    "B05": "orange",
    "B06": "darkblue",
    "MANUAL_REVIEW": "gray",
}

# ---------------------------------------------------------------------------
# Сайдбар
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Налаштування")
    threshold = st.slider(
        "Поріг ручної перевірки, км",
        min_value=5.0, max_value=50.0,
        value=float(DEFAULT_THRESHOLD_KM), step=1.0,
        help="Клієнти далі за цей радіус від найближчого відділення позначаються "
             "як такі, що потребують ручної перевірки.",
    )

    st.markdown("---")
    st.markdown("### 🔒 Безпека даних")
    st.info(
        "На геокодування надсилається **тільки адреса**. "
        "Назва клієнта, ЄДРПОУ та інші персональні дані "
        "не залишають комп'ютер банку."
    )

    st.markdown("---")
    st.markdown("### 🏦 Відділення")
    branches = load_branches()
    st.dataframe(
        branches[["branch_name", "city"]].rename(
            columns={"branch_name": "Відділення", "city": "Місто"}
        ),
        use_container_width=True, hide_index=True,
    )

# ---------------------------------------------------------------------------
# Заголовок
# ---------------------------------------------------------------------------
st.title("🏦 Розподіл нових клієнтів між відділеннями")
st.caption(
    "Завантажте Excel з vkursi.pro — інструмент автоматично визначить "
    "найближче відділення для кожного клієнта."
)

# ---------------------------------------------------------------------------
# Завантаження файлу
# ---------------------------------------------------------------------------
uploaded = st.file_uploader(
    "Excel з новими ФОП/ЮО",
    type=["xlsx", "xls"],
    help="Файл має містити колонку 'Адреса реєстрації' (або 'Адреса', 'address').",
)

if not uploaded:
    st.info("👆 Завантажте файл, щоб розпочати розподіл.")
    st.stop()

# Прочитаємо файл, щоб показати прев'ю
try:
    df_input = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Не вдалось прочитати файл: {e}")
    st.stop()

st.subheader(f"📋 Прев'ю вхідних даних ({len(df_input)} рядків)")
st.dataframe(df_input.head(10), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Кнопка обробки
# ---------------------------------------------------------------------------
if st.button("🚀 Розподілити клієнтів", type="primary", use_container_width=True):
    progress_bar = st.progress(0.0)
    status_text = st.empty()

    def progress_cb(i: int, total: int, addr: str) -> None:
        progress_bar.progress(i / total)
        status_text.caption(f"Геокодую {i}/{total}: {addr}")

    with st.spinner("Розподіляю клієнтів... це триває ~1 секунду на адресу через ліміт Nominatim."):
        result = process_dataframe(
            df_input, branches,
            threshold_km=threshold,
            progress_cb=progress_cb,
        )

    progress_bar.empty()
    status_text.empty()
    st.session_state["result"] = result
    st.success(f"✅ Готово! Оброблено {len(result)} клієнтів.")

# ---------------------------------------------------------------------------
# Відображення результату
# ---------------------------------------------------------------------------
result = st.session_state.get("result")
if result is None:
    st.stop()

# Метрики ----------------------------------------------------------------
geocoded = result["lat"].notna().sum()
needs_review = int(result["needs_review"].sum())
auto_assigned = len(result) - needs_review

c1, c2, c3, c4 = st.columns(4)
c1.metric("Усього клієнтів", len(result))
c2.metric("Геокодовано", f"{geocoded} ({geocoded/len(result)*100:.0f}%)")
c3.metric("Автоматично розподілено", auto_assigned)
c4.metric("На ручну перевірку", needs_review)

# Графік розподілу -------------------------------------------------------
st.subheader("📊 Розподіл по відділеннях")
distribution = (
    result.groupby("branch_name").size()
    .sort_values(ascending=False).reset_index(name="Клієнтів")
)
st.bar_chart(distribution.set_index("branch_name"))

# Карта ------------------------------------------------------------------
st.subheader("🗺️ Карта розподілу")

center_lat = branches["lat"].mean()
center_lon = branches["lon"].mean()
fmap = folium.Map(location=[center_lat, center_lon], zoom_start=8, tiles="OpenStreetMap")

# Маркери відділень — великі квадратні
for _, b in branches.iterrows():
    color = BRANCH_COLORS.get(b["branch_id"], "blue")
    folium.Marker(
        location=[b["lat"], b["lon"]],
        popup=folium.Popup(
            f"<b>{b['branch_name']}</b><br>{b['address']}", max_width=300
        ),
        tooltip=b["branch_name"],
        icon=folium.Icon(color=color, icon="university", prefix="fa"),
    ).add_to(fmap)

# Кластер клієнтів
cluster = MarkerCluster(name="Клієнти").add_to(fmap)
client_addr_col = next(
    (c for c in ["Адреса реєстрації", "Адреса", "address"] if c in result.columns),
    result.columns[0],
)
for _, row in result.iterrows():
    if pd.isna(row["lat"]) or pd.isna(row["lon"]):
        continue
    color = BRANCH_COLORS.get(row["branch_id"], "gray")
    name = row.get("Назва клієнта", "клієнт")
    popup_html = (
        f"<b>{name}</b><br>"
        f"📍 {row[client_addr_col]}<br>"
        f"→ {row['branch_name']}<br>"
        f"📏 {row['distance_km']} км"
    )
    if row["needs_review"]:
        popup_html += "<br>⚠️ Потребує перевірки"
    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=6, color=color, fill=True, fill_opacity=0.8,
        popup=folium.Popup(popup_html, max_width=300),
    ).add_to(cluster)

# Лінії клієнт→відділення (тільки для тих, що автоматично розподілені)
branch_coords = {r["branch_id"]: (r["lat"], r["lon"]) for _, r in branches.iterrows()}
for _, row in result.iterrows():
    if pd.isna(row["lat"]) or row["branch_id"] not in branch_coords:
        continue
    if row["needs_review"]:
        continue
    bl, bn = branch_coords[row["branch_id"]]
    folium.PolyLine(
        locations=[[row["lat"], row["lon"]], [bl, bn]],
        color=BRANCH_COLORS.get(row["branch_id"], "gray"),
        weight=1, opacity=0.4,
    ).add_to(fmap)

folium.LayerControl().add_to(fmap)
st_folium(fmap, width=None, height=550, returned_objects=[])

# Таблиця результату -----------------------------------------------------
st.subheader("📑 Деталі розподілу")
show_only_review = st.checkbox("Показати лише тих, хто потребує перевірки")
display_df = result[result["needs_review"]] if show_only_review else result

display_cols = [c for c in [
    "Назва клієнта", "ЄДРПОУ/ІПН", client_addr_col,
    "branch_name", "distance_km", "needs_review", "geocoding_status", "notes",
] if c in display_df.columns]

st.dataframe(
    display_df[display_cols].rename(columns={
        "branch_name": "Відділення",
        "distance_km": "Відстань, км",
        "needs_review": "Перевірити",
        "geocoding_status": "Якість геокодування",
        "notes": "Примітки",
    }),
    use_container_width=True, hide_index=True,
)

# Кнопка скачування -------------------------------------------------------
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    result.to_excel(writer, index=False, sheet_name="Розподіл")
    distribution.to_excel(writer, index=False, sheet_name="Статистика")

st.download_button(
    "💾 Скачати результат (Excel)",
    data=buf.getvalue(),
    file_name="rozpodil_kliientiv.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    use_container_width=True,
)
