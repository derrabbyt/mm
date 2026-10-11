from bs4 import BeautifulSoup
from bs4.element import Tag

_PIXEL_DIMENSIONS = {"0", "1"}


def og_image(soup: BeautifulSoup) -> str | None:
    for selector in (
        'meta[property="og:image"][content]',
        'meta[name="og:image"][content]',
        'meta[name="twitter:image"][content]',
    ):
        tag = soup.select_one(selector)
        if tag is not None:
            content = (tag.get("content") or "").strip()
            if content:
                return content
    return None


def is_tracking_pixel(img: Tag) -> bool:
    return (
        str(img.get("width", "")).strip() in _PIXEL_DIMENSIONS
        and str(img.get("height", "")).strip() in _PIXEL_DIMENSIONS
    )
