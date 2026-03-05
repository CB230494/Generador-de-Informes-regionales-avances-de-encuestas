# -*- coding: utf-8 -*-
import io
import os
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import pandas as pd
import streamlit as st
from PIL import Image

from pypdf import PdfReader

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch


# =========================
# Config
# =========================
st.set_page_config(page_title="Reporte Regional - Encuestas", layout="wide")

TIPOS_VALIDOS = ["Comunidad", "Comercio", "Policial"]

REGIONES_CATALOGO = [
    "Región 1 San José Norte.",
    "Región 1 San José Sur.",
    "Región 1 San José Central.",
    "Región 2 Alajuela.",
    "Región 3 Cartago.",
    "Región 4 Heredia.",
    "Región 5 Chorotega.",
    "Región 6 Pacífico Central.",
    "Región 7 Brunca.",
    "Región 8 Huetar Norte.",
    "Región 9 Huetar Atlántica.",
    "Región 10 Brunca Sur.",
    "Región 11 Chorotega Norte.",
    "Región 12 Caribe.",
]

# Colores institucionales
COLOR_VERDE = colors.HexColor("#1B5E20")
COLOR_NARANJA = colors.HexColor("#F9A825")  # Amarillo
COLOR_ROJO = colors.HexColor("#B71C1C")
COLOR_ENCABEZADO = colors.HexColor("#263238")
COLOR_ENCABEZADO_2 = colors.HexColor("#37474F")


# =========================
# Helpers - PDF to text
# =========================
@dataclass
class ParsedHeader:
    delegacion: str
    fecha: str
    hora: str


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_header(text: str) -> ParsedHeader:
    deleg = ""
    hora = ""
    fecha = ""

    m = re.search(r"Delegaci[oó]n:\s*(.+)", text, re.IGNORECASE)
    if m:
        deleg = m.group(1).strip()

    m = re.search(r"Hora del reporte:\s*([0-9]{1,2}:[0-9]{2})", text, re.IGNORECASE)
    if m:
        hora = m.group(1).strip()

    m = re.search(r"Fecha:\s*(.+)", text, re.IGNORECASE)
    if m:
        fecha = m.group(1).strip()

    return ParsedHeader(delegacion=deleg, fecha=fecha, hora=hora)


def get_section_block(text: str, section_name: str) -> str:
    t = text.replace("\r", "\n")

    m_start = re.search(rf"\n{re.escape(section_name)}\n", t, re.IGNORECASE)
    if not m_start:
        m_start = re.search(rf"{re.escape(section_name)}\n", t, re.IGNORECASE)
        if not m_start:
            return ""

    start_idx = m_start.end()

    next_idxs = []
    for sec in TIPOS_VALIDOS:
        if sec.lower() == section_name.lower():
            continue
        m_next = re.search(rf"\n{re.escape(sec)}\n", t[start_idx:], re.IGNORECASE)
        if m_next:
            next_idxs.append(start_idx + m_next.start())

    end_idx = min(next_idxs) if next_idxs else len(t)
    return t[start_idx:end_idx].strip()


def parse_table_block_robust(header: ParsedHeader, block_text: str) -> List[Dict]:
    rows = []
    if not block_text:
        return rows

    if re.search(r"No hay registros", block_text, re.IGNORECASE):
        return rows

    compact = " ".join(block_text.split())

    matches = re.findall(
        r"(Comunidad|Comercio|Policial)\s+"
        r"([A-Za-zÁÉÍÓÚÑáéíóúñ\s]+?)\s+"
        r"(\d+)\s+(\d+)\s+(\d+)%\s+(\d+)",
        compact
    )

    for tipo, distrito, meta, contab, pct, pendiente in matches:
        rows.append({
            "Delegación": header.delegacion,
            "Fecha": header.fecha,
            "Hora": header.hora,
            "Tipo": tipo.strip(),
            "Distrito": distrito.strip(),
            "Meta": int(meta),
            "Contabilidad": int(contab),
            "% Avance": float(pct),
            "Pendiente": int(pendiente),
        })

    return rows


def parse_pdf_report(file_name: str, file_bytes: bytes) -> Tuple[ParsedHeader, pd.DataFrame]:
    text = extract_text_from_pdf(file_bytes)
    header = parse_header(text)

    all_rows = []
    for sec in TIPOS_VALIDOS:
        block = get_section_block(text, sec)
        rows = parse_table_block_robust(header, block)
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "Delegación", "Fecha", "Hora", "Tipo", "Distrito",
            "Meta", "Contabilidad", "% Avance", "Pendiente"
        ])
    df["Archivo"] = file_name
    return header, df


