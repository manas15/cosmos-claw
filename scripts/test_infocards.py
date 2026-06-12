"""Render the info cards standalone to eyeball them (no GPU / no LLM)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import infocards  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs" / "infocard_test"
OUT.mkdir(parents=True, exist_ok=True)

POIS = [
    {"name": "Runyon Canyon Park", "category": "park", "note": "15 min walk"},
    {"name": "Trader Joe's", "category": "grocery", "note": "5 min drive"},
    {"name": "Hollywood/Highland Metro", "category": "transit", "note": "12 min walk"},
    {"name": "In-N-Out Burger", "category": "food", "note": "8 min drive"},
    {"name": "The Grove", "category": "shopping", "note": "15 min drive"},
]
LOCATION = "Hollywood, Los Angeles, California"

print("map…")
print(infocards.make_info_card(
    "map", {"location_query": LOCATION, "pois": POIS, "title": "Explore the neighborhood"},
    str(OUT / "map.png")))

print("neighborhood…")
print(infocards.render_neighborhood_card(
    str(OUT / "neighborhood.png"), title="The Neighborhood", pois=POIS, location=LOCATION))

print("price…")
print(infocards.make_info_card("price", {"price": "$239 / night"}, str(OUT / "price.png")))

print("rating…")
print(infocards.make_info_card(
    "rating", {"rating": {"score": "4.98", "reviews": "288", "superhost": True, "note": "Superhost"}},
    str(OUT / "rating.png")))

print("done ->", OUT)
