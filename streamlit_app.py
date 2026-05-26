import os
import pandas as pd
import streamlit as st
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
        return st.secrets.get(name, os.getenv(name, default))
    except Exception:
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
    config = {
        "host": get_secret_or_env("DATABRICKS_HOST"),
        "token": get_secret_or_env("DATABRICKS_TOKEN"),
        "space_id": get_secret_or_env("GENIE_SPACE_ID")
    }

    missing = [
        key for key, value in config.items()
        if not value
    ]

    if missing:
        st.error(
            "Faltan variables de conexión en Streamlit Secrets. "
            "Verifica que existan: DATABRICKS_HOST, DATABRICKS_TOKEN y GENIE_SPACE_ID."
        )
        st.stop()

    return config


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
            "- ¿Cuál fue la tendencia mensual de traspasos por AFORE destino?\n\n"
            "Escribe una pregunta en lenguaje natural y consultaré la información disponible en Genie."
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
                    st.subheader(f"Resultado tabular {idx}")
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
