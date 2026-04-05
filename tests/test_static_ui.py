"""Smoke tests for the ZRO Helper web UI (React + Vite build)."""
import re
from pathlib import Path
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

_static = Path(__file__).parent.parent / "static"


def _bundle_text():
    """Return the text of the compiled JS bundle."""
    # Support both stable name (index.js) and legacy hashed names (index-*.js)
    bundle = _static / "assets" / "index.js"
    if not bundle.exists():
        bundles = list((_static / "assets").glob("index-*.js"))
        assert bundles, "No built JS bundle found — run ./build-ui.sh first"
        bundle = bundles[0]
    return bundle.read_text(encoding="utf-8", errors="ignore")


class TestStaticUi:
    def test_root_serves_index_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "ZRO Calibration Helper" in resp.text

    def test_index_has_react_root(self):
        resp = client.get("/")
        assert '<div id="root">' in resp.text

    def test_index_links_js_bundle(self):
        resp = client.get("/")
        assert re.search(r'src="/assets/index[\w-]*\.js"', resp.text)

    def test_index_links_css_bundle(self):
        resp = client.get("/")
        assert re.search(r'href="/assets/index[\w-]*\.css"', resp.text)

    def test_js_bundle_is_served(self):
        html = client.get("/").text
        match = re.search(r'src="(/assets/index[\w-]*\.js)"', html)
        assert match, "No JS bundle link found in index.html"
        resp = client.get(match.group(1))
        assert resp.status_code == 200

    def test_css_bundle_is_served(self):
        html = client.get("/").text
        match = re.search(r'href="(/assets/index[\w-]*\.css)"', html)
        assert match, "No CSS bundle link found in index.html"
        resp = client.get(match.group(1))
        assert resp.status_code == 200

    def test_bundle_references_colorspace_zro(self):
        bundle = _bundle_text()
        assert "ZRO" in bundle
        assert "ColourSpace" in bundle or "Colorspace" in bundle

    def test_bundle_references_dogegen(self):
        assert "Dogegen" in _bundle_text()

    def test_bundle_references_ten_bit_codes(self):
        assert "10-bit Codes" in _bundle_text()

    def test_bundle_contains_measurement_delta_e(self):
        """delta_e field must be referenced in the bundle."""
        assert "delta_e" in _bundle_text()

    def test_bundle_contains_wizard_step_labels(self):
        """Step label strings (not minified) must appear in the bundle."""
        bundle = _bundle_text()
        # These are string literals passed as props — not minified
        assert "Pre-Cal" in bundle or "pre_grayscale" in bundle
        assert "white_balance" in bundle or "White Balance" in bundle
        assert "color_tuner" in bundle or "Color Tuner" in bundle

    def test_bundle_no_argyllcms_references(self):
        bundle = _bundle_text()
        assert "spotread" not in bundle
        assert "argyllcms" not in bundle.lower()

    def test_bundle_no_lightspace_connect_controls(self):
        bundle = _bundle_text()
        assert "UPGCI" not in bundle
        assert "showTestPattern" not in bundle
