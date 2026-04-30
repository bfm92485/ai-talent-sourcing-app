"""
customize_job_applicant.py
==========================
Reorganizes the ERPNext Job Applicant DocType into a 4-tab layout:
  Tab 1 — Applicant (Identity, Application, Skills, Resume & Materials)
  Tab 2 — Experience & Education
  Tab 3 — AI Enrichment (AI Assessment, Raw Data)
  Tab 4 — Salary Expectation (existing, untouched)

Uses Custom Fields + Property Setters exclusively (upgrade-safe).
Idempotent: safe to run multiple times.
Reversible: see revert_job_applicant.py.

Execution modes:
  Remote API: python3 customize_job_applicant.py
  Local bench: bench --site <site> execute customize_job_applicant.run

Environment variables (for remote mode):
  ERPNEXT_URL       - e.g. https://erpnext-v16-talent-sourcing-production.up.railway.app
  ERPNEXT_API_KEY   - API key for Administrator
  ERPNEXT_API_SECRET - API secret for Administrator
"""

import os
import sys
import json
import requests
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODULE = "HR"  # Must be a valid Module Def in Frappe; HR owns Job Applicant

ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erpnext-v16-talent-sourcing-production.up.railway.app")
API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")

HEADERS = {
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Actual field names (verified against live instance 2026-04-30)
# ---------------------------------------------------------------------------
# Core fields: applicant_name, email_id, phone_number, job_title, designation,
#   country, status, applicant_rating, resume_link, resume_preview_html,
#   open_resume_button, cover_letter, resume_attachment, notes, source,
#   source_name, employee_referral, salary_expectation_tab, currency,
#   lower_range, upper_range
#
# Existing custom fields: custom_current_company, custom_skills_list,
#   custom_enrichment_extras, custom_source, custom_linkedin_url,
#   custom_github_url, custom_ai_score, custom_ai_justification,
#   custom_persona_tag, custom_enrichment_status, custom_data_ttl_expiry,
#   custom_experience, custom_education
#
# Existing structural: details_section (Section Break), column_break_3,
#   column_break_enue, section_break_6 (Resume), source_and_rating_section,
#   column_break_13, column_break_18

# ---------------------------------------------------------------------------
# Target layout definition
# ---------------------------------------------------------------------------
# We create new Tab/Section/Column Breaks as Custom Fields and reorder
# existing fields via Property Setters (property="insert_after").

# New structural Custom Fields to create (Tab Breaks, Section Breaks, Column Breaks)
NEW_STRUCTURAL_FIELDS = [
    # TAB 1: "Applicant" — rename existing details_section via Property Setter
    # We'll insert a Tab Break at the very beginning
    {
        "fieldname": "custom_tab_applicant",
        "label": "Applicant",
        "fieldtype": "Tab Break",
        "insert_after": "",  # First field
        "module": MODULE,
    },
    # Section: Identity
    {
        "fieldname": "custom_section_identity",
        "label": "Identity",
        "fieldtype": "Section Break",
        "insert_after": "custom_tab_applicant",
        "module": MODULE,
    },
    # Column break after phone_number (col1 → col2)
    {
        "fieldname": "custom_col_identity_2",
        "label": "",
        "fieldtype": "Column Break",
        "insert_after": "country",
        "module": MODULE,
    },
    # Column break after custom_current_company (col2 → col3)
    {
        "fieldname": "custom_col_identity_3",
        "label": "",
        "fieldtype": "Column Break",
        "insert_after": "custom_current_company",
        "module": MODULE,
    },
    # Section: Application
    {
        "fieldname": "custom_section_application",
        "label": "Application",
        "fieldtype": "Section Break",
        "insert_after": "custom_persona_tag",
        "module": MODULE,
    },
    # Column break in Application section (col1 → col2)
    {
        "fieldname": "custom_col_application_2",
        "label": "",
        "fieldtype": "Column Break",
        "insert_after": "designation",
        "module": MODULE,
    },
    # Section: Skills (full width)
    {
        "fieldname": "custom_section_skills",
        "label": "Skills",
        "fieldtype": "Section Break",
        "insert_after": "employee_referral",
        "module": MODULE,
    },
    # Section: Resume & Materials
    {
        "fieldname": "custom_section_resume",
        "label": "Resume & Materials",
        "fieldtype": "Section Break",
        "insert_after": "custom_skills_list",
        "module": MODULE,
    },
    # Column break in Resume section (col1 → col2)
    {
        "fieldname": "custom_col_resume_2",
        "label": "",
        "fieldtype": "Column Break",
        "insert_after": "resume_attachment",
        "module": MODULE,
    },
    # TAB 2: Experience & Education
    {
        "fieldname": "custom_tab_experience",
        "label": "Experience & Education",
        "fieldtype": "Tab Break",
        "insert_after": "notes",
        "module": MODULE,
    },
    # Section: Experience
    {
        "fieldname": "custom_section_experience",
        "label": "Experience",
        "fieldtype": "Section Break",
        "insert_after": "custom_tab_experience",
        "module": MODULE,
    },
    # Section: Education
    {
        "fieldname": "custom_section_education",
        "label": "Education",
        "fieldtype": "Section Break",
        "insert_after": "custom_experience",
        "module": MODULE,
    },
    # TAB 3: AI Enrichment
    {
        "fieldname": "custom_tab_ai_enrichment",
        "label": "AI Enrichment",
        "fieldtype": "Tab Break",
        "insert_after": "custom_education",
        "module": MODULE,
    },
    # Section: AI Assessment
    {
        "fieldname": "custom_section_ai_assessment",
        "label": "AI Assessment",
        "fieldtype": "Section Break",
        "insert_after": "custom_tab_ai_enrichment",
        "module": MODULE,
    },
    # Column break in AI Assessment (col1 → col2 for justification)
    {
        "fieldname": "custom_col_ai_2",
        "label": "",
        "fieldtype": "Column Break",
        "insert_after": "custom_enrichment_status",
        "module": MODULE,
    },
    # Section: Raw Data (collapsible, collapsed)
    {
        "fieldname": "custom_section_raw_data",
        "label": "Raw Data",
        "fieldtype": "Section Break",
        "insert_after": "custom_ai_justification",
        "collapsible": 1,
        "collapsible_depends_on": "",
        "module": MODULE,
    },
]

# Property Setters for field reordering (insert_after changes)
REORDER_SETTERS = [
    # TAB 1 — Identity section ordering
    # col1: applicant_name, email_id, phone_number, country
    {"field_name": "applicant_name", "property": "insert_after", "value": "custom_section_identity"},
    {"field_name": "email_id", "property": "insert_after", "value": "applicant_name"},
    {"field_name": "phone_number", "property": "insert_after", "value": "email_id"},
    {"field_name": "country", "property": "insert_after", "value": "phone_number"},
    # col2: custom_linkedin_url, custom_github_url, custom_current_company
    # (custom_col_identity_2 is after country)
    {"field_name": "custom_linkedin_url", "property": "insert_after", "value": "custom_col_identity_2"},
    {"field_name": "custom_github_url", "property": "insert_after", "value": "custom_linkedin_url"},
    {"field_name": "custom_current_company", "property": "insert_after", "value": "custom_github_url"},
    # col3: status, applicant_rating, custom_persona_tag
    # (custom_col_identity_3 is after custom_current_company)
    {"field_name": "status", "property": "insert_after", "value": "custom_col_identity_3"},
    {"field_name": "applicant_rating", "property": "insert_after", "value": "status"},
    {"field_name": "custom_persona_tag", "property": "insert_after", "value": "applicant_rating"},

    # Application section: col1: job_title, designation; col2: custom_source, source, source_name, employee_referral
    {"field_name": "job_title", "property": "insert_after", "value": "custom_section_application"},
    {"field_name": "designation", "property": "insert_after", "value": "job_title"},
    # (custom_col_application_2 is after designation)
    {"field_name": "custom_source", "property": "insert_after", "value": "custom_col_application_2"},
    {"field_name": "source", "property": "insert_after", "value": "custom_source"},
    {"field_name": "source_name", "property": "insert_after", "value": "source"},
    {"field_name": "employee_referral", "property": "insert_after", "value": "source_name"},

    # Skills section (full width)
    # (custom_section_skills is after employee_referral)
    {"field_name": "custom_skills_list", "property": "insert_after", "value": "custom_section_skills"},

    # Resume & Materials section
    # col1: resume_link, resume_preview_html, open_resume_button, resume_attachment
    {"field_name": "resume_link", "property": "insert_after", "value": "custom_section_resume"},
    {"field_name": "resume_preview_html", "property": "insert_after", "value": "resume_link"},
    {"field_name": "open_resume_button", "property": "insert_after", "value": "resume_preview_html"},
    {"field_name": "resume_attachment", "property": "insert_after", "value": "open_resume_button"},
    # col2: cover_letter, notes
    # (custom_col_resume_2 is after resume_attachment)
    {"field_name": "cover_letter", "property": "insert_after", "value": "custom_col_resume_2"},
    {"field_name": "notes", "property": "insert_after", "value": "cover_letter"},

    # TAB 2 — Experience & Education
    # (custom_tab_experience after notes)
    # (custom_section_experience after custom_tab_experience)
    {"field_name": "custom_experience", "property": "insert_after", "value": "custom_section_experience"},
    # (custom_section_education after custom_experience)
    {"field_name": "custom_education", "property": "insert_after", "value": "custom_section_education"},

    # TAB 3 — AI Enrichment
    # (custom_tab_ai_enrichment after custom_education)
    # (custom_section_ai_assessment after custom_tab_ai_enrichment)
    # col1: custom_ai_score, custom_persona_tag (already in identity), custom_enrichment_status
    {"field_name": "custom_ai_score", "property": "insert_after", "value": "custom_section_ai_assessment"},
    {"field_name": "custom_enrichment_status", "property": "insert_after", "value": "custom_ai_score"},
    # col2: custom_ai_justification (full)
    # (custom_col_ai_2 after custom_enrichment_status)
    {"field_name": "custom_ai_justification", "property": "insert_after", "value": "custom_col_ai_2"},

    # Raw Data section (collapsible)
    # (custom_section_raw_data after custom_ai_justification)
    {"field_name": "custom_enrichment_extras", "property": "insert_after", "value": "custom_section_raw_data"},
    {"field_name": "custom_data_ttl_expiry", "property": "insert_after", "value": "custom_enrichment_extras"},
]

# Property Setters for field flags (bold, in_preview, in_global_search, collapsible)
FLAG_SETTERS = [
    # bold = 1
    {"field_name": "applicant_name", "property": "bold", "value": "1", "property_type": "Check"},
    {"field_name": "email_id", "property": "bold", "value": "1", "property_type": "Check"},
    {"field_name": "status", "property": "bold", "value": "1", "property_type": "Check"},
    # in_preview = 1 (shows in sidebar/list preview)
    {"field_name": "applicant_name", "property": "in_preview", "value": "1", "property_type": "Check"},
    {"field_name": "email_id", "property": "in_preview", "value": "1", "property_type": "Check"},
    {"field_name": "status", "property": "in_preview", "value": "1", "property_type": "Check"},
    {"field_name": "job_title", "property": "in_preview", "value": "1", "property_type": "Check"},
    {"field_name": "applicant_rating", "property": "in_preview", "value": "1", "property_type": "Check"},
    # in_global_search = 1
    {"field_name": "applicant_name", "property": "in_global_search", "value": "1", "property_type": "Check"},
    {"field_name": "email_id", "property": "in_global_search", "value": "1", "property_type": "Check"},
    {"field_name": "custom_skills_list", "property": "in_global_search", "value": "1", "property_type": "Check"},
    {"field_name": "custom_current_company", "property": "in_global_search", "value": "1", "property_type": "Check"},
]

# Hide old structural fields that are now replaced
HIDE_OLD_STRUCTURAL = [
    "details_section",
    "column_break_3",
    "column_break_enue",
    "section_break_6",
    "source_and_rating_section",
    "column_break_13",
]


# ---------------------------------------------------------------------------
# API Helper Functions
# ---------------------------------------------------------------------------
def api_get(endpoint: str, params: dict = None) -> dict:
    """GET request to Frappe REST API."""
    url = f"{ERPNEXT_URL}{endpoint}"
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def api_post(endpoint: str, data: dict) -> dict:
    """POST request to Frappe REST API."""
    url = f"{ERPNEXT_URL}{endpoint}"
    resp = requests.post(url, headers=HEADERS, json=data)
    resp.raise_for_status()
    return resp.json()


def api_put(endpoint: str, data: dict) -> dict:
    """PUT request to Frappe REST API."""
    url = f"{ERPNEXT_URL}{endpoint}"
    resp = requests.put(url, headers=HEADERS, json=data)
    resp.raise_for_status()
    return resp.json()


def custom_field_exists(fieldname: str) -> Optional[str]:
    """Check if a Custom Field exists for Job Applicant. Returns name or None."""
    name = f"Job Applicant-{fieldname}"
    try:
        resp = requests.get(
            f"{ERPNEXT_URL}/api/resource/Custom Field/{name}",
            headers=HEADERS,
        )
        if resp.status_code == 200:
            return name
    except Exception:
        pass
    return None


def property_setter_exists(field_name: str, prop: str) -> Optional[str]:
    """Check if a Property Setter exists. Returns name or None."""
    params = {
        "filters": json.dumps([
            ["doc_type", "=", "Job Applicant"],
            ["field_name", "=", field_name],
            ["property", "=", prop],
        ]),
        "fields": json.dumps(["name"]),
        "limit_page_length": 1,
    }
    try:
        resp = api_get("/api/resource/Property Setter", params=params)
        if resp.get("data") and len(resp["data"]) > 0:
            return resp["data"][0]["name"]
    except Exception:
        pass
    return None


def upsert_custom_field(field_def: dict):
    """Create or update a Custom Field on Job Applicant."""
    fieldname = field_def["fieldname"]
    existing = custom_field_exists(fieldname)

    payload = {
        "dt": "Job Applicant",
        "fieldname": fieldname,
        "label": field_def.get("label", ""),
        "fieldtype": field_def["fieldtype"],
        "insert_after": field_def.get("insert_after", ""),
        "module": field_def.get("module", MODULE),
    }

    # Add optional properties
    if "collapsible" in field_def:
        payload["collapsible"] = field_def["collapsible"]
    if "collapsible_depends_on" in field_def:
        payload["collapsible_depends_on"] = field_def["collapsible_depends_on"]

    if existing:
        print(f"  [UPDATE] Custom Field: {fieldname}")
        api_put(f"/api/resource/Custom Field/{existing}", payload)
    else:
        print(f"  [CREATE] Custom Field: {fieldname}")
        payload["doctype"] = "Custom Field"
        api_post("/api/resource/Custom Field", {"data": json.dumps(payload)})


def upsert_property_setter(field_name: str, prop: str, value: str, property_type: str = "Data"):
    """Create or update a Property Setter on Job Applicant."""
    existing = property_setter_exists(field_name, prop)

    payload = {
        "doc_type": "Job Applicant",
        "doctype_or_field": "DocField",
        "field_name": field_name,
        "property": prop,
        "value": value,
        "property_type": property_type,
        "module": MODULE,
    }

    if existing:
        print(f"  [UPDATE] Property Setter: {field_name}.{prop} = {value}")
        api_put(f"/api/resource/Property Setter/{existing}", payload)
    else:
        print(f"  [CREATE] Property Setter: {field_name}.{prop} = {value}")
        payload["doctype"] = "Property Setter"
        api_post("/api/resource/Property Setter", {"data": json.dumps(payload)})


def clear_cache():
    """Clear DocType cache for Job Applicant."""
    print("\n[CACHE] Clearing cache for Job Applicant...")
    try:
        api_post("/api/method/frappe.client.clear_cache", {"doctype": "Job Applicant"})
        print("  Cache cleared successfully.")
    except Exception as e:
        print(f"  Warning: Cache clear returned: {e}")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def run():
    """Main entry point — can be called via bench execute or directly."""
    print("=" * 70)
    print("CUSTOMIZE JOB APPLICANT — 4-Tab Layout Reorganization")
    print("=" * 70)
    print(f"\nTarget: {ERPNEXT_URL}")
    print(f"Module tag: {MODULE}")

    # Step 1: Create new structural Custom Fields (Tab/Section/Column Breaks)
    print("\n" + "-" * 50)
    print("STEP 1: Creating structural Custom Fields (Tabs, Sections, Columns)")
    print("-" * 50)
    for field_def in NEW_STRUCTURAL_FIELDS:
        upsert_custom_field(field_def)

    # Step 2: Apply reorder Property Setters (insert_after)
    print("\n" + "-" * 50)
    print("STEP 2: Applying field reorder Property Setters (insert_after)")
    print("-" * 50)
    for setter in REORDER_SETTERS:
        upsert_property_setter(
            field_name=setter["field_name"],
            prop=setter["property"],
            value=setter["value"],
            property_type="Data",
        )

    # Step 3: Apply flag Property Setters (bold, in_preview, in_global_search)
    print("\n" + "-" * 50)
    print("STEP 3: Applying field flag Property Setters")
    print("-" * 50)
    for setter in FLAG_SETTERS:
        upsert_property_setter(
            field_name=setter["field_name"],
            prop=setter["property"],
            value=setter["value"],
            property_type=setter.get("property_type", "Check"),
        )

    # Step 4: Hide old structural fields that are now superseded
    print("\n" + "-" * 50)
    print("STEP 4: Hiding old structural fields (replaced by new layout)")
    print("-" * 50)
    for fieldname in HIDE_OLD_STRUCTURAL:
        upsert_property_setter(
            field_name=fieldname,
            prop="hidden",
            value="1",
            property_type="Check",
        )

    # Step 5: Clear cache
    clear_cache()

    print("\n" + "=" * 70)
    print("DONE — Job Applicant form reorganized into 4-tab layout.")
    print("=" * 70)
    print("\nValidation steps:")
    print(f"  1. Open: {ERPNEXT_URL}/app/job-applicant")
    print(f"  2. Click any record — verify 4 tabs: Applicant, Experience & Education, AI Enrichment, Salary Expectation")
    print(f"  3. Verify bold fields: Full Name, Email, Status")
    print(f"  4. Verify preview panel shows: Name, Email, Status, Job Opening, Rating")


if __name__ == "__main__":
    # Load env from .env file if available
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env.erpnext_v16")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())
        # Re-read after loading
        ERPNEXT_URL = os.environ.get("ERPNEXT_URL", ERPNEXT_URL)
        API_KEY = os.environ.get("ERPNEXT_API_KEY", API_KEY)
        API_SECRET = os.environ.get("ERPNEXT_API_SECRET", API_SECRET)
        HEADERS["Authorization"] = f"token {API_KEY}:{API_SECRET}"

    if not API_KEY or not API_SECRET:
        print("ERROR: ERPNEXT_API_KEY and ERPNEXT_API_SECRET must be set.")
        print("  Set via environment variables or place in ../.env.erpnext_v16")
        sys.exit(1)

    run()
