"""Austin, TX (Travis County, FIPS 48453) - City of Austin open data.

assessor  REMOVED. The City of Austin Land Database (TCAD-derived appraisal values) has no licence (grey in
          usa/research/licence_registry_local.json); no green alternative.
permits   data.austintexas.gov Issued Construction Permits (3syk-w9eu, Public Domain U.S. Government works).
          Contractor / applicant names, phones and addresses are not requested.
311       data.austintexas.gov Austin 311 Public Data (xwdj-i9he, Public Domain).
"""
from ._common import case_rec, date, num, permit_rec, removed, run, socrata, soql_box

NAME, STATE, COUNTY_FIPS = "Austin", "TX", "48453"
BBOX = (-98.00, 30.05, -97.55, 30.55)
LICENCES = {"assessor": "removed (City of Austin Land Database: no licence, TCAD-derived values - grey)",
            "permits": "Public Domain (data.austintexas.gov 3syk-w9eu)",
            "311": "Public Domain (data.austintexas.gov xwdj-i9he)"}
DOMAIN = "data.austintexas.gov"


def permits(bbox):
    rows = socrata(DOMAIN, "3syk-w9eu", where=soql_box("location", bbox), order="issue_date DESC",
                   select="permit_type_desc,permit_class_mapped,work_class,description,issue_date,status_current,"
                          "total_job_valuation,original_address1,latitude,longitude")
    return [permit_rec(lon=num(r.get("longitude")), lat=num(r.get("latitude")), address=r.get("original_address1"),
                       date=date(r.get("issue_date")), type=r.get("permit_type_desc"),
                       work=" / ".join(x for x in (r.get("permit_class_mapped"), r.get("work_class")) if x) or None,
                       status=r.get("status_current"), value=num(r.get("total_job_valuation")), comments=r.get("description"))
            for r in rows if r.get("latitude")]


def cases311(bbox):
    rows = socrata(DOMAIN, "xwdj-i9he", where=soql_box("sr_location_lat_long", bbox), order="sr_created_date DESC",
                   select="sr_type_desc,sr_department_desc,sr_status_desc,sr_created_date,sr_location_lat,sr_location_long")
    return [case_rec(lon=num(r.get("sr_location_long")), lat=num(r.get("sr_location_lat")), date=date(r.get("sr_created_date")),
                     type=r.get("sr_department_desc"), title=r.get("sr_type_desc"), status=r.get("sr_status_desc"))
            for r in rows if r.get("sr_location_lat")]


def fetch(bbox):
    return run({"assessor": removed("City of Austin Land Database (TCAD-derived) has no licence (grey)"), "permits": permits, "311": cases311}, bbox)
