"""Container entrypoint: HTTPS on 8443 (TLS_DIR holds tls.crt/tls.key; a self-signed pair is made at start if missing).
Local development stays plain: uvicorn app.main:app --port 8000
"""
import os

import uvicorn

from .tls import make_self_signed


def main():
    d = os.environ.get("TLS_DIR", "/tmp/tls")
    cert, key = os.path.join(d, "tls.crt"), os.path.join(d, "tls.key")
    if not (os.path.exists(cert) and os.path.exists(key)):
        cert, key = make_self_signed(d)
    # 1 worker on purpose: SSE fan-out broker is in memory
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8443")),
                ssl_certfile=cert, ssl_keyfile=key, workers=1, timeout_keep_alive=75)


if __name__ == "__main__":
    main()
