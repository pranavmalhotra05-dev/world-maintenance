"""Huntsville, AL (Madison County, FIPS 01089) - City of Huntsville GIS (maps.huntsvilleal.gov).

Every Huntsville source failed the clear-green licence rule (usa/research/licence_registry_local.json), so nothing is fetched:
assessor  REMOVED. Planning/FindAProperty layer 1 MadisonParcelInfo: no licence stated (grey).
permits   REMOVED. Licenses/BuildingPermits layer 0: no licence, service keyword 'Restricted' (grey).
311       not open (Huntsville Connect requests are not published)
"""
from ._common import removed, run

NAME, STATE, COUNTY_FIPS = "Huntsville", "AL", "01089"
BBOX = (-86.80, 34.55, -86.38, 34.85)
LICENCES = {"assessor": "removed (City of Huntsville GIS parcels: no licence - grey)",
            "permits": "removed (City of Huntsville GIS building permits: no licence, 'Restricted' - grey)",
            "311": "not open"}


def fetch(bbox):
    return run({"assessor": removed("City of Huntsville GIS parcels (Madison County tax data) have no licence (grey)"),
                "permits": removed("City of Huntsville building permits have no licence and are keyworded 'Restricted' (grey)"),
                "311": None}, bbox)
