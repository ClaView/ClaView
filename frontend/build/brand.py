"""서비스 로고(아이콘 + 글자)를 화면에 넣는다.

화면 보안 규칙(CSP)이 이미지는 data: 만 허용하므로 로고 파일을 따로 서빙하지 않고
SVG를 HTML 안에 직접 넣는다. 원본 SVG는 frontend/src/brand/ 에 둔다.
"""
import re
from urllib.parse import quote
from paths import SRC

BRAND_DIR = SRC / "brand"
_SVG = re.compile(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*)</svg>', re.S)
_DEFS = re.compile(r"<defs>.*?</defs>", re.S)


def _read(name):
    text = (BRAND_DIR / name).read_text(encoding="utf-8").strip()
    m = _SVG.fullmatch(text)
    if not m:
        raise RuntimeError(f"로고 SVG 형식 오류: {name}")
    view_box, inner = m.groups()
    defs = _DEFS.search(inner)
    return view_box, (defs.group(0) if defs else ""), _DEFS.sub("", inner)


def sprite():
    """글자·아이콘 모양을 한 번만 정의해 두고 <use>로 여러 곳에서 쓴다."""
    logo_vb, defs, logo_body = _read("claview_logo.svg")
    mark_vb, _, mark_body = _read("claview_mark.svg")
    return ('<svg class="cv-sprite" aria-hidden="true" focusable="false" width="0" height="0">'
            f'{defs}<symbol id="cv-logo" viewBox="{logo_vb}">{logo_body}</symbol>'
            f'<symbol id="cv-mark" viewBox="{mark_vb}">{mark_body}</symbol></svg>'), logo_vb


def logo_markup(view_box):
    return (f'<svg class="brand-logo" viewBox="{view_box}" role="img" aria-label="ClaView">'
            '<use href="#cv-logo"/></svg>')


def favicon_link():
    text = (BRAND_DIR / "claview_mark.svg").read_text(encoding="utf-8").strip()
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(text, safe="")}">'
