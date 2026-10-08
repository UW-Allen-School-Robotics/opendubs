import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import serve


def test_no_certificate_means_http_only_on_8080():
    cfgs = serve.configs({"OPENDUBS_TLS_CERT": "/nonexistent.crt", "OPENDUBS_TLS_KEY": "/nonexistent.key"})
    assert [(c.port, bool(c.ssl_certfile)) for c in cfgs] == [(8080, False)]


def test_a_certificate_adds_https_on_8443_without_a_second_startup():
    with tempfile.TemporaryDirectory() as d:
        crt, key = os.path.join(d, "c.crt"), os.path.join(d, "c.key")
        for p in (crt, key):
            open(p, "w").close()
        cfgs = serve.configs({"OPENDUBS_TLS_CERT": crt, "OPENDUBS_TLS_KEY": key})
    assert [(c.port, bool(c.ssl_certfile)) for c in cfgs] == [(8080, False), (8443, True)]
    # the app's startup hooks (state_out, the serial port) must run once: only on HTTP
    assert [c.lifespan for c in cfgs] == ["on", "off"]
