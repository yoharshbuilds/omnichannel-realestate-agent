"""Tiny dependency-free PDF writer, enough for brochures and cost sheets (one A4 page,
Helvetica headings, Courier for aligned tables)."""

from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("₹", "Rs. ")


def render(lines: list[tuple[str, str]]) -> bytes:
    """lines: (style, text) with style in {"title", "h", "body", "mono", "small", "gap"}."""
    fonts = {"title": ("F1", 22, 30), "h": ("F1", 13, 22), "body": ("F2", 11, 16),
             "mono": ("F3", 10, 15), "small": ("F2", 8, 12), "gap": ("F2", 11, 10)}
    ops, y = ["BT"], 790
    for style, text in lines:
        font, size, lead = fonts[style]
        y -= lead
        if style != "gap":
            ops.append(f"/{font} {size} Tf 1 0 0 1 50 {y} Tm ({_esc(text)}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def write(path: Path, lines: list[tuple[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render(lines))
    return path