# =========================
# Aggregations
# =========================
def agg_tipo(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()
    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    return g


def agg_delegacion_tipo(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["Delegación", "Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()
    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    return g


# =========================
# Color rules
# =========================
def color_por_porcentaje(p: float, verde_desde: float, naranja_desde: float):
    if p >= verde_desde:
        return COLOR_VERDE
    if p >= naranja_desde:
        return COLOR_NARANJA
    return COLOR_ROJO


def etiqueta_por_porcentaje(p: float, verde_desde: float, naranja_desde: float) -> str:
    if p >= verde_desde:
        return "ALTO"
    if p >= naranja_desde:
        return "MEDIO"
    return "BAJO"


# =========================
# PDF helpers
# =========================
def rl_image_from_pil(pil_img: Image.Image, width_in: float = 1.10) -> RLImage:
    bio = io.BytesIO()
    pil_img.save(bio, format="PNG")
    bio.seek(0)
    img = RLImage(bio)
    img.drawWidth = width_in * inch
    img.drawHeight = (pil_img.height / pil_img.width) * img.drawWidth
    return img


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def infer_corte(df_detalle: pd.DataFrame) -> str:
    if df_detalle.empty:
        return ""
    tmp = df_detalle.copy()
    tmp["Corte"] = tmp["Fecha"].astype(str).str.strip() + " " + tmp["Hora"].astype(str).str.strip()
    tmp["Corte"] = tmp["Corte"].str.strip()
    tmp = tmp[tmp["Corte"] != ""]
    if tmp.empty:
        return ""
    return tmp["Corte"].value_counts().idxmax()


def extraer_num_region(region_name: str) -> str:
    m = re.search(r"Regi[oó]n\s*([0-9]{1,2})", str(region_name), re.IGNORECASE)
    return m.group(1) if m else str(region_name)


def build_pdf_report(
    region_name: str,
    df_detalle: pd.DataFrame,
    df_tipo: pd.DataFrame,
    df_deleg: pd.DataFrame,
    logo_pil: Optional[Image.Image],
    titulo_base: str,
    subtitulo: str,
    verde_desde: float,
    naranja_desde: float,
) -> bytes:
    generado_dt = datetime.now()
    generado_str = generado_dt.strftime("%d/%m/%Y")  # SIN hora
    corte = infer_corte(df_detalle)

    titulo_final = f"{titulo_base} – {region_name}"
    num_region = extraer_num_region(region_name)

    criterio = (
        f"Criterio de color: Verde = avance ≥ {int(verde_desde)}%, "
        f"Amarillo = avance ≥ {int(naranja_desde)}% y < {int(verde_desde)}%, "
        f"Rojo = avance < {int(naranja_desde)}%."
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=36, rightMargin=36,
        topMargin=36, bottomMargin=36
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleCenter", parent=styles["Heading1"], fontSize=16, spaceAfter=6, alignment=1))
    styles.add(ParagraphStyle(name="SubCenter", parent=styles["Heading2"], fontSize=12, spaceAfter=10, alignment=1))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontSize=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="Tiny", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="H3x", parent=styles["Heading3"], fontSize=11, spaceAfter=6))

    elems = []

    # -------- Header: logo + titulo centrado --------
    if logo_pil:
        header_tbl = Table(
            [[
                rl_image_from_pil(logo_pil, 1.10),
                Paragraph(f"<b>{titulo_final}</b>", styles["TitleCenter"])
            ]],
            colWidths=[1.35 * inch, 5.65 * inch]
        )
        header_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elems.append(header_tbl)
        elems.append(Paragraph(subtitulo, styles["SubCenter"]))
    else:
        elems.append(Paragraph(f"<b>{titulo_final}</b>", styles["TitleCenter"]))
        elems.append(Paragraph(subtitulo, styles["SubCenter"]))

    # 1 texto por renglón + "Región {número}"
    linea_1 = (
        f"<b>Este reporte fue generado el día</b> {generado_str} "
        f"<b>para el Director(a) Regional de la Dirección Regional </b> {num_region}."
    )
    elems.append(Paragraph(linea_1, styles["Small"]))

    if corte:
        linea_2 = f"<b>Los datos para este reporte fueron tomados el día</b> {corte}"
        elems.append(Paragraph(linea_2, styles["Small"]))

    elems.append(Spacer(1, 10))

    # =========================
    # Cuadro 1. Resumen por tipo
    # =========================
    elems.append(Paragraph("Detalle a nivel regional", styles["H2x"]))

    df_tipo = df_tipo.copy().sort_values("Tipo")
    df_tipo["Estado"] = df_tipo["% Avance"].apply(lambda p: etiqueta_por_porcentaje(float(p), verde_desde, naranja_desde))

    data1 = [["Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Estado"]]
    for _, r in df_tipo.iterrows():
        data1.append([
            str(r["Tipo"]),
            fmt_int(r["Meta"]),
            fmt_int(r["Contabilidad"]),
            fmt_int(r["Pendiente"]),
            f'{float(r["% Avance"]):.1f}%',
            str(r["Estado"])
        ])

    tbl1 = Table(data1, repeatRows=1, colWidths=[1.2*inch, 1.0*inch, 1.2*inch, 1.0*inch, 0.9*inch, 0.8*inch])
    style1 = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_2),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data1)):
        p = float(df_tipo.iloc[i-1]["% Avance"])
        c = color_por_porcentaje(p, verde_desde, naranja_desde)
        style1.append(("BACKGROUND", (-2, i), (-2, i), c))
        style1.append(("TEXTCOLOR", (-2, i), (-2, i), colors.white))
        style1.append(("FONTNAME", (-2, i), (-2, i), "Helvetica-Bold"))
        style1.append(("BACKGROUND", (-1, i), (-1, i), c))
        style1.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
        style1.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))

        contab_val = int(df_tipo.iloc[i-1]["Contabilidad"])
        if contab_val == 0:
            style1.append(("BACKGROUND", (2, i), (2, i), COLOR_ROJO))
            style1.append(("TEXTCOLOR", (2, i), (2, i), colors.white))
            style1.append(("FONTNAME", (2, i), (2, i), "Helvetica-Bold"))

    tbl1.setStyle(TableStyle(style1))
    elems.append(tbl1)
    elems.append(Spacer(1, 10))

    # =========================
    # ✅ NUEVO: Totales regionales (Meta/Contabilidad/Pendiente/%/Estado)
    # =========================
    total_meta = int(df_tipo["Meta"].sum()) if not df_tipo.empty else 0
    total_contab = int(df_tipo["Contabilidad"].sum()) if not df_tipo.empty else 0
    total_pend = int(df_tipo["Pendiente"].sum()) if not df_tipo.empty else 0
    total_pct = (total_contab / total_meta * 100.0) if total_meta else 0.0
    total_estado = etiqueta_por_porcentaje(float(total_pct), verde_desde, naranja_desde)
    total_color = color_por_porcentaje(float(total_pct), verde_desde, naranja_desde)

    elems.append(Paragraph("Totales regionales", styles["H3x"]))

    data_tot = [
        ["Meta total", "Contabilidad total", "Pendiente total", "% Avance total", "Estado total"],
        [fmt_int(total_meta), fmt_int(total_contab), fmt_int(total_pend), f"{total_pct:.1f}%", total_estado]
    ]
    tbl_tot = Table(data_tot, repeatRows=1, colWidths=[1.2*inch, 1.6*inch, 1.2*inch, 1.2*inch, 1.0*inch])
    style_tot = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_2),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (3, 1), (3, 1), total_color),
        ("TEXTCOLOR", (3, 1), (3, 1), colors.white),
        ("BACKGROUND", (4, 1), (4, 1), total_color),
        ("TEXTCOLOR", (4, 1), (4, 1), colors.white),
    ]
    tbl_tot.setStyle(TableStyle(style_tot))
    elems.append(tbl_tot)
    elems.append(Spacer(1, 12))

    # =========================
    # Cuadro 2. Detalle por delegación
    # =========================
    elems.append(Paragraph("Detalle por delegación", styles["H2x"]))

    det = df_deleg.copy().sort_values(["Delegación", "Tipo"])
    det["Estado"] = det["% Avance"].apply(lambda p: etiqueta_por_porcentaje(float(p), verde_desde, naranja_desde))

    data2 = [["Delegación", "Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Estado"]]
    for _, r in det.iterrows():
        data2.append([
            str(r["Delegación"]),
            str(r["Tipo"]),
            fmt_int(r["Meta"]),
            fmt_int(r["Contabilidad"]),
            fmt_int(r["Pendiente"]),
            f'{float(r["% Avance"]):.1f}%',
            str(r["Estado"])
        ])

    tbl2 = Table(data2, repeatRows=1, colWidths=[2.1*inch, 0.9*inch, 0.9*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.7*inch])
    style2 = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data2)):
        p = float(det.iloc[i-1]["% Avance"])
        c = color_por_porcentaje(p, verde_desde, naranja_desde)
        style2.append(("BACKGROUND", (-2, i), (-2, i), c))
        style2.append(("TEXTCOLOR", (-2, i), (-2, i), colors.white))
        style2.append(("FONTNAME", (-2, i), (-2, i), "Helvetica-Bold"))
        style2.append(("BACKGROUND", (-1, i), (-1, i), c))
        style2.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
        style2.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))

        contab_val = int(det.iloc[i-1]["Contabilidad"])
        if contab_val == 0:
            style2.append(("BACKGROUND", (3, i), (3, i), COLOR_ROJO))
            style2.append(("TEXTCOLOR", (3, i), (3, i), colors.white))
            style2.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))

    tbl2.setStyle(TableStyle(style2))
    elems.append(tbl2)
    elems.append(Spacer(1, 10))

    elems.append(Paragraph(criterio, styles["Tiny"]))

    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


