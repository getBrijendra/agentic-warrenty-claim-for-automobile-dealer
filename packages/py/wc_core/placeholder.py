"""Generated placeholder evidence (SVG) — no real photos of branded parts, no OEM logos."""
from __future__ import annotations

from html import escape

KIND_STYLE = {
    "photo": ("#2f3b4c", "PHOTO"), "dtc_snapshot": ("#3b2f4c", "DTC SNAPSHOT"),
    "battery_soh_report": ("#1f4c45", "BATTERY SOH REPORT"), "diag_log": ("#4c3b1f", "DIAGNOSTIC LOG"),
    "odometer_photo": ("#2f4c33", "ODOMETER PHOTO"), "invoice": ("#4c1f2a", "INVOICE"),
}


def _wrap(text: str, width: int = 44) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:4]


def placeholder_svg(kind: str, title: str, caption: str, wip_no: str, captured_at: str) -> bytes:
    bg, label = KIND_STYLE.get(kind, ("#333", kind.upper()))
    cap = "".join(f'<text x="24" y="{150 + i * 22}" font-size="15" fill="#e8ecf2">{escape(l)}</text>'
                  for i, l in enumerate(_wrap(caption)))
    ttl = "".join(f'<text x="24" y="{86 + i * 26}" font-size="20" font-weight="600" fill="#fff">{escape(l)}</text>'
                  for i, l in enumerate(_wrap(title, 36)[:2]))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" viewBox="0 0 480 300">
<rect width="480" height="300" rx="10" fill="{bg}"/>
<rect x="0" y="0" width="480" height="44" rx="10" fill="rgba(0,0,0,.25)"/>
<text x="24" y="28" font-family="Segoe UI, Arial" font-size="13" letter-spacing="2" fill="#cfd6e1">{label} · PLACEHOLDER</text>
<g font-family="Segoe UI, Arial">{ttl}{cap}</g>
<text x="24" y="282" font-family="Consolas, monospace" font-size="12" fill="#aab4c3">WIP {escape(wip_no)} · captured {escape(captured_at[:16])} · generated image, not a real photo</text>
</svg>"""
    return svg.encode("utf-8")
