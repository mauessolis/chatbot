import os
import re
import unicodedata
import pandas as pd
import streamlit as st
import plotly.express as px
from datetime import timedelta
from databricks.sdk import WorkspaceClient


# ------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ------------------------------------------------------------

st.set_page_config(
    page_title="Asistente de Traspasos AFORE",
    page_icon="💬",
    layout="wide"
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


def reset_chat():
    """
    Reinicia la conversación en Streamlit.
    En la siguiente pregunta se crea una nueva conversación en Genie.
    """
    st.session_state.messages = []
    st.session_state.conversation_id = None
    st.session_state.last_raw_response = None


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
        No activa Agent Mode real, pero guía a Genie para responder con
        más estructura, validación y razonamiento analítico.
    """
    if not deep_thinking:
        return user_prompt

    return f"""
Actúa como un analista experto en datos de traspasos AFORE.

Tu objetivo es responder la pregunta del usuario con el mayor rigor posible usando la información disponible en el Genie Space.

Antes de responder:
1. Interpreta cuidadosamente la intención de la pregunta.
2. Usa la definición de negocio correcta: un traspaso implica una cuenta que ya tenía una AFORE previa y posteriormente se movió a otra AFORE.
3. Identifica si la pregunta requiere análisis por año, mes, AFORE origen, AFORE destino, ranking, comparación o tendencia.
4. Si el usuario menciona 2025, prioriza ese año porque es el periodo más confiable para validación.
5. Si el usuario menciona 2026, considera que puede haber diferencias por actualización o corte de tablas y adviértelo si aplica.
6. Si generas resultados mensuales, ordénalos cronológicamente.
7. Si la respuesta puede incluir una tabla, procura estructurarla claramente.
8. Entrega una respuesta ejecutiva: primero el resultado directo, después una breve interpretación.
9. Si hay posibles limitaciones de datos, cortes distintos o ambigüedad en la pregunta, menciónalo con claridad.
10. Evita inventar datos. Si la información no está disponible, indícalo.
11. Siempre que sea posible, devuelve los resultados en una estructura tabular clara con columnas bien nombradas para que puedan visualizarse en una gráfica.
12. Usa nombres de columnas descriptivos, por ejemplo: anio, mes, afore_origen, afore_destino, total_traspasos, participacion, variacion.

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
    - intenta convertir columnas numéricas que vienen como texto;
    - crea una columna temporal si detecta año + mes.
    """
    df_chart = df.copy()
    df_chart.columns = [str(col).strip() for col in df_chart.columns]

    for col in df_chart.columns:
        if df_chart[col].dtype == "object":
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
        norm = normalize_text(col)

        if any(keyword in norm for keyword in ["fecha", "periodo", "date"]):
            parsed = pd.to_datetime(df[col], errors="coerce", dayfirst=True)

            if len(df) > 0 and parsed.notna().mean() >= 0.50:
                df[col] = parsed
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
        "grupo",
        "categoria",
        "segmento"
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
    Aplica estilo visual consistente a las gráficas.
    """
    fig.update_layout(
        title={
            "text": title,
            "x": 0.02,
            "xanchor": "left"
        },
        template="plotly_white",
        height=460,
        margin=dict(l=20, r=20, t=70, b=40),
        font=dict(size=13),
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
        gridcolor="rgba(0,0,0,0.08)"
    )

    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor="rgba(0,0,0,0.08)"
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


def render_heatmap_if_possible(df: pd.DataFrame, value_col: str):
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
    st.plotly_chart(fig, use_container_width=True)

    return True


def render_smart_visualization(df: pd.DataFrame, chart_index: int = 1):
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

    # Heatmap para cruces origen/destino.
    if render_heatmap_if_possible(df_chart, value_col):
        return

    # Tendencia temporal.
    if time_col:
        color_col = None

        for col in categorical_cols:
            unique_count = df_chart[col].nunique(dropna=True)

            if 1 < unique_count <= 12:
                if any(
                    keyword in normalize_text(col)
                    for keyword in ["afore", "origen", "destino", "grupo", "categoria"]
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
            labels=labels
        )

        fig = style_plotly_figure(
            fig,
            title=f"Tendencia de {prettify_label(value_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True)
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
                }
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
            st.plotly_chart(fig, use_container_width=True)
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
            }
        )

        fig.update_traces(
            texttemplate="%{text:,.0f}",
            textposition="outside",
            cliponaxis=False
        )

        fig = style_plotly_figure(
            fig,
            title=f"Ranking por {prettify_label(value_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True)
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
            }
        )

        fig = style_plotly_figure(
            fig,
            title=f"Relación entre {prettify_label(x_col)} y {prettify_label(y_col)}"
        )

        st.subheader("Visualización")
        st.plotly_chart(fig, use_container_width=True)
        return

    # Fallback: histograma.
    fig = px.histogram(
        df_chart,
        x=value_col,
        labels={
            value_col: prettify_label(value_col)
        }
    )

    fig = style_plotly_figure(
        fig,
        title=f"Distribución de {prettify_label(value_col)}"
    )

    st.subheader("Visualización")
    st.plotly_chart(fig, use_container_width=True)


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
# INTERFAZ
# ------------------------------------------------------------

