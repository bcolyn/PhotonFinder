"""Astrometry.net through astroquery, with nova.astrometry.net replaced at the HTTP request:
astroquery's upload and our polling run for real."""
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from photonfinder.platesolver import AstrometryNetSolver, SolverCancelled, _CancellableAstrometryNet
from tests.conftest import fixed_solution_header


class Response:
    def __init__(self, json=None, text=""):
        self._json = json
        self.text = text
        self.status_code = 200

    def json(self):
        return self._json


class FakeNova:
    """nova.astrometry.net: accepts an upload, then reports the job as running for
    ``polls_before_result`` polls before giving ``result`` ('success' or 'failure')."""

    def __init__(self, polls_before_result=10**6, result="success"):
        self.polls_before_result = polls_before_result
        self.result = result
        self.requests = []  # (time, url)
        self._polls = 0

    def request(self, method, url, **kwargs):
        self.requests.append((time.monotonic(), url))
        if url.endswith("/login"):
            return Response({"status": "success", "session": "s1"})
        if url.endswith("/url_upload"):  # a source list
            return Response({"status": "success", "subid": 7})
        if "/submissions/" in url:
            return Response({"jobs": [42]})
        if url.endswith("/jobs/42/info"):
            self._polls += 1
            status = self.result if self._polls > self.polls_before_result else "solving"
            return Response({"status": status})
        if "/wcs_file/42" in url:
            return Response(text=fixed_solution_header().tostring())
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def nova(monkeypatch):
    def install(**kwargs):
        fake = FakeNova(**kwargs)
        monkeypatch.setattr(_CancellableAstrometryNet, "_request", lambda self, method, url, **kw:
                            fake.request(method, url, **kw))
        monkeypatch.setattr(_CancellableAstrometryNet, "POLL_SECONDS", 0.2)
        return fake
    return install


@pytest.fixture
def image_file(tmp_path) -> Path:
    """A frame with stars, so that source extraction finds something to send."""
    rng = np.random.default_rng(5)
    data = rng.normal(1000, 10, (200, 300))
    yy, xx = np.mgrid[0:200, 0:300]
    for _ in range(30):
        y, x = rng.uniform(10, 190), rng.uniform(10, 290)
        data += rng.uniform(500, 3000) * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.5 ** 2))
    path = tmp_path / "frame.fits"
    fits.PrimaryHDU(data.astype(np.float32)).writeto(path)
    return path


def _solve(image_file, cancelled=None):
    with AstrometryNetSolver("key") as solver:
        return solver.solve(image_file, cancelled=cancelled)


def test_a_cancel_stops_polling_and_abandons_the_job(image_file, nova):
    fake = nova()
    cancelled_at = []

    def cancel_after_three_polls():
        if sum("/info" in url for _, url in fake.requests) >= 3:
            cancelled_at.append(time.monotonic())
            return True
        return False

    started = time.monotonic()
    with pytest.raises(SolverCancelled):
        _solve(image_file, cancelled=cancel_after_three_polls)

    assert time.monotonic() - cancelled_at[0] < 0.5  # well within one poll interval
    assert all(when <= cancelled_at[0] for when, _ in fake.requests)  # nothing sent after it
    assert time.monotonic() - started < 5


def test_without_a_cancel_the_solution_is_returned(image_file, nova):
    nova(polls_before_result=2)

    solution = _solve(image_file, cancelled=lambda: False)

    assert solution["CRVAL1"] == 10.68
    assert (solution["NAXIS1"], solution["NAXIS2"]) == (300, 200)
