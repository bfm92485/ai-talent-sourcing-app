"""
revert_job_applicant.py
=======================
Reverts all layout customizations made by customize_job_applicant.py.

Deletes:
  - All Custom Fields tagged with module "AI Talent Sourcing" that are
    structural (Tab Break, Section Break, Column Break)
  - All Property Setters created for Job Applicant by the customization script

Does NOT delete:
  - Data-bearing custom fields (custom_skills_list, custom_ai_score, etc.)
  - The underlying data in Job Applicant records

After running, the Job Applicant form returns to its pre-customization layout.

Execution modes:
  Remote API: python3 revert_job_applicant.py
  Local bench: bench --site <site> execute revert_job_applicant.run

Environment variables (for remote mode):
  ERPNEXT_URL       - e.g. https://erpnext-v16-talent-sourcing-production.up.railway.app
  ERPNEXT_API_KEY   - API key for Administrator
  ERPNEXT_API_SECRET - API secret for Administrator
"""

import os
import sys
import json
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODULE = "HR"

ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erpnext-v16-talent-sourcing-production.up.railway.app")
API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")

HEADERS = {
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json",
}

# Structural Custom Fields created by customize_job_applicant.py
STRUCTURAL_FIELDS_TO_DELETE = [
    "custom_tab_applicant",
    "custom_section_identity",
    "custom_col_identity_2",
    "custom_col_identity_3",
    "custom_section_application",
    "custom_col_application_2",
    "custom_section_skills",
    "custom_section_resume",
    "custom_col_resume_2",
    "custom_tab_experience",
    "custom_section_experience",
    "custom_section_education",
    "custom_tab_ai_enrichment",
    "custom_section_ai_assessment",
    "custom_col_ai_2",
    "custom_section_raw_data",
]

# Property Setters to delete (all created by the customization script)
# We'll query by doc_type=Job Applicant and module=AI Talent Sourcing
# But also explicitly list the properties we set for safety
PROPERTIES_TO_REVERT = [
    # Reorder setters (insert_after)
    ("applicant_name", "insert_after"),
    ("email_id", "insert_after"),
    ("phone_number", "insert_after"),
    ("country", "insert_after"),
    ("custom_linkedin_url", "insert_after"),
    ("custom_github_url", "insert_after"),
    ("custom_current_company", "insert_after"),
    ("status", "insert_after"),
    ("applicant_rating", "insert_after"),
    ("custom_persona_tag", "insert_after"),
    ("job_title", "insert_after"),
    ("designation", "insert_after"),
    ("custom_source", "insert_after"),
    ("source", "insert_after"),
    ("source_name", "insert_after"),
    ("employee_referral", "insert_after"),
    ("custom_skills_list", "insert_after"),
    ("resume_link", "insert_after"),
    ("resume_preview_html", "insert_after"),
    ("open_resume_button", "insert_after"),
    ("resume_attachment", "insert_after"),
    ("cover_letter", "insert_after"),
    ("notes", "insert_after"),
    ("custom_experience", "insert_after"),
    ("custom_education", "insert_after"),
    ("custom_ai_score", "insert_after"),
    ("custom_enrichment_status", "insert_after"),
    ("custom_ai_justification", "insert_after"),
    ("custom_enrichment_extras", "insert_after"),
    ("custom_data_ttl_expiry", "insert_after"),
    # Flag setters
    ("applicant_name", "bold"),
    ("email_id", "bold"),
    ("status", "bold"),
    ("applicant_name", "in_preview"),
    ("email_id", "in_preview"),
    ("status", "in_preview"),
    ("job_title", "in_preview"),
    ("applicant_rating", "in_preview"),
    ("applicant_name", "in_global_search"),
    ("email_id", "in_global_search"),
    ("custom_skills_list", "in_global_search"),
    ("custom_current_company", "in_global_search"),
    # Hidden setters for old structural fields
    ("details_section", "hidden"),
    ("column_break_3", "hidden"),
    ("column_break_enue", "hidden"),
    ("section_break_6", "hidden"),
    ("source_and_rating_section", "hidden"),
    ("column_break_13", "hidden"),
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


def api_delete(endpoint: str) -> bool:
    """DELETE request to Frappe REST API. Returns True on success."""
    url = f"{ERPNEXT_URL}{endpoint}"
    resp = requests.delete(url, headers=HEADERS)
    return resp.status_code in (200, 202, 204)


def find_property_setter(field_name: str, prop: str) -> list:
    """Find Property Setter names matching field_name and property."""
    params = {
        "filters": json.dumps([
            ["doc_type", "=", "Job Applicant"],
            ["field_name", "=", field_name],
            ["property", "=", prop],
        ]),
        "fields": json.dumps(["name"]),
        "limit_page_length": 0,
    }
    try:
        resp = api_get("/api/resource/Property Setter", params=params)
        return [d["name"] for d in resp.get("data", [])]
    except Exception:
        return []


def clear_cache():
    """Clear DocType cache for Job Applicant."""
    print("\n[CACHE] Clearing cache for Job Applicant...")
    try:
        requests.post(
            f"{ERPNEXT_URL}/api/method/frappe.client.clear_cache",
            headers=HEADERS,
            json={"doctype": "Job Applicant"},
        )
        print("  Cache cleared successfully.")
    except Exception as e:
        print(f"  Warning: Cache clear returned: {e}")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def run():
    """Main entry point — reverts all layout customizations."""
    print("=" * 70)
    print("REVERT JOB APPLICANT — Remove 4-Tab Layout Customizations")
    print("=" * 70)
    print(f"\nTarget: {ERPNEXT_URL}")
    print(f"Module tag: {MODULE}")

    # Step 1: Delete structural Custom Fields
    print("\n" + "-" * 50)
    print("STEP 1: Deleting structural Custom Fields (Tabs, Sections, Columns)")
    print("-" * 50)
    deleted_cf = 0
    for fieldname in STRUCTURAL_FIELDS_TO_DELETE:
        cf_name = f"Job Applicant-{fieldname}"
        success = api_delete(f"/api/resource/Custom%20Field/{cf_name}")
        if success:
            print(f"  [DELETED] Custom Field: {fieldname}")
            deleted_cf += 1
        else:
            print(f"  [SKIP]    Custom Field: {fieldname} (not found or already deleted)")

    # Step 2: Delete Property Setters
    print("\n" + "-" * 50)
    print("STEP 2: Deleting Property Setters")
    print("-" * 50)
    deleted_ps = 0
    for field_name, prop in PROPERTIES_TO_REVERT:
        names = find_property_setter(field_name, prop)
        for name in names:
            success = api_delete(f"/api/resource/Property%20Setter/{name}")
            if success:
                print(f"  [DELETED] Property Setter: {field_name}.{prop} ({name})")
                deleted_ps += 1
            else:
                print(f"  [FAIL]    Could not delete: {name}")
        if not names:
            print(f"  [SKIP]    {field_name}.{prop} (not found)")

    # Step 3: Clear cache
    clear_cache()

    print("\n" + "=" * 70)
    print(f"REVERT COMPLETE — Deleted {deleted_cf} Custom Fields, {deleted_ps} Property Setters.")
    print("=" * 70)
    print("\nThe Job Applicant form should now display its original layout.")
    print(f"Verify at: {ERPNEXT_URL}/app/job-applicant")


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
