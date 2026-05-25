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

    timeout = timedelta(minutes=5)

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


def demo_answer(prompt: str):
    """
    Respuesta temporal mientras se consiguen credenciales reales.
    """
    return {
        "text": (
            "Modo demo activo. Esta respuesta todavía no viene de Genie.\n\n"
            f"Pregunta recibida: **{prompt}**\n\n"
            "Cuando conectemos Databricks, aquí aparecerá la respuesta generada "
            "por el Genie Space de traspasos, junto con tablas o resultados "
            "cuando la pregunta devuelva información estructurada."
        ),
        "sql": [],
        "dataframes": [],
        "raw": {}
    }


# ------------------------------------------------------------
# INTERFAZ
# ------------------------------------------------------------

init_session_state()

st.title("💬 Asistente de Traspasos AFORE")

st.write(
    "Interfaz tipo chatbot para consultar información de traspasos usando "
    "Databricks Genie. El objetivo es que usuarios no técnicos puedan hacer "
    "preguntas de negocio sin entrar directamente a Databricks."
)

with st.sidebar:
    st.header("Configuración")

    demo_mode = st.toggle(
        "Usar modo demo",
        value=True,
        help="Actívalo mientras todavía no tengas token o Genie Space ID."
    )

    default_host = get_secret_or_env("DATABRICKS_HOST")
    default_space_id = get_secret_or_env("GENIE_SPACE_ID")

    databricks_host = st.text_input(
        "Databricks Host",
        value=default_host,
        placeholder="https://adb-xxxxxxxx.azuredatabricks.net"
    )

    databricks_token = st.text_input(
        "Databricks Token",
        value="",
        type="password",
        placeholder="dapi..."
    )

    genie_space_id = st.text_input(
        "Genie Space ID",
        value=default_space_id,
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    )

    show_sql = st.toggle(
        "Mostrar SQL generado",
        value=False,
        help="Útil para validación técnica contra Power BI o queries manuales."
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
        "Para producción, lo ideal es no pedir token al usuario final. "
        "Conviene usar secrets, variables de entorno, OAuth o service principal."
    )


# ------------------------------------------------------------
# VALIDACIÓN DE CREDENCIALES
# ------------------------------------------------------------

if not demo_mode:
    missing = []

    if not databricks_host:
        missing.append("Databricks Host")

    if not databricks_token:
        missing.append("Databricks Token")

    if not genie_space_id:
        missing.append("Genie Space ID")

    if missing:
        st.info(
            "Agrega los siguientes datos para conectar con Genie: "
            + ", ".join(missing),
            icon="🗝️"
        )


# ------------------------------------------------------------
# MENSAJE INICIAL
# ------------------------------------------------------------

if len(st.session_state.messages) == 0:
    st.session_state.messages.append({
        "role": "assistant",
        "content": (
            "Hola. Puedes preguntarme sobre traspasos AFORE. "
            "Por ejemplo: **¿Cuántos traspasos tuvo Profuturo en 2025 por mes?**"
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
            with st.spinner("Consultando Genie..."):
                if demo_mode:
                    result = demo_answer(prompt)
                else:
                    result = ask_genie(
                        host=databricks_host,
                        token=databricks_token,
                        space_id=genie_space_id,
                        prompt=prompt,
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
            error_message = (
                "No pude completar la consulta con Genie. "
                "Revisa host, token, permisos del Genie Space y acceso al SQL Warehouse.\n\n"
                f"Detalle técnico: `{e}`"
            )

            st.error(error_message)

            st.session_state.messages.append({
                "role": "assistant",
                "content": error_message
            })
