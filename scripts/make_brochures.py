"""Write a simple placeholder brochure PDF for each project in data/projects.json.
Replace these with the developer's real brochures before going live."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import pdf  # noqa: E402


def main() -> None:
    out_dir = ROOT / "static" / "brochures"
    for p in json.loads((ROOT / "data" / "projects.json").read_text()):
        lines = [
            ("title", p["name"]),
            ("h", p["location"]),
            ("gap", ""),
            ("body", f"Configurations: {', '.join(p['configurations'])}"),
            ("body", f"Carpet area: {p['carpet_area']}"),
            ("body", f"Base price: {p['price_range']}"),
            ("body", f"Status: {p['status']}, possession {p['possession']}"),
            ("body", f"Amenities: {', '.join(p['amenities'])}"),
            ("body", f"Connectivity: {', '.join(p['connectivity'])}"),
            ("body", f"RERA: {p['rera']}"),
            ("gap", ""),
            ("body", f"Site visits: {p['site_address']}"),
            ("body", f"Hours: {p['visit_hours']}"),
            ("gap", ""),
            ("small", "Sample brochure for a demo. Not a real project."),
        ]
        print("wrote", pdf.write(out_dir / p["brochure_file"], lines))


if __name__ == "__main__":
    main()