# =========================
# UI
# =========================
st.title("📄 Reporte Regional – Encuestas (Comunidad / Comercio / Policial)")

with st.sidebar:
    st.header("Configuración del informe")
    titulo_base = st.text_input("Título base del informe", value="Reporte Regional – Avance de Encuestas")
    subtitulo = st.text_input("Subtítulo", value="Comunidad / Comercio / Policial")

    st.markdown("---")
    st.subheader("Región del lote (un solo click)")
    region_name = st.selectbox(
        "Seleccione la región para estos PDFs",
        options=REGIONES_CATALOGO,
        index=6
    )

    st.markdown("---")
    st.subheader("Logo")
    st.caption("Si existe '001.png' en el repo, se carga solo. También puedes subir otro logo aquí.")

    logo_img = None
    if os.path.exists("001.png"):
        try:
            logo_img = Image.open("001.png").convert("RGBA")
            st.image(logo_img, caption="Logo (001.png) detectado", use_container_width=True)
        except Exception:
            st.warning("Encontré 001.png pero no pude abrirlo. Subí el logo manualmente.")

    logo_file = st.file_uploader("Subir logo (opcional)", type=["png", "jpg", "jpeg"], accept_multiple_files=False)
    if logo_file:
        try:
            logo_img = Image.open(logo_file).convert("RGBA")
            st.image(logo_img, caption="Logo cargado manualmente", use_container_width=True)
        except Exception:
            st.warning("No pude leer el logo subido.")

    st.markdown("---")
    st.subheader("Colores por % de avance")
    verde_desde = st.number_input("Verde desde (%)", min_value=0, max_value=100, value=80, step=1)
    naranja_desde = st.number_input("Naranja desde (%)", min_value=0, max_value=100, value=40, step=1)

