"""Live service transports: count actual sends and stop a run after an authentication failure."""

from __future__ import annotations

import threading

import httpx

from claview_b.contracts import StageFailure
from claview_b.fetch import KosisClient
from claview_b.models import OpenAIModel
from claview_common.settings import B_MODEL_ID


class ServiceTransports:
    """One sequential screen run. Module selection limits remain in B, not invented here."""

    def __init__(self):
        self.lock = threading.Lock()
        self.start()

    def start(self):
        with self.lock:
            self.sends = {"B_model": 0, "KOSIS": 0}
            self.halted = None

    def before(self, service):
        with self.lock:
            if self.halted:
                raise StageFailure("service_credential_halted", "error")
            self.sends[service] += 1

    def after(self, service, status):
        if status in (401, 403):
            with self.lock:
                self.halted = f"{service.lower()}_http_{status}"

    def transport(self, service, inner=None):
        owner = self

        class CountedTransport(httpx.BaseTransport):
            def __init__(self):
                self.inner = inner or httpx.HTTPTransport(retries=0)

            def handle_request(self, request):
                owner.before(service)
                response = self.inner.handle_request(request)
                owner.after(service, response.status_code)
                return response

            def close(self):
                self.inner.close()

        return CountedTransport()

    def clients(self, openai_key, kosis_key, *, model_transport=None, data_transport=None):
        # Keys stay in the existing clients. In particular, never log KOSIS request URLs.
        model = None
        client = None
        if openai_key:
            model = OpenAIModel(openai_key, B_MODEL_ID, retries=0,
                                client=httpx.Client(timeout=120, follow_redirects=False,
                                                    transport=self.transport("B_model", model_transport)))
        if kosis_key:
            client = KosisClient(kosis_key, retries=0,
                                 client=httpx.Client(timeout=45, follow_redirects=False,
                                                     transport=self.transport("KOSIS", data_transport)))
        return model, client
