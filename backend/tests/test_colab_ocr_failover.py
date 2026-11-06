"""COLAB_OCR_URL may list several OCR workers (e.g. Colab + Kaggle); the client
tries them in order so one notebook's runtime ending doesn't fail uploads."""
import pytest
import requests

from colab_ocr_client import colab_base_urls, send_images_to_colab_ocr


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = "ERR_NGROK_3200 endpoint offline" if status >= 400 else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Not Found")

    def json(self):
        return self._payload


@pytest.fixture
def page(tmp_path):
    orig = tmp_path / "orig.png"
    p = tmp_path / "p.png"
    orig.write_bytes(b"orig-bytes")
    p.write_bytes(b"p-bytes")
    return [{"orig": str(orig), "P": str(p)}]


def test_base_urls_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("COLAB_OCR_URL", " https://a.ngrok.app/ ,https://b.ngrok.app,, ")
    assert colab_base_urls() == ["https://a.ngrok.app", "https://b.ngrok.app"]
    assert colab_base_urls("https://c.ngrok.app/") == ["https://c.ngrok.app"]


def test_ocr_fails_over_and_resends_full_files(monkeypatch, page):
    tried, bodies = [], []

    def fake_post(url, files, timeout):
        tried.append(url)
        bodies.append([f[1][1].read() for f in files])
        if "dead" in url:
            return _Response(404)
        return _Response(200, {"success": True})

    monkeypatch.setattr(requests, "post", fake_post)
    result = send_images_to_colab_ocr(page, "https://dead.ngrok.app,https://live.ngrok.app")

    assert result == {"success": True}
    assert tried == ["https://dead.ngrok.app/ocr", "https://live.ngrok.app/ocr"]
    # The retry must send the images from the start, not an empty stream.
    assert bodies[1] == [b"orig-bytes", b"p-bytes"]


def test_ocr_raises_with_every_worker_error(monkeypatch, page):
    def fake_post(url, files, timeout):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(Exception) as exc:
        send_images_to_colab_ocr(page, "https://a.ngrok.app,https://b.ngrok.app")
    assert "a.ngrok.app" in str(exc.value) and "b.ngrok.app" in str(exc.value)


def test_ocr_without_url_raises(monkeypatch, page):
    monkeypatch.delenv("COLAB_OCR_URL", raising=False)
    with pytest.raises(ValueError):
        send_images_to_colab_ocr(page)
