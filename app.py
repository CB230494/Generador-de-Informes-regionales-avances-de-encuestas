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
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch


# =========================
# Config
# =========================
st.set_page_config(page_title="Reporte Regional - Encuestas", layout="wide")

TIPOS_VALIDOS = ["Comunidad", "Comercio", "Policial"]

# Catálogo de regiones (12), con Región 1 dividida en 3
REGIONES_CATALOGO = [
    "Región 1 Norte",
    "Región 1 Sur",
    "Región 1 Central",
    "Región 2",
    "Región 3",
    "Región 4",
    "Región 5",
    "Región 6",
    "Región 7",
    "Región 8",
    "Región 9",
    "Región 10",
    "Región 11",
    "Región 12",
]

# Colores institucionales
COLOR_VERDE = colors.HexColor("#1B5E20")
COLOR_NARANJA = colors.HexColor("#E65100")
COLOR_ROJO = colors.HexColor("#B71C1C")
COLOR_ENCABEZADO = colors.HexColor("#263238")   # gris azulado oscuro
COLOR_ENCABEZADO_2 = colors.HexColor("#37474F") # variante


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
    """
    Devuelve el bloque de texto entre 'Comunidad' y el siguiente título (Comercio/Policial)
    o hasta el final.
    """
    t = text.replace("\r", "\n")

    # Inicio
    m_start = re.search(rf"\n{re.escape(section_name)}\n", t, re.IGNORECASE)
    if not m_start:
        m_start = re.search(rf"{re.escape(section_name)}\n", t, re.IGNORECASE)
        if not m_start:
            return ""

    start_idx = m_start.end()

    # Fin
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
    """
    Parser robusto: detecta filas aunque el PDF rompa líneas.
    """
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
# Helpers - Aggregations
# =========================
def agg_region_tipo(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["Región", "Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()

    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    return g


def agg_region_delegacion_tipo(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["Región", "Delegación", "Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()

    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    return g


# =========================
# Helpers - Color rules
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
# PDF Builder
# =========================
def rl_image_from_pil(pil_img: Image.Image, width_in: float = 1.15) -> RLImage:
    bio = io.BytesIO()
    pil_img.save(bio, format="PNG")
    bio.seek(0)
    img = RLImage(bio)
    img.drawWidth = width_in * inch
    img.drawHeight = (pil_img.height / pil_img.width) * img.drawWidth
    return img


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _infer_fecha_corte(df_detalle: pd.DataFrame) -> str:
    """
    Intenta mostrar una "fecha de corte" basada en el encabezado del PDF.
    Si hay varias, muestra la más frecuente.
    """
    if df_detalle.empty:
        return ""
    # Combina Fecha + Hora del PDF
    tmp = df_detalle.copy()
    tmp["Corte"] = tmp["Fecha"].astype(str).str.strip() + " " + tmp["Hora"].astype(str).str.strip()
    tmp["Corte"] = tmp["Corte"].str.strip()
    tmp = tmp[tmp["Corte"] != ""]
    if tmp.empty:
        return ""
    return tmp["Corte"].value_counts().idxmax()


def build_pdf_report(
    df_detalle: pd.DataFrame,
    df_regional: pd.DataFrame,
    df_por_deleg: pd.DataFrame,
    logo_pil: Optional[Image.Image],
    titulo: str,
    subtitulo: str,
    verde_desde: float,
    naranja_desde: float,
) -> bytes:
    generado_dt = datetime.now()
    generado_str = generado_dt.strftime("%d/%m/%Y %H:%M")

    fecha_corte = _infer_fecha_corte(df_detalle)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1x", parent=styles["Heading1"], fontSize=16, spaceAfter=6))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontSize=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="Tiny", parent=styles["BodyText"], fontSize=8, leading=10))

    elems = []

    # Encabezado con logo
    header_tbl = []
    if logo_pil:
        header_tbl.append([
            rl_image_from_pil(logo_pil, 1.1),
            Paragraph(f"<b>{titulo}</b><br/>{subtitulo}", styles["H1x"])
        ])
        colw = [1.35 * inch, 5.65 * inch]
    else:
        header_tbl.append([Paragraph(f"<b>{titulo}</b><br/>{subtitulo}", styles["H1x"])])
        colw = [7.0 * inch]

    t = Table(header_tbl, colWidths=colw)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(t)

    # Fecha/hora de generación + corte
    info_lines = [f"<b>Generado:</b> {generado_str}"]
    if fecha_corte:
        info_lines.append(f"<b>Corte (según PDFs):</b> {fecha_corte}")
    elems.append(Paragraph(" &nbsp;&nbsp; | &nbsp;&nbsp; ".join(info_lines), styles["Small"]))
    elems.append(Spacer(1, 10))

    # ===== Resumen regional (SIN paréntesis) =====
    elems.append(Paragraph("Resumen regional", styles["H2x"]))

    reg = df_regional.copy().sort_values(["Región", "Tipo"])
    reg["Estado"] = reg["% Avance"].apply(lambda p: etiqueta_por_porcentaje(float(p), verde_desde, naranja_desde))

    data = [["Región", "Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Estado"]]
    for _, r in reg.iterrows():
        data.append([
            str(r["Región"]).strip(),
            str(r["Tipo"]),
            fmt_int(r["Meta"]),
            fmt_int(r["Contabilidad"]),
            fmt_int(r["Pendiente"]),
            f'{float(r["% Avance"]):.1f}%',
            str(r["Estado"])
        ])

    tbl = Table(data, repeatRows=1, colWidths=[1.8*inch, 1.0*inch, 0.9*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.7*inch])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
    ]

    # Colorear la columna Estado según % Avance
    for i in range(1, len(data)):
        p = float(reg.iloc[i-1]["% Avance"])
        c = color_por_porcentaje(p, verde_desde, naranja_desde)
        style_cmds.append(("BACKGROUND", (-1, i), (-1, i), c))
        style_cmds.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
        style_cmds.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))

        # Opcional: también colorear la celda de % Avance (para que se note más)
        style_cmds.append(("BACKGROUND", (-2, i), (-2, i), c))
        style_cmds.append(("TEXTCOLOR", (-2, i), (-2, i), colors.white))
        style_cmds.append(("FONTNAME", (-2, i), (-2, i), "Helvetica-Bold"))

    tbl.setStyle(TableStyle(style_cmds))
    elems.append(tbl)
    elems.append(Spacer(1, 8))

    # Criterio de color (AHORA NO QUEDA ABAJO DEL TODO)
    criterio = (
        f"Criterio de color: Verde = avance ≥ {int(verde_desde)}%, "
        f"Naranja = avance ≥ {int(naranja_desde)}% y < {int(verde_desde)}%, "
        f"Rojo = avance < {int(naranja_desde)}%."
    )
    elems.append(Paragraph(criterio, styles["Tiny"]))
    elems.append(Spacer(1, 10))

    # ===== Páginas por Región =====
    regiones = sorted([r for r in df_por_deleg["Región"].dropna().unique().tolist() if str(r).strip()])

    for region in regiones:
        elems.append(PageBreak())
        region_name = str(region).strip()  # asegura que salga "Región 5", etc.
        elems.append(Paragraph(region_name, styles["H1x"]))

        # Sub-resumen de la región
        sub = reg[reg["Región"] == region].sort_values("Tipo")
        sub_data = [["Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Estado"]]
        for _, r in sub.iterrows():
            sub_data.append([
                str(r["Tipo"]),
                fmt_int(r["Meta"]),
                fmt_int(r["Contabilidad"]),
                fmt_int(r["Pendiente"]),
                f'{float(r["% Avance"]):.1f}%',
                str(r["Estado"])
            ])

        sub_tbl = Table(sub_data, repeatRows=1, colWidths=[1.2*inch, 1.0*inch, 1.2*inch, 1.0*inch, 0.9*inch, 0.8*inch])
        sub_style = [
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO_2),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]
        for i in range(1, len(sub_data)):
            p = float(sub.iloc[i-1]["% Avance"])
            c = color_por_porcentaje(p, verde_desde, naranja_desde)
            sub_style.append(("BACKGROUND", (-1, i), (-1, i), c))
            sub_style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
            sub_style.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))
            sub_style.append(("BACKGROUND", (-2, i), (-2, i), c))
            sub_style.append(("TEXTCOLOR", (-2, i), (-2, i), colors.white))
            sub_style.append(("FONTNAME", (-2, i), (-2, i), "Helvetica-Bold"))
        sub_tbl.setStyle(TableStyle(sub_style))
        elems.append(sub_tbl)
        elems.append(Spacer(1, 10))

        # Detalle por delegación
        det = df_por_deleg[df_por_deleg["Región"] == region].copy().sort_values(["Delegación", "Tipo"])
        det["Estado"] = det["% Avance"].apply(lambda p: etiqueta_por_porcentaje(float(p), verde_desde, naranja_desde))

        det_data = [["Delegación", "Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Estado"]]
        for _, r in det.iterrows():
            det_data.append([
                str(r["Delegación"]),
                str(r["Tipo"]),
                fmt_int(r["Meta"]),
                fmt_int(r["Contabilidad"]),
                fmt_int(r["Pendiente"]),
                f'{float(r["% Avance"]):.1f}%',
                str(r["Estado"])
            ])

        det_tbl = Table(det_data, repeatRows=1, colWidths=[2.1*inch, 0.9*inch, 0.9*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.7*inch])
        det_style = [
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_ENCABEZADO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for i in range(1, len(det_data)):
            p = float(det.iloc[i-1]["% Avance"])
            c = color_por_porcentaje(p, verde_desde, naranja_desde)
            det_style.append(("BACKGROUND", (-1, i), (-1, i), c))
            det_style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
            det_style.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))
            det_style.append(("BACKGROUND", (-2, i), (-2, i), c))
            det_style.append(("TEXTCOLOR", (-2, i), (-2, i), colors.white))
            det_style.append(("FONTNAME", (-2, i), (-2, i), "Helvetica-Bold"))
        det_tbl.setStyle(TableStyle(det_style))
        elems.append(det_tbl)
        elems.append(Spacer(1, 12))

        # Criterio de color (en cada región pero con espacio, no abajo pegado)
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
    titulo = st.text_input("Título del informe", value="Reporte Regional – Avance de Encuestas")
    subtitulo = st.text_input("Subtítulo", value="Comunidad / Comercio / Policial")

    st.markdown("---")
    st.subheader("Logo")
    st.caption("Si existe '001.png' en el repo, se carga solo. También puedes subir otro logo aquí.")

    logo_img = None

    if os.path.exists("001.png"):
        try:
            logo_img = Image.open("001.png").convert("RGBA")
            st.image(logo_img, caption="Logo (001.png) detectado en el repo", use_container_width=True)
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
    st.caption("Esto pinta celdas del PDF (verde/naranja/rojo).")
    verde_desde = st.number_input("Verde desde (%)", min_value=0, max_value=100, value=80, step=1)
    naranja_desde = st.number_input("Naranja desde (%)", min_value=0, max_value=100, value=40, step=1)

