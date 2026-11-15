"""Streamlit entry point:  streamlit run streamlit_app.py

Shows the same page as the Flask server (run.py). The page's API calls travel through a Streamlit
component instead of HTTP - see stock_entry/bridge.py and web/api.streamlit.js.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from stock_entry.bridge import build_component_dir, handle_request, is_new_request, page_fingerprint
from stock_entry.config import Settings
from stock_entry.services import Services, build_services

COMPONENT_NAME = "stock_entry_page"
RESPONSE_KEY = "bridge_response"
HANDLED_KEY = "bridge_handled_request"
# The page brings its own layout: drop Streamlit's header and padding around it.
PAGE_CSS = """
<style>
  header[data-testid="stHeader"], footer, #MainMenu { display: none; }
  .block-container, [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: 100% !important; }
  iframe { display: block; }
  .stApp, [data-testid="stAppViewContainer"] { background: #0b1220; }  /* same as the page, no white band */
</style>
"""


@st.cache_resource
def get_services() -> Services:
    """One set of services for the whole server, so every visitor sees the same scan and progress."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return build_services(Settings())


@st.cache_resource
def get_component(version: str) -> Any:
    """One copy of the page per version. A push redeploys the script without restarting the server,
    so the cache key has to change with the page files, or the old page keeps being served."""
    target = Path(tempfile.mkdtemp(prefix=f"stock-entry-page-{version}-"))
    web_dir = Settings().web_dir
    return components.declare_component(COMPONENT_NAME, path=str(build_component_dir(web_dir, target, version)))


def main() -> None:
    st.set_page_config(page_title="Stock Entry", layout="wide")
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    try:
        services, page = get_services(), get_component(page_fingerprint(Settings().web_dir))
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).exception("Stock Entry could not start")
        st.error(f"Stock Entry could not start: {exc}")
        return

    # pop: the reply (up to ~600 KB for a full scan) goes to the page once instead of staying in every session
    request = page(response=st.session_state.pop(RESPONSE_KEY, None), key=COMPONENT_NAME, default=None)
    if not is_new_request(request, st.session_state.get(HANDLED_KEY)):
        return  # nothing new: this run only delivered the previous reply to the page
    st.session_state[RESPONSE_KEY] = handle_request(services, request)
    st.session_state[HANDLED_KEY] = request
    st.rerun()  # the next run hands the reply to the page


main()
