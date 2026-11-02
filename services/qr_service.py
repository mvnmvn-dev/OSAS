"""Generate high-contrast QR images for OSAS scan tokens and visitor passes."""
from __future__ import annotations

from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_Q


def make_qr_png(payload: str) -> bytes:
    """Return a print-friendly black-on-white PNG containing only the payload."""
    value = str(payload or "").strip()
    if not value:
        raise ValueError("QR payload cannot be empty")

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_Q,
        box_size=12,
        border=4,
    )
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#10243A", back_color="#FFFFFF")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
