# -*- coding: utf-8 -*-
import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

# PDF text extraction
from pypdf import PdfReader

# PDF creation
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
st.set_page_config(page_title="Reporte Regional - Encuestas (PDF)", layout="wide")

SEM_GREEN_MIN = 80  # >= 80% verde
SEM_ORANGE_MIN = 40 # 40-79% naranja
# < 40% rojo

TIPOS_VALIDOS = ["Comunidad", "Comercio", "Policial"]


# =========================
# Helpers - Parsing
# =========================
@dataclass
class ParsedHeader:
    delegacion: str
    fecha: str
    hora: str


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    text_all = []
    for page in reader.pages:
        t = page.extract_text() or ""
        text_all.append(t)
    return "\n".join(text_all)


def _parse_header(text: str) -> ParsedHeader:
    # Delegación: San Carlos Oeste
    # Hora del reporte: 13:35
    # Fecha: miércoles, 4 de marzo de 2026
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


def _section_lines(text: str, section_name: str) -> List[str]:
    """
    Extract lines between section_name and next section.
    Sections in your PDFs appear as:
    Comunidad
    ...
    Comercio
    ...
    Policial
    ...
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    idxs = {name: None for name in TIPOS_VALIDOS}
    for i, ln in enumerate(lines):
        for name in TIPOS_VALIDOS:
            if ln.lower() == name.lower():
                idxs[name] = i

    start = idxs.get(section_name)
    if start is None:
        return []

    # find next section start after 'start'
    next_starts = []
    for name in TIPOS_VALIDOS:
        j = idxs.get(name)
        if j is not None and j > start:
            next_starts.append(j)
    end = min(next_starts) if next_starts else len(lines)

    return lines[start + 1: end]


def _parse_table_lines(section: str, header: ParsedHeader, section_lines: List[str]) -> List[Dict]:
    """
    Parses lines like:
    Comunidad La Fortuna 155 6 4% 149
    Comercio San Carlos Oeste 231 3 1% 228
    Policial San Carlos Oeste 94 42 45% 52
    """
    rows = []
    if not section_lines:
        return rows

    # If section says "No hay registros..."
    if any("no hay registros" in ln.lower() for ln in section_lines):
        return rows

    # remove header line if present
    # "Tipo Distrito Meta Contabilidad % Avance Pendiente"
    cleaned = []
    for ln in section_lines:
        if re.search(r"Tipo\s+Distrito\s+Meta\s+Contabilidad", ln, re.IGNORECASE):
            continue
        cleaned.append(ln)

    for ln in cleaned:
        # Only parse lines that begin with the section word or a known tipo
        # In your PDFs: line starts with the same as section (Comunidad/Comercio/Policial)
        toks = ln.split()
        if len(toks) < 6:
            continue

        tipo = toks[0].strip()
        if tipo.lower() not in [t.lower() for t in TIPOS_VALIDOS]:
            continue

        # last tokens: Meta Contabilidad %Avance Pendiente
        # Example: 155 6 4% 149
        try:
            pendiente = int(toks[-1])
            avance_str = toks[-2]
            contabilidad = int(toks[-3])
            meta = int(toks[-4])
            distrito = " ".join(toks[1:-4]).strip()
        except Exception:
            continue

        # normalize percent
        avance_pct = None
        m = re.match(r"^([0-9]+)\s*%$", avance_str.strip())
        if m:
            avance_pct = float(m.group(1))
        else:
            # fallback if "4%" without space
            m2 = re.search(r"([0-9]+)\s*%$", avance_str.strip())
            avance_pct = float(m2.group(1)) if m2 else None

        rows.append({
            "Delegación": header.delegacion,
            "Fecha": header.fecha,
            "Hora": header.hora,
            "Tipo": tipo,
            "Distrito": distrito,
            "Meta": meta,
            "Contabilidad": contabilidad,
            "% Avance": avance_pct if avance_pct is not None else 0.0,
            "Pendiente": pendiente,
        })

    return rows


def parse_pdf_report(file_name: str, file_bytes: bytes) -> Tuple[ParsedHeader, pd.DataFrame]:
    text = _extract_text_from_pdf(file_bytes)
    header = _parse_header(text)

    all_rows = []
    for sec in TIPOS_VALIDOS:
        lines = _section_lines(text, sec)
        all_rows.extend(_parse_table_lines(sec, header, lines))

    df = pd.DataFrame(all_rows)
    if df.empty:
        # Keep at least header info
        df = pd.DataFrame(columns=["Delegación", "Fecha", "Hora", "Tipo", "Distrito", "Meta", "Contabilidad", "% Avance", "Pendiente"])
    df["Archivo"] = file_name
    return header, df


# =========================
# Helpers - Metrics & Semáforo
# =========================
def semaforo_color(avance_pct: float):
    if avance_pct >= SEM_GREEN_MIN:
        return colors.HexColor("#1B5E20")  # verde oscuro
    if avance_pct >= SEM_ORANGE_MIN:
        return colors.HexColor("#E65100")  # naranja
    return colors.HexColor("#B71C1C")      # rojo


def semaforo_label(avance_pct: float) -> str:
    if avance_pct >= SEM_GREEN_MIN:
        return "ALTO"
    if avance_pct >= SEM_ORANGE_MIN:
        return "MEDIO"
    return "BAJO"


def agg_region_tipo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate by Región + Tipo with weighted progress:
    avance% = (sum(contabilidad)/sum(meta))*100
    """
    g = df.groupby(["Región", "Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()

    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    g["Semáforo"] = g["% Avance"].apply(semaforo_label)
    return g


def agg_region_delegacion_tipo(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["Región", "Delegación", "Tipo"], dropna=False).agg(
        Meta=("Meta", "sum"),
        Contabilidad=("Contabilidad", "sum"),
        Pendiente=("Pendiente", "sum"),
    ).reset_index()
    g["% Avance"] = g.apply(lambda r: (r["Contabilidad"] / r["Meta"] * 100.0) if r["Meta"] else 0.0, axis=1)
    g["Semáforo"] = g["% Avance"].apply(semaforo_label)
    return g


# =========================
# PDF Builder (ReportLab)
# =========================
def _rl_image_from_pil(pil_img: Image.Image, width_in: float = 1.2) -> RLImage:
    bio = io.BytesIO()
    pil_img.save(bio, format="PNG")
    bio.seek(0)
    img = RLImage(bio)
    img.drawWidth = width_in * inch
    img.drawHeight = (pil_img.height / pil_img.width) * img.drawWidth
    return img


def build_pdf_report(
    df_detalle: pd.DataFrame,
    df_regional: pd.DataFrame,
    df_por_deleg: pd.DataFrame,
    logo_pil: Optional[Image.Image],
    titulo: str,
    subtitulo: str,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=16, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], fontSize=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="Tiny", parent=styles["BodyText"], fontSize=8, leading=10))

    elems = []

    # Header
    header_tbl = []
    if logo_pil:
        header_tbl.append([_rl_image_from_pil(logo_pil, width_in=1.1),
                           Paragraph(f"<b>{titulo}</b><br/>{subtitulo}", styles["H1"])])
    else:
        header_tbl.append([Paragraph(f"<b>{titulo}</b><br/>{subtitulo}", styles["H1"]), ""])

    t = Table(header_tbl, colWidths=[1.3 * inch, 5.7 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 10))

    # Resumen regional
    elems.append(Paragraph("Resumen regional (Meta / Contabilidad / Pendiente / % Avance)", styles["H2"]))

    reg = df_regional.copy()
    reg = reg.sort_values(["Región", "Tipo"])

    data = [["Región", "Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Semáforo"]]
    for _, r in reg.iterrows():
        data.append([
            str(r["Región"]),
            str(r["Tipo"]),
            f'{int(r["Meta"]):,}'.replace(",", "."),
            f'{int(r["Contabilidad"]):,}'.replace(",", "."),
            f'{int(r["Pendiente"]):,}'.replace(",", "."),
            f'{r["% Avance"]:.1f}%',
            str(r["Semáforo"]),
        ])

    tbl = Table(data, repeatRows=1, colWidths=[1.6*inch, 1.0*inch, 0.9*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.8*inch])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263238")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
    ]

    # semáforo color cell (last column)
    for i in range(1, len(data)):
        pct = float(reg.iloc[i-1]["% Avance"])
        style_cmds.append(("BACKGROUND", (-1, i), (-1, i), semaforo_color(pct)))
        style_cmds.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
        style_cmds.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))

    tbl.setStyle(TableStyle(style_cmds))
    elems.append(tbl)
    elems.append(Spacer(1, 12))

    # Por región: detalle por delegación
    regiones = [r for r in df_por_deleg["Región"].dropna().unique().tolist()]
    regiones = sorted(regiones)

    if not regiones:
        elems.append(Paragraph("<i>No hay regiones asignadas aún. Asigne regiones en la app y vuelva a generar.</i>", styles["Small"]))
    else:
        for region in regiones:
            elems.append(PageBreak())
            elems.append(Paragraph(f"Región: {region}", styles["H1"]))

            # Sub-resumen región
            sub = df_regional[df_regional["Región"] == region].sort_values("Tipo")
            sub_data = [["Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Semáforo"]]
            for _, r in sub.iterrows():
                sub_data.append([
                    str(r["Tipo"]),
                    f'{int(r["Meta"]):,}'.replace(",", "."),
                    f'{int(r["Contabilidad"]):,}'.replace(",", "."),
                    f'{int(r["Pendiente"]):,}'.replace(",", "."),
                    f'{r["% Avance"]:.1f}%',
                    str(r["Semáforo"])
                ])

            sub_tbl = Table(sub_data, repeatRows=1, colWidths=[1.2*inch, 1.0*inch, 1.2*inch, 1.0*inch, 0.9*inch, 0.9*inch])
            sub_style = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#37474F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
            for i in range(1, len(sub_data)):
                pct = float(sub.iloc[i-1]["% Avance"])
                sub_style.append(("BACKGROUND", (-1, i), (-1, i), semaforo_color(pct)))
                sub_style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
                sub_style.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))
            sub_tbl.setStyle(TableStyle(sub_style))
            elems.append(sub_tbl)
            elems.append(Spacer(1, 12))

            # Tabla por delegación y tipo
            det = df_por_deleg[df_por_deleg["Región"] == region].copy()
            det = det.sort_values(["Delegación", "Tipo"])
            det_data = [["Delegación", "Tipo", "Meta", "Contabilidad", "Pendiente", "% Avance", "Semáforo"]]
            for _, r in det.iterrows():
                det_data.append([
                    str(r["Delegación"]),
                    str(r["Tipo"]),
                    f'{int(r["Meta"]):,}'.replace(",", "."),
                    f'{int(r["Contabilidad"]):,}'.replace(",", "."),
                    f'{int(r["Pendiente"]):,}'.replace(",", "."),
                    f'{r["% Avance"]:.1f}%',
                    str(r["Semáforo"])
                ])

            det_tbl = Table(det_data, repeatRows=1, colWidths=[2.0*inch, 0.9*inch, 0.9*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.8*inch])
            det_style = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263238")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
            for i in range(1, len(det_data)):
                pct = float(det.iloc[i-1]["% Avance"])
                det_style.append(("BACKGROUND", (-1, i), (-1, i), semaforo_color(pct)))
                det_style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.white))
                det_style.append(("FONTNAME", (-1, i), (-1, i), "Helvetica-Bold"))
            det_tbl.setStyle(TableStyle(det_style))
            elems.append(det_tbl)
            elems.append(Spacer(1, 12))

            # Nota metodológica
            elems.append(Paragraph(
                "Nota: El % de avance se calcula como (Contabilidad total / Meta total) × 100, ponderado por la meta.",
                styles["Tiny"]
            ))

    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