st.markdown("### 1) Suba los PDFs del reporte (ya agrupados por región)")
pdf_files = st.file_uploader(
    "Puede subir varios PDFs a la vez",
    type=["pdf"],
    accept_multiple_files=True
)

if not pdf_files:
    st.info("Suba los PDFs para empezar.")
    st.stop()

all_dfs = []
parse_errors = []

for f in pdf_files:
    try:
        b = f.read()
        _, df = parse_pdf_report(f.name, b)
        all_dfs.append(df)
    except Exception as e:
        parse_errors.append((f.name, str(e)))

if parse_errors:
    st.error("Algunos PDFs no se pudieron procesar:")
    for name, err in parse_errors:
        st.write(f"- {name}: {err}")

df_all = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

st.markdown("### 2) Datos detectados (desde los PDFs)")
if df_all.empty:
    st.warning("No se detectaron filas. (Esto suele pasar si el PDF viene como imagen escaneada).")
    st.stop()

df_all["Región"] = region_name

st.success(f"Filas detectadas: {len(df_all)}")
st.dataframe(df_all.sort_values(["Delegación", "Tipo", "Distrito"]), use_container_width=True, height=320)

st.markdown("### 3) Resumen (pantalla)")
df_tipo = agg_tipo(df_all)
df_deleg = agg_delegacion_tipo(df_all)
st.dataframe(df_tipo.sort_values("Tipo"), use_container_width=True)

st.markdown("### 4) Generar PDF")
if st.button("🧾 Generar informe PDF", type="primary"):
    pdf_bytes = build_pdf_report(
        region_name=region_name,
        df_detalle=df_all,
        df_tipo=df_tipo,
        df_deleg=df_deleg,
        logo_pil=logo_img,
        titulo_base=titulo_base,
        subtitulo=subtitulo,
        verde_desde=float(verde_desde),
        naranja_desde=float(naranja_desde),
    )
    st.success("PDF generado.")
    st.download_button(
        "⬇️ Descargar informe PDF",
        data=pdf_bytes,
        file_name=f"Informe_{region_name.replace(' ', '_')}_Encuestas.pdf",
        mime="application/pdf"
    )



