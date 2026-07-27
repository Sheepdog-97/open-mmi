from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from dashboard_contract_helpers import read_dashboard_styles


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "ui" / "web_dashboard" / "static" / "index.html"
APP = ROOT / "ui" / "web_dashboard" / "static" / "app.js"
VEHICLE = ROOT / "ui" / "web_dashboard" / "static" / "vehicle.js"


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._current = None
        self.articles = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and self._depth == 0:
            self._depth = 1
            self._current = {"text": [], "data_fields": set(), "data_bools": set()}
        elif self._depth:
            self._depth += 1
        if self._current is not None:
            if "data-field" in attrs:
                self._current["data_fields"].add(attrs["data-field"])
            if "data-bool" in attrs:
                self._current["data_bools"].add(attrs["data-bool"])

    def handle_endtag(self, tag):
        if not self._depth:
            return
        self._depth -= 1
        if self._depth == 0 and self._current is not None:
            self._current["text"] = " ".join(" ".join(self._current["text"]).split())
            self.articles.append(self._current)
            self._current = None

    def handle_data(self, data):
        if self._current is not None and data.strip():
            self._current["text"].append(data)


class DashboardVehicleUiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")
        cls.styles = read_dashboard_styles()
        cls.vehicle = VEHICLE.read_text(encoding="utf-8")
        parser = _ArticleParser()
        parser.feed(cls.index)
        cls.articles = parser.articles

    def test_climate_page_uses_recirculation_and_omits_inactive_air_intake(self):
        recirculation = [
            article for article in self.articles
            if "recirculation" in article["data_bools"]
        ]
        self.assertEqual(len(recirculation), 1)
        self.assertIn("Re-circulation", recirculation[0]["text"])
        self.assertFalse(any("front_demist" in item["data_bools"] for item in self.articles))
        self.assertFalse(any("air_intake" in item["data_fields"] for item in self.articles))

    def test_fuel_remaining_field_uses_the_verified_litre_signal(self):
        fuel_articles = [
            article for article in self.articles
            if "fuel_l" in article["data_fields"]
        ]
        self.assertEqual(len(fuel_articles), 2)
        self.assertTrue(all("Fuel remaining" in article["text"] for article in fuel_articles))
        self.assertTrue(all(article["text"].endswith("L") for article in fuel_articles))
        self.assertIn("fuel_l: formatNumber(fuel.level_l, 0)", self.vehicle)
        self.assertIn("fuelLowWarning: fuel.low_level_warning", self.vehicle)
        self.assertIn(
            'documentElement.classList.toggle("openmmi-fuel-low-warning", warningActive)',
            self.vehicle,
        )
        self.assertIn(':root.openmmi-fuel-low-warning [data-field="fuel_l"]', self.styles)
        self.assertIn(':root.openmmi-fuel-low-warning [data-field="fuel_l"] + small', self.styles)
        self.assertIn("color: #ffd60a !important;", self.styles)

    def test_rpm_fill_clips_a_fixed_track_gradient(self):
        self.assertIn('setProperty("--rpm-fill"', self.vehicle)
        start = self.styles.index("/* --- Open MMI vehicle UI corrections start --- */")
        end = self.styles.index("/* --- Open MMI vehicle UI corrections end --- */", start)
        block = self.styles[start:end]
        self.assertIn("clip-path: inset", block)
        self.assertIn("var(--rpm-fill)", block)
        self.assertIn("rgba(255, 60, 56, 1) 86% 100%", block)
        self.assertIn("transform: none !important", block)

    def test_blower_panel_uses_rectangular_card_geometry(self):
        start = self.styles.index("/* --- Open MMI vehicle UI corrections start --- */")
        end = self.styles.index("/* --- Open MMI vehicle UI corrections end --- */", start)
        block = self.styles[start:end]
        rule = re.search(r"\.blower-card\s*\{(?P<body>.*?)\}", block, re.S)
        self.assertIsNotNone(rule)
        body = rule.group("body")
        self.assertIn("width: 100% !important", body)
        self.assertIn("aspect-ratio: auto !important", body)
        self.assertIn("border-radius: 16px !important", body)
        self.assertNotIn("border-radius: 50%", body)


if __name__ == "__main__":
    unittest.main()