# =========================
# UI
# =========================
st.title("📄 Reporte Regional – Encuestas (Comunidad / Comercio / Policial)")

with st.sidebar:
    st.header("Configuración")
    titulo = st.text_input("Título del informe", value="Informe de Avance Regional – Encuestas")
    subtitulo = st.text_input("Subtítulo", value="Comunidad / Comercio / Policial")

    st.markdown("---")
    st.subheader("Logo (PNG/JPG)")
    logo_file = st.file_uploader("Cargar logo", type=["png", "jpg", "jpeg"], accept_multiple_files=False)
    logo_img = None
    if logo_file:
        try:
            logo_img = Image.open(logo_file).convert("RGBA")
            st.image(logo_img, caption="Logo cargado", use_container_width=True)
        except Exception:
            st.warning("No pude leer el logo. Intente con PNG/JPG.")

    st.markdown("---")
    st.subheader("Semáforo de avance")
    c1, c2 = st.columns(2)
    with c1:
        green_min = st.number_input("Verde desde (%)", min_value=0, max_value=100, value=SEM_GREEN_MIN, step=1)
    with c2:
        orange_min = st.number_input("Naranja desde (%)", min_value=0, max_value=100, value=SEM_ORANGE_MIN, step=1)
    # Update globals locally (simple approach)
    SEM_GREEN_MIN = float(green_min)
    SEM_ORANGE_MIN = float(orange_min)

