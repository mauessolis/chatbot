import os
import re
import uuid
import json
import unicodedata
import pandas as pd
import streamlit as st
import plotly.express as px
from datetime import datetime, timedelta
from databricks.sdk import WorkspaceClient


# ------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ------------------------------------------------------------

st.set_page_config(
    page_title="Asistente de Traspasos AFORE",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ------------------------------------------------------------
# CONSTANTES VISUALES PROFUTURO
# ------------------------------------------------------------

LOGO_PATH = "assets/profuturo_logo.png"

PROFUTURO_COLORS = {
    "blue": "#004B8D",
    "dark_blue": "#002B5C",
    "deep_blue": "#003B73",
    "light_blue": "#00A6D6",
    "gold": "#F6B221",
    "orange": "#F28C28",
    "gray": "#6B7280",
    "light_gray": "#F3F6FA",
    "white": "#FFFFFF",
    "text": "#1A1A1A"
}

PROFUTURO_COLOR_SEQUENCE = [
    PROFUTURO_COLORS["blue"],
    PROFUTURO_COLORS["gold"],
    PROFUTURO_COLORS["light_blue"],
    PROFUTURO_COLORS["orange"],
    PROFUTURO_COLORS["dark_blue"],
    PROFUTURO_COLORS["gray"]
]


# ------------------------------------------------------------
# LOGO STREAMLIT
# ------------------------------------------------------------

if os.path.exists(LOGO_PATH):
    try:
        st.logo(
            LOGO_PATH,
            size="large",
            icon_image=LOGO_PATH
        )
    except Exception:
        pass


# ------------------------------------------------------------
# FUNCIONES DE ESTILO VISUAL
# ------------------------------------------------------------

def inject_profuturo_theme():
    """
    Inyecta estilos CSS para que la app se sienta como una herramienta interna
    de Profuturo y no como un template genérico de Streamlit.
    """
    st.markdown(
        """
        <style>
        :root {
            --profuturo-blue: #004B8D;
            --profuturo-dark-blue: #002B5C;
            --profuturo-deep-blue: #003B73;
            --profuturo-gold: #F6B221;
            --profuturo-bg: #F7F9FC;
        }

        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 3rem;
            max-width: 1280px;
        }

        .profuturo-header {
            background: linear-gradient(135deg, #003B73 0%, #004B8D 62%, #006CB8 100%);
            padding: 28px 32px;
            border-radius: 22px;
            color: white;
            margin-bottom: 22px;
            box-shadow: 0 14px 34px rgba(0, 43, 92, 0.22);
            border: 1px solid rgba(255,255,255,0.14);
        }

        .profuturo-eyebrow {
            color: #F6B221;
            font-size: 0.86rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 8px;
        }

        .profuturo-title {
            font-size: 2.25rem;
            font-weight: 850;
            margin: 0;
            line-height: 1.12;
        }

        .profuturo-subtitle {
            font-size: 1.02rem;
            line-height: 1.55;
            max-width: 1020px;
            margin-top: 12px;
            color: rgba(255,255,255,0.93);
        }

        .profuturo-pill {
            display: inline-block;
            background: rgba(246, 178, 33, 0.16);
            color: #FFE09A;
            border: 1px solid rgba(246, 178, 33, 0.42);
            padding: 5px 11px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 800;
            margin-top: 14px;
            margin-right: 8px;
        }

        .profuturo-card {
            background: #FFFFFF;
            border: 1px solid rgba(0, 75, 141, 0.12);
            border-radius: 18px;
            padding: 16px 18px;
            box-shadow: 0 8px 22px rgba(0, 43, 92, 0.07);
            margin-bottom: 14px;
        }

        .suggested-question {
            background: #FFFFFF;
            border: 1px solid rgba(0, 75, 141, 0.18);
            border-radius: 14px;
            padding: 12px;
            margin-bottom: 8px;
        }

        section[data-testid="stSidebar"] {
            border-right: 1px solid rgba(246, 178, 33, 0.24);
        }

        div[data-testid="stChatMessage"] {
            border-radius: 18px;
        }

        .stButton > button {
            border-radius: 999px;
            border: 1px solid rgba(246, 178, 33, 0.65);
            background-color: rgba(246, 178, 33, 0.05);
        }

        .stButton > button:hover {
            border: 1px solid #F6B221;
            background-color: rgba(246, 178, 33, 0.14);
        }

        .stDownloadButton > button {
            border-radius: 999px;
            border: 1px solid rgba(0, 75, 141, 0.35);
        }

        div[data-testid="stExpander"] {
            border-radius: 14px;
            border: 1px solid rgba(0, 75, 141, 0.14);
        }

        div[data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid rgba(0, 75, 141, 0.14);
            border-radius: 16px;
            padding: 14px 16px;
            box-shadow: 0 8px 18px rgba(0, 43, 92, 0.06);
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def render_profuturo_header():
    """
    Renderiza el header principal de la app.
    """
    st.markdown(
        """
        <div class="profuturo-header">
            <div class="profuturo-eyebrow">Profuturo · Inteligencia de datos</div>
            <h1 class="profuturo-title">Asistente de Traspasos AFORE</h1>
            <div class="profuturo-subtitle">
                Consulta información de traspasos mediante lenguaje natural.
                El asistente interpreta preguntas de negocio, consulta Databricks Genie
                y presenta respuestas con contexto analítico, tablas y visualizaciones.
            </div>
            <span class="profuturo-pill">Genie AI</span>
            <span class="profuturo-pill">Traspasos AFORE</span>
            <span class="profuturo-pill">Análisis conversacional</span>
        </div>
        """,
        unsafe_allow_html=True
    )


# ------------------------------------------------------------
# FUNCIONES AUXILIARES
# ------------------------------------------------------------

def get_secret_or_env(name: str, default: str = "") -> str:
    """
    Busca primero en st.secrets y después en variables de entorno.
    Esto permite usar la app localmente o desplegarla después.
    """
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass

    return os.getenv(name, default)


def load_databricks_config():
    """
    Carga la configuración de Databricks desde Streamlit Secrets
    o variables de entorno.

    Secrets esperados:
    - DATABRICKS_HOST
    - DATABRICKS_TOKEN
    - GENIE_SPACE_ID
    """
    raw_config = {
        "DATABRICKS_HOST": get_secret_or_env("DATABRICKS_HOST"),
        "DATABRICKS_TOKEN": get_secret_or_env("DATABRICKS_TOKEN"),
        "GENIE_SPACE_ID": get_secret_or_env("GENIE_SPACE_ID")
    }

    missing = [
        key for key, value in raw_config.items()
        if not value
    ]

    if missing:
        st.error(
            "Faltan variables de conexión en Streamlit Secrets: "
            + ", ".join(missing)
            + ". Verifica la configuración de secrets antes de continuar."
        )
        st.stop()

    return {
        "host": raw_config["DATABRICKS_HOST"],
        "token": raw_config["DATABRICKS_TOKEN"],
        "space_id": raw_config["GENIE_SPACE_ID"]
    }


def init_session_state():
    """
    Inicializa las variables de sesión para mantener el historial del chat.
    """
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None

    if "last_raw_response" not in st.session_state:
        st.session_state.last_raw_response = None

    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None

    if "feedback" not in st.session_state:
        st.session_state.feedback = {}


def reset_chat():
    """
    Reinicia la conversación en Streamlit.
    En la siguiente pregunta se crea una nueva conversación en Genie.
    """
    st.session_state.messages = []
    st.session_state.conversation_id = None
    st.session_state.last_raw_response = None
    st.session_state.pending_prompt = None
    st.session_state.feedback = {}


def normalize_host(host: str) -> str:
    """
    Normaliza la URL del workspace.
    Ejemplo válido:
    https://adb-xxxxxxxx.azuredatabricks.net
    """
    host = host.strip().rstrip("/")

    if host and not host.startswith("http"):
        host = f"https://{host}"

    return host


def get_attr(obj, *names, default=None):
    """
    Lee atributos de objetos del SDK o llaves de diccionarios.
    Ayuda a soportar pequeñas variaciones en la respuesta.
    """
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]

        if hasattr(obj, name):
            return getattr(obj, name)

    return default


def to_dict(obj):
    """
    Convierte respuestas del SDK a diccionario cuando sea posible.
    Útil para debug y para extraer resultados tabulares.
    """
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]

    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")

    if hasattr(obj, "as_dict"):
        try:
            return obj.as_dict()
        except Exception:
            pass

    if hasattr(obj, "__dict__"):
        return {
            k: to_dict(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }

    return str(obj)


def build_genie_prompt(user_prompt: str, deep_thinking: bool) -> str:
    """
    Construye el prompt que se enviará a Genie.

    Respuesta rápida:
        Envía la pregunta casi tal cual.

    Deep thinking:
        No activa Agent Mode real, pero guía a Genie para responder apoyándose
        en instrucciones, SQL Expressions y SQL Queries validadas del Space.
    """
    if not deep_thinking:
        return user_prompt

    return f"""
Actúa como un analista experto en datos de traspasos AFORE usando el contexto, instrucciones, SQL Expressions y SQL Queries validadas que ya existen dentro de este Genie Space.

Antes de responder, revisa si la pregunta del usuario se parece a alguno de los ejemplos SQL validados o patrones de respuesta definidos en las instrucciones del Space. Si existe una coincidencia o una pregunta similar, usa ese ejemplo como referencia principal para construir la consulta y la respuesta.

Prioriza la lógica ya documentada en el Genie Space sobre inferencias generales. En especial:
1. Usa las definiciones de negocio, métricas y filtros ya configurados en las instrucciones y SQL Expressions.
2. Apóyate en los SQL Queries validados como guía para resolver preguntas similares.
3. Respeta la diferencia entre traspasos recibidos/IN y traspasos cedidos/OUT.
4. Si el usuario pregunta por una AFORE sin especificar origen o destino, interpreta por defecto que se refiere a traspasos recibidos, salvo que use términos como cedidos, salientes, perdidos, OUT, origen o desde.
5. Si la pregunta involucra Profuturo, identifica claramente si debe tratarse como AFORE destino, AFORE origen o entidad de comparación.
6. Para análisis mensuales, ordena los resultados cronológicamente.
7. Para comparativos, usa periodos equivalentes y explica claramente contra qué se está comparando.
8. Si la pregunta puede responderse con una tabla, devuelve resultados estructurados con columnas claras y nombres descriptivos.
9. Si hay ambigüedad, responde indicando el supuesto utilizado en lugar de inventar una interpretación.
10. Evita inventar datos o definiciones fuera del contexto configurado en el Space.

Entrega la respuesta en formato ejecutivo:
- Primero da el resultado directo.
- Después incluye una breve interpretación.
- Si aplica, menciona cualquier supuesto usado.
- Si aplica, devuelve una tabla que facilite la visualización en Streamlit.

Pregunta del usuario:
{user_prompt}
""".strip()


def extract_text_from_genie_response(response) -> str:
    """
    Extrae el texto principal que devuelve Genie desde attachments.
    """
    attachments = get_attr(response, "attachments", default=[]) or []
    text_parts = []

    for attachment in attachments:
        text_obj = get_attr(attachment, "text")

        if text_obj:
            content = get_attr(text_obj, "content")

            if isinstance(text_obj, str):
                content = text_obj

            if content:
                text_parts.append(str(content))

    if text_parts:
        return "\n\n".join(text_parts)

    return "Genie procesó la solicitud, pero no devolvió una respuesta textual clara."


def extract_sql_from_genie_response(response) -> list[str]:
    """
    Extrae SQL generado cuando Genie devuelve attachments de tipo query.
    """
    attachments = get_attr(response, "attachments", default=[]) or []
    sql_queries = []

    for attachment in attachments:
        query_obj = get_attr(attachment, "query")

        if query_obj:
            sql_text = (
                get_attr(query_obj, "query")
                or get_attr(query_obj, "sql")
                or get_attr(query_obj, "statement")
            )

            if sql_text:
                sql_queries.append(str(sql_text))

    return sql_queries


def get_query_attachment_ids(response) -> list[str]:
    """
    Obtiene los attachment_id asociados a consultas SQL.
    Estos sirven para recuperar resultados tabulares.
    """
    attachments = get_attr(response, "attachments", default=[]) or []
    attachment_ids = []

    for attachment in attachments:
        query_obj = get_attr(attachment, "query")

        if query_obj:
            attachment_id = (
                get_attr(attachment, "attachment_id")
                or get_attr(attachment, "id")
            )

            if attachment_id:
                attachment_ids.append(str(attachment_id))

    return attachment_ids


def find_first_key(obj, target_key):
    """
    Busca recursivamente la primera aparición de una llave dentro de un dict/list.
    """
    if isinstance(obj, dict):
        if target_key in obj:
            return obj[target_key]

        for value in obj.values():
            result = find_first_key(value, target_key)
            if result is not None:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = find_first_key(item, target_key)
            if result is not None:
                return result

    return None


def extract_dataframe_from_query_result(query_result):
    """
    Intenta convertir la respuesta tabular de Genie en un DataFrame.
    La estructura exacta puede variar, por eso se parsea de forma flexible.
    """
    raw = to_dict(query_result)

    if not raw:
        return None

    data_array = find_first_key(raw, "data_array")
    columns_raw = find_first_key(raw, "columns")

    if not data_array:
        return None

    if isinstance(data_array, list) and len(data_array) > 0:
        if all(isinstance(row, dict) for row in data_array):
            return pd.DataFrame(data_array)

        column_names = []

        if isinstance(columns_raw, list):
            for idx, col in enumerate(columns_raw):
                if isinstance(col, dict):
                    column_names.append(
                        col.get("name")
                        or col.get("display_name")
                        or col.get("column_name")
                        or f"col_{idx + 1}"
                    )
                else:
                    column_names.append(str(col))

        if not column_names and isinstance(data_array[0], list):
            column_names = [f"col_{i + 1}" for i in range(len(data_array[0]))]

        try:
            return pd.DataFrame(data_array, columns=column_names)
        except Exception:
            return pd.DataFrame(data_array)

    return None


# ------------------------------------------------------------
# VISUALIZACIONES AUTOMÁTICAS
# ------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Normaliza texto para detectar columnas aunque tengan acentos,
    mayúsculas o variaciones de nombre.
    """
    text = str(text).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text


def prettify_label(label: str) -> str:
    """
    Convierte nombres técnicos de columnas en etiquetas más legibles.
    """
    label = str(label).replace("_", " ").strip()
    label = re.sub(r"\s+", " ", label)
    return label.title()


def format_number(value) -> str:
    """
    Da formato ejecutivo a valores numéricos.
    """
    try:
        value = float(value)

        if abs(value) >= 1_000_000:
            return f"{value:,.0f}"

        if abs(value) >= 1_000:
            return f"{value:,.0f}"

        if value.is_integer():
            return f"{value:,.0f}"

        return f"{value:,.2f}"

    except Exception:
        return str(value)


def prepare_dataframe_for_charts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepara el DataFrame para graficar:
    - limpia nombres de columnas;
    - convierte columnas numéricas aunque vengan como texto/string/decimal;
    - convierte fechas ISO;
    - crea una columna temporal si detecta año + mes.
    """
    df_chart = df.copy()
    df_chart.columns = [str(col).strip() for col in df_chart.columns]

    # Intentar convertir columnas tipo fecha.
    for col in df_chart.columns:
        norm = normalize_text(col)

        if any(keyword in norm for keyword in ["fecha", "periodo", "mes", "date"]):
            parsed_dates = pd.to_datetime(
                df_chart[col],
                errors="coerce",
                utc=True
            )

            if len(df_chart) > 0 and parsed_dates.notna().mean() >= 0.50:
                df_chart[col] = parsed_dates.dt.tz_convert(None)

    # Intentar convertir cualquier columna no-fecha a numérica.
    for col in df_chart.columns:
        if pd.api.types.is_datetime64_any_dtype(df_chart[col]):
            continue

        if pd.api.types.is_numeric_dtype(df_chart[col]):
            continue

        raw = df_chart[col].astype(str).str.strip()

        cleaned = (
            raw
            .str.replace("$", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(" ", "", regex=False)
        )

        numeric = pd.to_numeric(cleaned, errors="coerce")

        if len(df_chart) > 0 and numeric.notna().mean() >= 0.70:
            df_chart[col] = numeric

    # Detectar columnas de año y mes para crear periodo graficable.
    normalized_cols = {
        col: normalize_text(col)
        for col in df_chart.columns
    }

    year_candidates = [
        col for col, norm in normalized_cols.items()
        if norm in ["anio", "ano", "year"]
    ]

    month_candidates = [
        col for col, norm in normalized_cols.items()
        if norm in ["mes", "month"]
    ]

    month_map = {
        "enero": 1, "ene": 1, "january": 1, "jan": 1,
        "febrero": 2, "feb": 2, "february": 2,
        "marzo": 3, "mar": 3, "march": 3,
        "abril": 4, "abr": 4, "april": 4, "apr": 4,
        "mayo": 5, "may": 5,
        "junio": 6, "jun": 6, "june": 6,
        "julio": 7, "jul": 7, "july": 7,
        "agosto": 8, "ago": 8, "august": 8, "aug": 8,
        "septiembre": 9, "sep": 9, "september": 9, "sept": 9,
        "octubre": 10, "oct": 10, "october": 10,
        "noviembre": 11, "nov": 11, "november": 11,
        "diciembre": 12, "dic": 12, "december": 12, "dec": 12
    }

    if year_candidates and month_candidates:
        year_col = year_candidates[0]
        month_col = month_candidates[0]

        years = pd.to_numeric(df_chart[year_col], errors="coerce")

        if pd.api.types.is_numeric_dtype(df_chart[month_col]):
            months = pd.to_numeric(df_chart[month_col], errors="coerce")
        else:
            months = (
                df_chart[month_col]
                .astype(str)
                .map(lambda x: month_map.get(normalize_text(x), None))
            )

        period = pd.to_datetime(
            {
                "year": years,
                "month": months,
                "day": 1
            },
            errors="coerce"
        )

        if len(df_chart) > 0 and period.notna().mean() >= 0.50:
            df_chart["_periodo_grafico"] = period

    return df_chart


def get_numeric_columns(df: pd.DataFrame) -> list[str]:
    """
    Devuelve columnas numéricas útiles para graficar.
    """
    return [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
        and not str(col).startswith("_")
    ]


def get_categorical_columns(df: pd.DataFrame) -> list[str]:
    """
    Devuelve columnas categóricas útiles para graficar.
    """
    categorical_cols = []

    for col in df.columns:
        if str(col).startswith("_"):
            continue

        if pd.api.types.is_numeric_dtype(df[col]):
            continue

        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue

        unique_count = df[col].nunique(dropna=True)

        if unique_count >= 1:
            categorical_cols.append(col)

    return categorical_cols


def find_time_column(df: pd.DataFrame):
    """
    Detecta una columna temporal para gráficas de tendencia.
    """
    if "_periodo_grafico" in df.columns:
        return "_periodo_grafico"

    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col

    for col in df.columns:
        norm = normalize_text(col)

        if any(keyword in norm for keyword in ["fecha", "periodo", "date", "mes"]):
            parsed = pd.to_datetime(df[col], errors="coerce", utc=True)

            if len(df) > 0 and parsed.notna().mean() >= 0.50:
                df[col] = parsed.dt.tz_convert(None)
                return col

    for col in df.columns:
        norm = normalize_text(col)

        if norm in ["anio", "ano", "year"] and pd.api.types.is_numeric_dtype(df[col]):
            return col

        if norm in ["mes", "month"] and pd.api.types.is_numeric_dtype(df[col]):
            return col

    return None


def choose_measure_column(numeric_cols: list[str]) -> str | None:
    """
    Elige la métrica principal a graficar.
    Prioriza columnas que suenan a total, conteo o traspasos.
    """
    if not numeric_cols:
        return None

    priority_keywords = [
        "traspaso",
        "total",
        "conteo",
        "cantidad",
        "count",
        "registros",
        "cuentas",
        "volumen",
        "participacion",
        "porcentaje",
        "share",
        "monto",
        "saldo",
        "promedio",
        "variacion"
    ]

    for keyword in priority_keywords:
        for col in numeric_cols:
            if keyword in normalize_text(col):
                return col

    return numeric_cols[0]


def choose_category_column(
    df: pd.DataFrame,
    categorical_cols: list[str],
    max_categories: int = 30
) -> str | None:
    """
    Elige la mejor columna categórica para barras/rankings.
    """
    if not categorical_cols:
        return None

    priority_keywords = [
        "afore_destino",
        "destino",
        "afore_origen",
        "origen",
        "afore",
        "administradora",
        "instituto",
        "grupo",
        "categoria",
        "segmento",
        "zona",
        "canal"
    ]

    valid_cols = [
        col for col in categorical_cols
        if df[col].nunique(dropna=True) <= max_categories
    ]

    if not valid_cols:
        return None

    for keyword in priority_keywords:
        for col in valid_cols:
            if keyword in normalize_text(col):
                return col

    return valid_cols[0]


def sort_for_chart(df: pd.DataFrame, x_col: str) -> pd.DataFrame:
    """
    Ordena el DataFrame de forma útil para graficar.
    """
    df_sorted = df.copy()

    if x_col == "_periodo_grafico":
        return df_sorted.sort_values(x_col)

    if pd.api.types.is_datetime64_any_dtype(df_sorted[x_col]):
        return df_sorted.sort_values(x_col)

    if pd.api.types.is_numeric_dtype(df_sorted[x_col]):
        return df_sorted.sort_values(x_col)

    month_order = {
        "enero": 1, "ene": 1,
        "febrero": 2, "feb": 2,
        "marzo": 3, "mar": 3,
        "abril": 4, "abr": 4,
        "mayo": 5,
        "junio": 6, "jun": 6,
        "julio": 7, "jul": 7,
        "agosto": 8, "ago": 8,
        "septiembre": 9, "sep": 9,
        "octubre": 10, "oct": 10,
        "noviembre": 11, "nov": 11,
        "diciembre": 12, "dic": 12
    }

    if normalize_text(x_col) in ["mes", "month"]:
        df_sorted["_orden_mes"] = (
            df_sorted[x_col]
            .astype(str)
            .map(lambda x: month_order.get(normalize_text(x), None))
        )

        if df_sorted["_orden_mes"].notna().any():
            return df_sorted.sort_values("_orden_mes").drop(columns=["_orden_mes"])

    return df_sorted


def style_plotly_figure(fig, title: str):
    """
    Aplica estilo visual Profuturo a las gráficas.
    """
    fig.update_layout(
        title={
            "text": title,
            "x": 0.02,
            "xanchor": "left",
            "font": {
                "size": 20,
                "color": PROFUTURO_COLORS["dark_blue"]
            }
        },
        template="plotly_white",
        height=460,
        margin=dict(l=20, r=20, t=70, b=40),
        font=dict(
            size=13,
            color=PROFUTURO_COLORS["text"]
        ),
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        hovermode="x unified"
    )

    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor="rgba(0, 75, 141, 0.10)",
        zeroline=False,
        title_font=dict(color=PROFUTURO_COLORS["dark_blue"]),
        tickfont=dict(color="#374151")
    )

    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor="rgba(0, 75, 141, 0.10)",
        zeroline=False,
        title_font=dict(color=PROFUTURO_COLORS["dark_blue"]),
        tickfont=dict(color="#374151")
    )

    return fig


def render_kpi_cards(df: pd.DataFrame, numeric_cols: list[str]):
    """
    Muestra KPIs cuando la respuesta trae un solo registro.
    """
    display_cols = numeric_cols[:4]

    if not display_cols:
        return

    st.subheader("Resumen visual")

    cols = st.columns(len(display_cols))

    for idx, metric_col in enumerate(display_cols):
        value = df.iloc[0][metric_col]

        with cols[idx]:
            st.metric(
                label=prettify_label(metric_col),
                value=format_number(value)
            )


def render_heatmap_if_possible(df: pd.DataFrame, value_col: str, chart_key: str):
    """
    Genera heatmap cuando detecta un cruce tipo origen/destino.
    """
    cols = df.columns.tolist()

    origin_col = None
    destination_col = None

    for col in cols:
        norm = normalize_text(col)

        if "origen" in norm and origin_col is None:
            origin_col = col

        if "destino" in norm and destination_col is None:
            destination_col = col

    if not origin_col or not destination_col:
        return False

    if df[origin_col].nunique(dropna=True) > 30:
        return False

    if df[destination_col].nunique(dropna=True) > 30:
        return False

    pivot = df.pivot_table(
        index=origin_col,
        columns=destination_col,
        values=value_col,
        aggfunc="sum",
        fill_value=0
    )

    if pivot.empty:
        return False

    fig = px.imshow(
        pivot,
        text_auto=True,
        aspect="auto",
        color_continuous_scale=[
            "#F7F9FC",
            PROFUTURO_COLORS["light_blue"],
            PROFUTURO_COLORS["blue"],
            PROFUTURO_COLORS["dark_blue"]
        ],
        labels=dict(
            x=prettify_label(destination_col),
            y=prettify_label(origin_col),
            color=prettify_label(value_col)
        )
    )

    fig = style_plotly_figure(
        fig,
        title=f"Cruce de {prettify_label(origin_col)} vs {prettify_label(destination_col)}"
    )

    st.subheader("Visualización")
    st.plotly_chart(fig, use_container_width=True, key=f"heatmap_{chart_key}")

    return True


def render_smart_visualization(df: pd.DataFrame, chart_key: str):
    """
    Genera una visualización automática según la estructura de la tabla.

    Tipos soportados:
    - KPIs
    - línea temporal
    - barras/ranking
    - dona
    - heatmap origen/destino
    - scatter
    - histograma
    """
    if df is None or df.empty:
        return

    df_chart = prepare_dataframe_for_charts(df)

    numeric_cols = get_numeric_columns(df_chart)
    categorical_cols = get_categorical_columns(df_chart)
    time_col = find_time_column(df_chart)
    value_col = choose_measure_column(numeric_cols)

    if not numeric_cols or value_col is None:
        st.info("La respuesta no contiene columnas numéricas suficientes para generar una gráfica.")
        return

    if len(df_chart) == 1:
        render_kpi_cards(df_chart, numeric_cols)
        return

    if render_heatmap_if_possible(df_chart, value_col, chart_key):
        return

    # Tendencia temporal.
    if time_col:
        color_col = None

        for col in categorical_cols:
            unique_count = df_chart[col].nunique(dropna=True)

            if 1 < unique_count <= 12:
                if any(
                    keyword in normalize_text(col)
                    for keyword in ["afore", "origen", "destino", "grupo", "categoria", "instituto"]
                ):
                    color_col = col
                    break

        plot_df = sort_for_chart(df_chart, time_col)

        labels = {
            time_col: "Periodo" if time_col == "_periodo_grafico" else prettify_label(time_col),
            value_col: prettify_label(value_col)
        }

        if color_col:
            labels[color_col] = prettify_label(color_col)

        fig = px.line(
            plot_df,
            x=time_col,
            y=value_col,
            color=color_col,
            markers=True,
            labels=labels,
            color_discrete_sequence=PROFUTURO_COLOR_SEQUENCE
        )

        fig.update_traces(line=dict(width=3), marker=dict(size=8))

        fig = style_plotly_figure(
            fig,
            title=f"Tendencia de {prettify_label(value_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True, key=f"line_{chart_key}")
        return

    # Dona para participaciones o porcentajes.
    category_col = choose_category_column(df_chart, categorical_cols)

    if category_col:
        category_count = df_chart[category_col].nunique(dropna=True)
        value_norm = normalize_text(value_col)

        if category_count <= 8 and any(
            keyword in value_norm
            for keyword in ["participacion", "porcentaje", "share", "pct"]
        ):
            fig = px.pie(
                df_chart,
                names=category_col,
                values=value_col,
                hole=0.45,
                labels={
                    category_col: prettify_label(category_col),
                    value_col: prettify_label(value_col)
                },
                color_discrete_sequence=PROFUTURO_COLOR_SEQUENCE
            )

            fig = style_plotly_figure(
                fig,
                title=f"Distribución de {prettify_label(value_col)}"
            )

            fig.update_traces(
                textposition="inside",
                textinfo="percent+label"
            )

            st.subheader("Visualización")
            st.plotly_chart(fig, use_container_width=True, key=f"pie_{chart_key}")
            return

        # Barras horizontales para rankings o categorías.
        bar_df = (
            df_chart[[category_col, value_col]]
            .dropna()
            .groupby(category_col, as_index=False)[value_col]
            .sum()
            .sort_values(value_col, ascending=False)
            .head(20)
        )

        bar_df = bar_df.sort_values(value_col, ascending=True)

        fig = px.bar(
            bar_df,
            x=value_col,
            y=category_col,
            orientation="h",
            text=value_col,
            labels={
                category_col: prettify_label(category_col),
                value_col: prettify_label(value_col)
            },
            color_discrete_sequence=[PROFUTURO_COLORS["blue"]]
        )

        fig.update_traces(
            texttemplate="%{text:,.0f}",
            textposition="outside",
            cliponaxis=False,
            marker_line_width=0
        )

        fig = style_plotly_figure(
            fig,
            title=f"Ranking por {prettify_label(value_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True, key=f"bar_{chart_key}")
        return

    # Scatter cuando hay dos métricas numéricas.
    if len(numeric_cols) >= 2:
        x_col = numeric_cols[0]
        y_col = numeric_cols[1]

        fig = px.scatter(
            df_chart,
            x=x_col,
            y=y_col,
            size=value_col if value_col not in [x_col, y_col] else None,
            labels={
                x_col: prettify_label(x_col),
                y_col: prettify_label(y_col)
            },
            color_discrete_sequence=PROFUTURO_COLOR_SEQUENCE
        )

        fig = style_plotly_figure(
            fig,
            title=f"Relación entre {prettify_label(x_col)} y {prettify_label(y_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True, key=f"scatter_{chart_key}")
        return

    # Fallback: histograma.
    fig = px.histogram(
        df_chart,
        x=value_col,
        labels={
            value_col: prettify_label(value_col)
        },
        color_discrete_sequence=[PROFUTURO_COLORS["blue"]]
    )

    fig = style_plotly_figure(
        fig,
        title=f"Distribución de {prettify_label(value_col)}"
    )

    st.subheader("Visualización")
    st.plotly_chart(fig, use_container_width=True, key=f"hist_{chart_key}")


# ------------------------------------------------------------
# CONEXIÓN CON GENIE
# ------------------------------------------------------------

def ask_genie(
    host: str,
    token: str,
    space_id: str,
    prompt: str,
    show_sql: bool = False
):
    """
    Envía una pregunta a Genie.

    Si no existe conversation_id en sesión:
        crea una conversación nueva.

    Si ya existe conversation_id:
        manda la pregunta como seguimiento, conservando contexto.
    """
    host = normalize_host(host)

    client = WorkspaceClient(
        host=host,
        token=token
    )

    timeout = timedelta(minutes=10)

    if st.session_state.conversation_id is None:
        response = client.genie.start_conversation_and_wait(
            space_id=space_id,
            content=prompt,
            timeout=timeout
        )
        st.session_state.conversation_id = get_attr(response, "conversation_id")

    else:
        response = client.genie.create_message_and_wait(
            space_id=space_id,
            conversation_id=st.session_state.conversation_id,
            content=prompt,
            timeout=timeout
        )

    st.session_state.last_raw_response = to_dict(response)

    response_text = extract_text_from_genie_response(response)
    sql_queries = extract_sql_from_genie_response(response)

    message_id = get_attr(response, "id") or get_attr(response, "message_id")
    attachment_ids = get_query_attachment_ids(response)

    dataframes = []

    if message_id and attachment_ids:
        for attachment_id in attachment_ids:
            try:
                query_result = client.genie.get_message_attachment_query_result(
                    space_id=space_id,
                    conversation_id=st.session_state.conversation_id,
                    message_id=message_id,
                    attachment_id=attachment_id
                )

                df = extract_dataframe_from_query_result(query_result)

                if df is not None and not df.empty:
                    dataframes.append(df)

            except Exception as e:
                if show_sql:
                    st.warning(
                        f"No se pudo recuperar el resultado tabular para attachment_id={attachment_id}: {e}"
                    )

    return {
        "text": response_text,
        "sql": sql_queries,
        "dataframes": dataframes,
        "raw": st.session_state.last_raw_response
    }


# ------------------------------------------------------------
# RENDER DE RESPUESTAS COMPLETAS
# ------------------------------------------------------------

def render_feedback_controls(message_id: str):
    """
    Renderiza controles de feedback simple por respuesta.
    El feedback se conserva en session_state durante la sesión.
    """
    current_feedback = st.session_state.feedback.get(message_id)

    c1, c2, c3 = st.columns([1, 1, 6])

    with c1:
        if st.button("👍", key=f"thumbs_up_{message_id}", help="Marcar respuesta como correcta"):
            st.session_state.feedback[message_id] = "correcta"
            st.rerun()

    with c2:
        if st.button("👎", key=f"thumbs_down_{message_id}", help="Marcar respuesta como incorrecta"):
            st.session_state.feedback[message_id] = "incorrecta"
            st.rerun()

    with c3:
        if current_feedback:
            st.caption(f"Feedback registrado: **{current_feedback}**")


def render_assistant_artifacts(
    message: dict,
    message_index: int,
    show_charts: bool,
    show_sql: bool,
    show_debug: bool
):
    """
    Renderiza todos los elementos asociados a una respuesta del asistente:
    gráficas, tablas, CSV, SQL y respuesta cruda.
    Esto permite que las respuestas anteriores no pierdan sus visualizaciones.
    """
    message_id = message.get("id", f"msg_{message_index}")

    dataframes = message.get("dataframes", []) or []

    if dataframes:
        for df_idx, df in enumerate(dataframes, start=1):
            chart_key = f"{message_id}_{df_idx}"

            if show_charts:
                render_smart_visualization(
                    df,
                    chart_key=chart_key
                )

            with st.expander(f"Ver tabla de resultados {df_idx}", expanded=False):
                st.caption(f"{len(df):,} filas · {len(df.columns):,} columnas")
                st.dataframe(df, use_container_width=True)

                csv = df.to_csv(index=False).encode("utf-8")

                st.download_button(
                    label=f"Descargar resultado {df_idx} en CSV",
                    data=csv,
                    file_name=f"resultado_genie_{message_id}_{df_idx}.csv",
                    mime="text/csv",
                    key=f"download_{message_id}_{df_idx}"
                )

    if show_sql and message.get("sql"):
        with st.expander("SQL generado por Genie"):
            for sql_idx, sql in enumerate(message["sql"], start=1):
                st.code(sql, language="sql")

    if show_debug and message.get("raw"):
        with st.expander("Respuesta cruda de Genie"):
            st.json(message["raw"])

    if message.get("role") == "assistant" and message_id != "welcome":
        render_feedback_controls(message_id)


def build_conversation_export() -> str:
    """
    Construye una exportación de la conversación en formato Markdown.
    Incluye preguntas, respuestas, SQL y resumen de tablas.
    """
    lines = []
    lines.append("# Conversación - Asistente de Traspasos AFORE")
    lines.append("")
    lines.append(f"Fecha de exportación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for idx, message in enumerate(st.session_state.messages, start=1):
        role = message.get("role", "unknown")
        content = message.get("content", "")

        if role == "user":
            lines.append(f"## Pregunta {idx}")
            lines.append(content)
            lines.append("")

        elif role == "assistant":
            lines.append(f"## Respuesta {idx}")
            lines.append(content)
            lines.append("")

            dataframes = message.get("dataframes", []) or []
            if dataframes:
                lines.append("### Tablas devueltas")
                for df_idx, df in enumerate(dataframes, start=1):
                    lines.append(f"- Tabla {df_idx}: {len(df):,} filas · {len(df.columns):,} columnas")
                lines.append("")

            sql_queries = message.get("sql", []) or []
            if sql_queries:
                lines.append("### SQL generado")
                for sql_idx, sql in enumerate(sql_queries, start=1):
                    lines.append(f"```sql\n{sql}\n```")
                lines.append("")

            feedback_value = st.session_state.feedback.get(message.get("id"))
            if feedback_value:
                lines.append(f"Feedback: {feedback_value}")
                lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------
# INTERFAZ
# ------------------------------------------------------------

inject_profuturo_theme()
init_session_state()
databricks_config = load_databricks_config()

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=132)

    st.markdown("### Centro de control")
    st.caption("Configura cómo quieres consultar y visualizar la información.")

    deep_thinking = st.toggle(
        "Deep thinking",
        value=True,
        help=(
            "No activa el Agent Mode real de Genie, pero guía a Genie para apoyarse "
            "en instrucciones, SQL Expressions y SQL Queries validadas."
        )
    )

    if deep_thinking:
        st.caption("Modo actual: análisis más detallado y contextual.")
    else:
        st.caption("Modo actual: respuesta rápida y directa.")

    show_charts = st.toggle(
        "Mostrar visualizaciones automáticas",
        value=True,
        help=(
            "Genera gráficos automáticamente cuando Genie devuelve resultados tabulares. "
            "La app elige el tipo de visualización según las columnas de la respuesta."
        )
    )

    st.divider()

    show_sql = st.toggle(
        "Mostrar SQL generado",
        value=False,
        help="Útil para validación técnica contra Power BI o consultas manuales."
    )

    show_debug = st.toggle(
        "Mostrar respuesta cruda",
        value=False,
        help="Solo para pruebas técnicas."
    )

    st.divider()

    st.markdown("### Preguntas sugeridas")

    suggested_questions = [
        "¿Cuántos traspasos recibió Profuturo en 2025 por mes?",
        "¿Qué AFORE recibió más traspasos durante 2025?",
        "¿Cuáles fueron las principales AFORE origen hacia Profuturo en 2025?",
        "¿Cuál fue la participación de Profuturo en los traspasos recibidos de 2025?",
        "Compara los traspasos de Profuturo entre 2024 y 2025."
    ]

    for i, question in enumerate(suggested_questions, start=1):
        if st.button(question, key=f"suggested_question_{i}"):
            st.session_state.pending_prompt = question
            st.rerun()

    st.divider()

    export_text = build_conversation_export()
    st.download_button(
        label="Descargar conversación",
        data=export_text.encode("utf-8"),
        file_name="conversacion_traspasos_afore.md",
        mime="text/markdown",
        use_container_width=True
    )

    if st.button("Nueva conversación", use_container_width=True):
        reset_chat()
        st.rerun()

    st.caption(
        "La conexión con Databricks Genie está configurada por detrás mediante secrets."
    )


render_profuturo_header()


# ------------------------------------------------------------
# MENSAJE INICIAL
# ------------------------------------------------------------

if len(st.session_state.messages) == 0:
    st.session_state.messages.append({
        "id": "welcome",
        "role": "assistant",
        "content": (
            "Hola. Soy el asistente conversacional de análisis de **traspasos AFORE** de Profuturo.\n\n"
            "Puedo ayudarte a consultar información sobre traspasos recibidos, traspasos cedidos, "
            "participación, comparativos mensuales, comportamiento por AFORE, origen/destino y "
            "tendencias del mercado.\n\n"
            "Cuando la respuesta incluya datos estructurados, generaré tablas y visualizaciones "
            "automáticas para facilitar el análisis."
        ),
        "dataframes": [],
        "sql": [],
        "raw": {},
        "created_at": datetime.now().isoformat()
    })


# ------------------------------------------------------------
# HISTORIAL DE CHAT
# ------------------------------------------------------------

for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message["role"] == "assistant":
            render_assistant_artifacts(
                message=message,
                message_index=idx,
                show_charts=show_charts,
                show_sql=show_sql,
                show_debug=show_debug
            )


# ------------------------------------------------------------
# INPUT DEL USUARIO
# ------------------------------------------------------------

typed_prompt = st.chat_input("Pregunta algo sobre traspasos...")

prompt = typed_prompt

if st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt:
    user_message = {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": prompt,
        "created_at": datetime.now().isoformat()
    }

    st.session_state.messages.append(user_message)

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            genie_prompt = build_genie_prompt(
                user_prompt=prompt,
                deep_thinking=deep_thinking
            )

            spinner_text = (
                "Consultando Genie con análisis profundo..."
                if deep_thinking
                else "Consultando Genie..."
            )

            with st.spinner(spinner_text):
                result = ask_genie(
                    host=databricks_config["host"],
                    token=databricks_config["token"],
                    space_id=databricks_config["space_id"],
                    prompt=genie_prompt,
                    show_sql=show_sql
                )

            assistant_text = result["text"]

            assistant_message = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": assistant_text,
                "dataframes": result.get("dataframes", []),
                "sql": result.get("sql", []),
                "raw": result.get("raw", {}),
                "deep_thinking": deep_thinking,
                "created_at": datetime.now().isoformat()
            }

            st.markdown(assistant_text)

            render_assistant_artifacts(
                message=assistant_message,
                message_index=len(st.session_state.messages),
                show_charts=show_charts,
                show_sql=show_sql,
                show_debug=show_debug
            )

            st.session_state.messages.append(assistant_message)

        except Exception as e:
            error_text = str(e)

            if "PENDING_WAREHOUSE" in error_text:
                error_message = (
                    "La pregunta sí llegó a Genie, pero el SQL Warehouse no quedó listo a tiempo. "
                    "Revisa que el warehouse asignado al Genie Space esté encendido y disponible.\n\n"
                    f"Detalle técnico: `{e}`"
                )
            elif "PERMISSION" in error_text.upper() or "FORBIDDEN" in error_text.upper():
                error_message = (
                    "No pude completar la consulta porque parece haber un problema de permisos. "
                    "Revisa el acceso al Genie Space, SQL Warehouse o tablas utilizadas.\n\n"
                    f"Detalle técnico: `{e}`"
                )
            elif "TIMEOUT" in error_text.upper() or "TIMED OUT" in error_text.upper():
                error_message = (
                    "La consulta tardó más de lo esperado. Intenta reformular la pregunta o validar "
                    "que el SQL Warehouse esté disponible.\n\n"
                    f"Detalle técnico: `{e}`"
                )
            else:
                error_message = (
                    "No pude completar la consulta con Genie. "
                    "Revisa permisos del Genie Space, acceso al SQL Warehouse o disponibilidad de Databricks.\n\n"
                    f"Detalle técnico: `{e}`"
                )

            st.error(error_message)

            assistant_error_message = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": error_message,
                "dataframes": [],
                "sql": [],
                "raw": {},
                "deep_thinking": deep_thinking,
                "created_at": datetime.now().isoformat()
            }

            st.session_state.messages.append(assistant_error_message)
