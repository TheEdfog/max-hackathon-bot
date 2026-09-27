import ssl
from pathlib import Path
from urllib.parse import urlparse


def tls_context(api_url):
    context = ssl.create_default_context()
    # Trust is scoped to the MAX client only, never OS-wide. Downloaded over
    # verified HTTPS from https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer
    if urlparse(api_url).hostname == "platform-api2.max.ru":
        context.load_verify_locations(Path(__file__).parent / "certs" / "russian_trusted_root_ca.crt")
    return context