st.markdown("### 1) Suba los PDFs de reporte por delegación")
pdf_files = st.file_uploader(
    "Puede subir varios PDFs a la vez",
    type=["pdf"],
    accept_multiple_files=True
)

if not pdf_files:
    st.info("Suba los PDFs para empezar. (Formato como los reportes que ya estás generando).")
    st.stop()

# Parse PDFs
all_dfs = []
headers = []
parse_errors = []

for f in pdf_files:
    try:
        b = f.read()
        header, df = parse_pdf_report(f.name, b)
        headers.append(header)
        all_dfs.append(df)
    except Exception as e:
        parse_errors.append((f.name, str(e)))

if parse_errors:
    st.error("Algunos PDFs no se pudieron procesar:")
    for name, err in parse_errors:
        st.write(f"- {name}: {err}")

df_all = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

# Show parsed
st.markdown("### 2) Datos detectados (desde los PDFs)")
if df_all.empty:
    st.warning("No se detectaron filas de tablas en los PDFs. Revise que el PDF tenga texto (no imagen).")
    st.stop()

# Build region assignment table
delegaciones = sorted(df_all["Delegación"].dropna().unique().tolist())
st.markdown("### 3) Asignación de Región (manual)")
st.caption("Edite la columna Región para cada delegación. Luego genere el PDF consolidado.")