st.markdown("### 1) Suba los PDFs de reporte por delegación")
pdf_files = st.file_uploader(
    "Puede subir varios PDFs a la vez",
    type=["pdf"],
    accept_multiple_files=True
)

if not pdf_files:
    st.info("Suba los PDFs para empezar.")
    st.stop()

# Parse PDFs
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

st.success(f"Filas detectadas: {len(df_all)}")
st.dataframe(df_all.sort_values(["Delegación", "Tipo", "Distrito"]), use_container_width=True, height=320)

# Asignación de Región (dropdown)
st.markdown("### 3) Asignación de Región (catálogo)")
st.caption("Seleccione la región por delegación (sin escribir).")

delegaciones = sorted(df_all["Delegación"].dropna().unique().tolist())
base_map = pd.DataFrame({"Delegación": delegaciones, "Región": [""] * len(delegaciones)})

# Recuperar selecciones anteriores
if "region_map" in st.session_state:
    prev = st.session_state["region_map"]
    base_map = base_map.merge(prev, on="Delegación", how="left", suffixes=("", "_prev"))
    base_map["Región"] = base_map["Región_prev"].fillna(base_map["Región"])
    base_map = base_map[["Delegación", "Región"]]

# Editor
edited = st.data_editor(
    base_map,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "Delegación": st.column_config.TextColumn("Delegación", disabled=True),
        "Región": st.column_config.SelectboxColumn(
            "Región",
            options=[""] + REGIONES_CATALOGO,
            help="Seleccione la región correcta. Incluye Región 1 Norte/Sur/Central."
        ),
    }
)
st.session_state["region_map"] = edited.copy()

