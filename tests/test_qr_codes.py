from io import BytesIO

import pytest
from PIL import Image

from services.qr_service import make_qr_png


def test_make_qr_png_returns_print_sized_high_contrast_image():
    png = make_qr_png("tok-ent-sample-7f91ac32")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    image = Image.open(BytesIO(png)).convert("RGB")
    assert image.width == image.height
    assert image.width >= 300
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert any(min(pixel) < 60 for pixel in image.getdata())


def test_make_qr_png_rejects_empty_payload():
    with pytest.raises(ValueError):
        make_qr_png(" ")