# create editable mapping
map_df = pd.DataFrame({"Delegación": delegaciones, "Región": [""] * len(delegaciones)})

# try to keep prior edits in session
if "region_map" in st.session_state:
    prev = st.session_state["region_map"]
    map_df = map_df.merge(prev, on="Delegación", how="left", suffixes=("", "_prev"))
    map_df["Región"] = map_df["Región_prev"].fillna(map_df["Región"])
    map_df = map_df[["Delegación", "Región"]]

edited = st.data_editor(
    map_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "Delegación": st.column_config.TextColumn(disabled=True),
        "Región": st.column_config.TextColumn(help="Ej: Región Huetar Norte, Región 2 Alajuela, etc.")
    }
)

st.session_state["region_map"] = edited.copy()

# Merge regions into df_all
df = df_all.merge(edited, on="Delegación", how="left")

# Validate
missing_region = df["Región"].isna().sum() + (df["Región"].astype(str).str.strip() == "").sum()
if missing_region > 0:
    st.warning("Hay delegaciones sin Región asignada. Puede generar igual, pero el informe quedará incompleto por región.")

# Metrics
st.markdown("### 4) Resumen (en pantalla)")

df_valid = df.copy()
df_valid["Región"] = df_valid["Región"].fillna("").astype(str).str.strip()

df_reg = agg_region_tipo(df_valid[df_valid["Región"] != ""]) if (df_valid["Región"] != "").any() else pd.DataFrame()
df_deleg = agg_region_delegacion_tipo(df_valid[df_valid["Región"] != ""]) if (df_valid["Región"] != "").any() else pd.DataFrame()

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Delegaciones detectadas", len(delegaciones))
with c2:
    st.metric("Regiones con datos", df_valid[df_valid["Región"] != ""]["Región"].nunique())
with c3:
    st.metric("Filas de detalle", len(df_valid))

st.dataframe(df_valid.sort_values(["Delegación", "Tipo", "Distrito"]), use_container_width=True, height=320)

if not df_reg.empty:
    st.markdown("#### Resumen regional por tipo")
    st.dataframe(df_reg.sort_values(["Región", "Tipo"]), use_container_width=True)

# Generate PDF
st.markdown("### 5) Generar PDF consolidado")

colA, colB = st.columns([1, 2])
with colA:
    generar = st.button("🧾 Generar informe PDF", type="primary")
with colB:
    st.caption("El PDF incluye: portada simple con logo + resumen regional + páginas por región con detalle por delegación y semáforo.")

if generar:
    if df_valid[df_valid["Región"] != ""].empty:
        st.error("Asigne al menos una región (columna Región) para poder consolidar.")
        st.stop()

    pdf_bytes = build_pdf_report(
        df_detalle=df_valid[df_valid["Región"] != ""].copy(),
        df_regional=df_reg.copy(),
        df_por_deleg=df_deleg.copy(),
        logo_pil=logo_img,
        titulo=titulo,
        subtitulo=subtitulo
    )

    st.success("PDF generado.")
    st.download_button(
        "⬇️ Descargar informe PDF",
        data=pdf_bytes,
        file_name="Informe_Avance_Regional_Encuestas.pdf",
        mime="application/pdf"
    )

