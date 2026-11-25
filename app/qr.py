"""Server-side QR code as inline SVG (uses reportlab's pure-python QR encoder; no network, no image libs)."""
from reportlab.graphics.barcode.qrencoder import QRCode, QRErrorCorrectLevel


def qr_svg(text, scale=6, border=4):
    qr = QRCode(None, QRErrorCorrectLevel.M)   # None = pick the smallest version that fits
    qr.addData(text)
    qr.make()
    n = qr.getModuleCount()
    size = (n + 2 * border) * scale
    cells = "".join(
        f"M{(c + border) * scale},{(r + border) * scale}h{scale}v{scale}h-{scale}z"
        for r in range(n) for c in range(n) if qr.isDark(r, c))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'role="img" aria-label="Authenticator QR code"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{cells}" fill="#000"/></svg>')
