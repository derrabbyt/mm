"""Pulling the one picture that represents a happening out of a page.

Named for what it reads rather than `html`, which is a stdlib module.

Two traps, which warda sets both of and the HTML Sources still to come set too:
the `<img>` that looks like the picture is often a tracking pixel or a *related
event* card, while the page's own `og:image` is exactly the picture the site puts
on its social preview - the one image it considers to represent the thing.
"""

from bs4 import BeautifulSoup
from bs4.element import Tag

# Analytics beacons are rendered as 1x1 images; warda's detail pages carry one
# and nothing else, so a naive "first img" read returns a transparent pixel.
_PIXEL_DIMENSIONS = {"0", "1"}


def og_image(soup: BeautifulSoup) -> str | None:
    """The page's Open Graph image, or its Twitter-card equivalent."""
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