init_session_state()
databricks_config = load_databricks_config()

st.title("💬 Asistente de Traspasos AFORE")

st.write(
    "Consulta información de traspasos AFORE mediante lenguaje natural. "
    "Puedes hacer preguntas sobre periodos, AFORE origen, AFORE destino, "
    "comparativos, tendencias mensuales, rankings y validaciones generales "
    "sin entrar directamente a Databricks."
)

with st.sidebar:
    st.header("Opciones")

    deep_thinking = st.toggle(
        "Deep thinking",
        value=True,
        help=(
            "No activa el Agent Mode real de Genie, pero envía una instrucción "
            "más completa para buscar respuestas con mayor estructura, validación "
            "e interpretación."
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

    if st.button("Reiniciar conversación"):
        reset_chat()
        st.rerun()

    st.caption(
        "La conexión con Databricks Genie está configurada por detrás mediante secrets."
    )


# ------------------------------------------------------------
# MENSAJE INICIAL
# ------------------------------------------------------------

if len(st.session_state.messages) == 0:
    st.session_state.messages.append({
        "role": "assistant",
        "content": (
            "Hola. Soy un asistente para consultar información de **traspasos AFORE**.\n\n"
            "Puedo ayudarte a responder preguntas como:\n\n"
            "- ¿Cuántos traspasos tuvo Profuturo en 2025 por mes?\n"
            "- ¿Qué AFORE recibió más traspasos durante 2025?\n"
            "- ¿Cuáles fueron las principales AFORE origen hacia Profuturo?\n"
            "- ¿Cómo se comparan los traspasos entre dos periodos?\n"
            "- ¿Cuál fue la tendencia mensual de traspasos por AFORE destino?\n"
            "- ¿Qué AFORE tuvo mayor participación en los traspasos recibidos?\n\n"
            "Cuando la respuesta incluya datos tabulares, también intentaré generar una visualización automática para facilitar el análisis."
        )
    })


# ------------------------------------------------------------
# HISTORIAL DE CHAT
# ------------------------------------------------------------

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


# ------------------------------------------------------------
# INPUT DEL USUARIO
# ------------------------------------------------------------

prompt = st.chat_input("Pregunta algo sobre traspasos...")

if prompt:
    st.session_state.messages.append({
        "role": "user",
        "content": prompt
    })

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
            st.markdown(assistant_text)

            if result["dataframes"]:
                for idx, df in enumerate(result["dataframes"], start=1):

                    if show_charts:
                        render_smart_visualization(df, chart_index=idx)

                    with st.expander(f"Ver tabla de resultados {idx}", expanded=True):
                        st.dataframe(df, use_container_width=True)

                        csv = df.to_csv(index=False).encode("utf-8")
                        st.download_button(
                            label=f"Descargar resultado {idx} en CSV",
                            data=csv,
                            file_name=f"resultado_genie_{idx}.csv",
                            mime="text/csv"
                        )

            if show_sql and result["sql"]:
                with st.expander("SQL generado por Genie"):
                    for idx, sql in enumerate(result["sql"], start=1):
                        st.code(sql, language="sql")

            if show_debug:
                with st.expander("Respuesta cruda de Genie"):
                    st.json(result["raw"])

            st.session_state.messages.append({
                "role": "assistant",
                "content": assistant_text
            })

        except Exception as e:
            error_text = str(e)

            if "PENDING_WAREHOUSE" in error_text:
                error_message = (
                    "La pregunta sí llegó a Genie, pero el SQL Warehouse no quedó listo a tiempo. "
                    "Revisa que el warehouse asignado al Genie Space esté encendido y disponible.\n\n"
                    f"Detalle técnico: `{e}`"
                )
            else:
                error_message = (
                    "No pude completar la consulta con Genie. "
                    "Revisa permisos del Genie Space, acceso al SQL Warehouse o disponibilidad de Databricks.\n\n"
                    f"Detalle técnico: `{e}`"
                )

            st.error(error_message)

            st.session_state.messages.append({
                "role": "assistant",
                "content": error_message
            })