df = df_all.merge(edited, on="Delegación", how="left")
df["Región"] = df["Región"].fillna("").astype(str).str.strip()

missing = (df["Región"] == "").sum()
if missing > 0:
    st.warning("Hay delegaciones sin región asignada. El PDF solo consolidará las que tengan región.")

df_use = df[df["Región"] != ""].copy()
if df_use.empty:
    st.info("Asigne al menos una región para generar el PDF.")
    st.stop()

# Resumen en pantalla
st.markdown("### 4) Resumen regional (pantalla)")
df_reg = agg_region_tipo(df_use)
df_deleg = agg_region_delegacion_tipo(df_use)

df_reg_show = df_reg.copy()
df_reg_show["Estado"] = df_reg_show["% Avance"].apply(lambda p: etiqueta_por_porcentaje(float(p), float(verde_desde), float(naranja_desde)))
st.dataframe(df_reg_show.sort_values(["Región", "Tipo"]), use_container_width=True)

# Generar PDF
st.markdown("### 5) Generar PDF consolidado")

if st.button("🧾 Generar informe PDF", type="primary"):
    pdf_bytes = build_pdf_report(
        df_detalle=df_use,
        df_regional=df_reg,
        df_por_deleg=df_deleg,
        logo_pil=logo_img,
        titulo=titulo,
        subtitulo=subtitulo,
        verde_desde=float(verde_desde),
        naranja_desde=float(naranja_desde),
    )
    st.success("PDF generado.")
    st.download_button(
        "⬇️ Descargar informe PDF",
        data=pdf_bytes,
        file_name="Informe_Avance_Regional_Encuestas.pdf",
        mime="application/pdf"
    )
