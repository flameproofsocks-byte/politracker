"""The look lives in static/theme.css. These tests keep it that way (and keep the agreed theme: light, cream, square)."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "politracker" / "static"
TEMPLATES = Path(__file__).resolve().parent.parent / "politracker" / "templates"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def declared(css):
    """{--name: value} for every custom property declared in a stylesheet."""
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css))


def test_theme_file_declares_every_variable_the_layout_uses():
    theme = (STATIC / "theme.css").read_text()
    for sheet in ("style.css", "present.css"):
        layout = (STATIC / sheet).read_text()
        used = set(re.findall(r"var\((--[\w-]+)", layout))
        missing = used - set(declared(theme)) - set(declared(layout)) - {"--dur"}   # --dur = a slide's length, set inline per progress segment
        assert not missing, f"{sheet} uses variables theme.css doesn't define: {sorted(missing)}"
        assert set(declared(layout)) <= {"--dur"}, f"{sheet} must not declare its own theme variables; they belong in theme.css"


def test_layout_and_templates_hardcode_no_theme_values():
    for sheet in ("style.css", "present.css"):                      # every stylesheet except theme.css itself
        layout = (STATIC / sheet).read_text()
        assert not HEX.search(layout), f"hard-coded hex colour in {sheet}: move it to theme.css"
        assert not re.search(r"rgba?\(", layout), f"hard-coded rgb()/rgba() in {sheet}: move it to theme.css"
        assert not re.search(r"border-radius:\s*[1-9]", layout), f"hard-coded border radius in {sheet}: use var(--radius)"
        assert "prefers-color-scheme" not in layout and "system-ui" not in layout
    for page in TEMPLATES.glob("*.html"):
        assert not HEX.search(re.sub(r"&#\d+;", "", page.read_text())), f"hard-coded colour in {page.name}"


def test_theme_is_light_cream_blue_with_red_and_square():
    t = declared((STATIC / "theme.css").read_text())

    def rgb(v):
        h = v.strip().lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    for name in ("--bg", "--surface", "--surface-2"):
        r, g, b = rgb(t[name])
        assert r > 200 and g > 190 and r >= g >= b, f"{name} should be a light warm cream/tan, got {t[name]}"   # warm = r>=g>=b
        assert (r, g, b) != (255, 255, 255), f"{name} must not be plain white"
    ar, ag, ab = rgb(t["--accent"])
    assert ab > ar and ab > ag, "main accent should be blue"
    rr, rg, rb = rgb(t["--accent-2"])
    assert rr > rg and rr > rb, "secondary accent should be red"
    assert t["--radius"].strip() == "0" and t["--radius-pill"].strip() == "0", "square corners"
    assert "dark" not in (STATIC / "theme.css").read_text().lower().replace("color-scheme: light", "").replace("dark mode", "")


def test_pages_load_theme_before_layout(client):
    page = client.get("/").data.decode()
    assert page.index("theme.css") < page.index("style.css")
    assert client.get("/static/theme.css").status_code == 200
