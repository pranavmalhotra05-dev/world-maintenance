"""Tulsa, OK (Tulsa County, FIPS 40143) - swap for Oklahoma City (no usable OKC / Oklahoma County open data).

Every Tulsa source failed the clear-green licence rule (usa/research/licence_registry_local.json), so nothing is fetched:
assessor  REMOVED. INCOG Tulsa County Parcels (Tulsa County Assessor data): no licence (grey).
          Zoning (City of Tulsa TulsaZoning_OpenData): "illustrative purposes" disclaimer only (grey).
permits   not open (City of Tulsa publishes no permit layer / dataset)
311       REMOVED. City of Tulsa Customer Care Verint cases: proxied service, no licence (grey).
"""
from ._common import removed, run

NAME, STATE, COUNTY_FIPS = "Tulsa", "OK", "40143"
BBOX = (-96.10, 35.95, -95.75, 36.35)
LICENCES = {"assessor": "removed (INCOG / Tulsa County Assessor parcels and City of Tulsa zoning: no licence - grey)",
            "permits": "not open",
            "311": "removed (City of Tulsa Verint cases: no licence - grey)"}


def fetch(bbox):
    return run({"assessor": removed("INCOG / Tulsa County Assessor parcels and City of Tulsa zoning have no licence (grey)"),
                "permits": None,
                "311": removed("City of Tulsa Verint 311 cases have no licence (grey)")}, bbox)
