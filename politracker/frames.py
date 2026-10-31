"""Export the presenter's slides as PNG images using headless Chromium (optional: needs a Chromium/Chrome binary on the machine).

Each slide is rendered by loading `<present url>?clean=1&freeze=1&scene=N` in a 1080x1920 window and saving a screenshot. The image on the portrait is
fetched from the web, so a virtual-time budget gives it a moment to load.
"""
import shutil
import subprocess
from pathlib import Path

WIDTH, HEIGHT = 1080, 1920
BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def find_browser(which=shutil.which):
    for name in BROWSERS:
        path = which(name)
        if path:
            return path
    return None


def render_frames(browser, url, count, out_dir, run=None, timeout=90):
    """Write scene-01.png .. scene-NN.png into `out_dir`; returns the list of paths. Raises RuntimeError naming the failing slide."""
    run = run or subprocess.run              # looked up at call time (not a default argument) so it can be swapped out in tests
    out_dir = Path(out_dir)
    paths = []
    for i in range(1, count + 1):
        path = out_dir / f"scene-{i:02d}.png"
        cmd = [browser, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", f"--window-size={WIDTH},{HEIGHT}",
               "--virtual-time-budget=4000", f"--screenshot={path}", f"{url}{'&' if '?' in url else '?'}clean=1&freeze=1&scene={i}"]
        try:
            run(cmd, capture_output=True, timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"slide {i}: {e}") from e
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"slide {i}: the browser produced no image")
        paths.append(path)
    return paths
