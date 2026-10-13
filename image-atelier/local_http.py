"""Reusable TLS placeholder for clients whose validated URLs are plain loopback HTTP.

No certificates are needed for these HTTP URLs. Avoid rebuilding a default CA
store on every status poll. HTTPS clients keep their separate normal verification.
"""
import ssl

LOCAL_HTTP_TLS_CONTEXT=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
