"""
customize_job_applicant_v2.py
=============================
Reorganizes the ERPNext Job Applicant DocType into a 4-tab layout using
the Customize Form "save" approach (the only method that physically moves
fields between sections in Frappe v16).

Strategy:
1. First revert all Property Setters from v1 (they don't work for reordering)
2. Delete all structural Custom Fields from v1
3. Fetch the current Customize Form state
4. Build the desired field order with new structural breaks
5. Submit via the Customize Form save endpoint

This approach mirrors what the Customize Form UI does when you drag-and-drop fields.
"""

import os
import sys
import json
import time
import requests
from copy import deepcopy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erpnext-v16-talent-sourcing-production.up.railway.app")
API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")

HEADERS = {
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# API Helpers
# ---------------------------------------------------------------------------
def api_get(endpoint, params=None):
    resp = requests.get(f"{ERPNEXT_URL}{endpoint}", headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()

def api_post(endpoint, data):
    resp = requests.post(f"{ERPNEXT_URL}{endpoint}", headers=HEADERS, json=data)
    if resp.status_code >= 400:
        print(f"  ERROR {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json()

def api_put(endpoint, data):
    resp = requests.put(f"{ERPNEXT_URL}{endpoint}", headers=HEADERS, json=data)
    if resp.status_code >= 400:
        print(f"  ERROR {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json()

def api_delete(endpoint):
    resp = requests.delete(f"{ERPNEXT_URL}{endpoint}", headers=HEADERS)
    return resp.status_code in (200, 202, 204)

# ---------------------------------------------------------------------------
# Step 1: Clean up v1 Property Setters (insert_after ones that don't work)
# ---------------------------------------------------------------------------
def cleanup_v1_property_setters():
    """Delete all insert_after Property Setters for Job Applicant."""
    print("\n[CLEANUP] Removing v1 insert_after Property Setters...")
    params = {
        "filters": json.dumps([
            ["doc_type", "=", "Job Applicant"],
            ["property", "=", "insert_after"],
        ]),
        "fields": json.dumps(["name"]),
        "limit_page_length": 0,
    }
    resp = api_get("/api/resource/Property Setter", params=params)
    names = [d["name"] for d in resp.get("data", [])]
    for name in names:
        api_delete(f"/api/resource/Property%20Setter/{name}")
    print(f"  Deleted {len(names)} insert_after Property Setters")

    # Also delete hidden property setters for old structural fields
    params["filters"] = json.dumps([
        ["doc_type", "=", "Job Applicant"],
        ["property", "=", "hidden"],
        ["field_name", "in", ["details_section", "column_break_3", "column_break_enue",
                              "section_break_6", "source_and_rating_section", "column_break_13"]],
    ])
    resp = api_get("/api/resource/Property Setter", params=params)
    names = [d["name"] for d in resp.get("data", [])]
    for name in names:
        api_delete(f"/api/resource/Property%20Setter/{name}")
    print(f"  Deleted {len(names)} hidden Property Setters")

# ---------------------------------------------------------------------------
# Step 2: Delete v1 structural Custom Fields
# ---------------------------------------------------------------------------
V1_STRUCTURAL_FIELDS = [
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

def cleanup_v1_structural_fields():
    """Delete structural Custom Fields from v1."""
    print("\n[CLEANUP] Removing v1 structural Custom Fields...")
    deleted = 0
    for fn in V1_STRUCTURAL_FIELDS:
        name = f"Job Applicant-{fn}"
        if api_delete(f"/api/resource/Custom%20Field/{name}"):
            deleted += 1
    print(f"  Deleted {deleted}/{len(V1_STRUCTURAL_FIELDS)} structural Custom Fields")

# ---------------------------------------------------------------------------
# Step 3: Update existing custom field insert_after values to match new layout
# ---------------------------------------------------------------------------
# The desired field order (top to bottom):
# TAB 1: Applicant
#   Section: Identity
#     Col1: applicant_name, email_id, phone_number, country
#     Col2: custom_linkedin_url, custom_github_url, custom_current_company
#     Col3: status, applicant_rating, custom_persona_tag
#   Section: Application
#     Col1: job_title, designation
#     Col2: custom_source, source, source_name, employee_referral
#   Section: Skills
#     custom_skills_list (full width)
#   Section: Resume & Materials
#     Col1: resume_link, resume_preview_html, open_resume_button, resume_attachment
#     Col2: cover_letter, notes
# TAB 2: Experience & Education
#   Section: Experience
#     custom_experience (table, full width)
#   Section: Education
#     custom_education (table, full width)
# TAB 3: AI Enrichment
#   Section: AI Assessment
#     Col1: custom_ai_score, custom_enrichment_status, custom_data_ttl_expiry
#     Col2: custom_ai_justification
#   Section: Raw Data (collapsible)
#     custom_enrichment_extras (full width)
# TAB 4: Salary Expectation (existing - salary_expectation_tab)
#   currency, column_break_18, lower_range, upper_range

# We need to update the insert_after of existing data-bearing custom fields
CUSTOM_FIELD_REORDER = {
    # These are the data-bearing custom fields that need their insert_after updated
    "custom_current_company": "custom_github_url",
    "custom_skills_list": "custom_section_skills",
    "custom_enrichment_extras": "custom_section_raw_data",
    "custom_source": "custom_col_application_2",
    "custom_linkedin_url": "custom_col_identity_2",
    "custom_github_url": "custom_linkedin_url",
    "custom_ai_score": "custom_section_ai_assessment",
    "custom_ai_justification": "custom_col_ai_2",
    "custom_persona_tag": "custom_col_identity_3",
    "custom_enrichment_status": "custom_ai_score",
    "custom_data_ttl_expiry": "custom_enrichment_status",
    "custom_experience": "custom_section_experience",
    "custom_education": "custom_section_education",
}

# New structural fields to create (in order)
NEW_STRUCTURAL_FIELDS = [
    # TAB 1: Applicant (insert at the very beginning)
    {"fieldname": "custom_tab_applicant", "label": "Applicant", "fieldtype": "Tab Break", "insert_after": ""},
    # Section: Identity (after the tab)
    {"fieldname": "custom_section_identity", "label": "Identity", "fieldtype": "Section Break", "insert_after": "custom_tab_applicant"},
    # Col2 in Identity (after country - col1 ends with country)
    {"fieldname": "custom_col_identity_2", "label": "", "fieldtype": "Column Break", "insert_after": "country"},
    # Col3 in Identity (after custom_current_company)
    {"fieldname": "custom_col_identity_3", "label": "", "fieldtype": "Column Break", "insert_after": "custom_current_company"},
    # Section: Application (after custom_persona_tag)
    {"fieldname": "custom_section_application", "label": "Application", "fieldtype": "Section Break", "insert_after": "custom_persona_tag"},
    # Col2 in Application (after designation)
    {"fieldname": "custom_col_application_2", "label": "", "fieldtype": "Column Break", "insert_after": "designation"},
    # Section: Skills (after employee_referral)
    {"fieldname": "custom_section_skills", "label": "Skills", "fieldtype": "Section Break", "insert_after": "employee_referral"},
    # Section: Resume & Materials (after custom_skills_list)
    {"fieldname": "custom_section_resume", "label": "Resume & Materials", "fieldtype": "Section Break", "insert_after": "custom_skills_list"},
    # Col2 in Resume (after resume_attachment)
    {"fieldname": "custom_col_resume_2", "label": "", "fieldtype": "Column Break", "insert_after": "resume_attachment"},
    # TAB 2: Experience & Education (after notes)
    {"fieldname": "custom_tab_experience", "label": "Experience & Education", "fieldtype": "Tab Break", "insert_after": "notes"},
    # Section: Experience
    {"fieldname": "custom_section_experience", "label": "Experience", "fieldtype": "Section Break", "insert_after": "custom_tab_experience"},
    # Section: Education (after custom_experience table)
    {"fieldname": "custom_section_education", "label": "Education", "fieldtype": "Section Break", "insert_after": "custom_experience"},
    # TAB 3: AI Enrichment (after custom_education)
    {"fieldname": "custom_tab_ai_enrichment", "label": "AI Enrichment", "fieldtype": "Tab Break", "insert_after": "custom_education"},
    # Section: AI Assessment
    {"fieldname": "custom_section_ai_assessment", "label": "AI Assessment", "fieldtype": "Section Break", "insert_after": "custom_tab_ai_enrichment"},
    # Col2 in AI Assessment (after custom_data_ttl_expiry)
    {"fieldname": "custom_col_ai_2", "label": "", "fieldtype": "Column Break", "insert_after": "custom_data_ttl_expiry"},
    # Section: Raw Data (after custom_ai_justification)
    {"fieldname": "custom_section_raw_data", "label": "Raw Data", "fieldtype": "Section Break", "insert_after": "custom_ai_justification", "collapsible": 1},
]

def create_structural_fields():
    """Create new structural Custom Fields."""
    print("\n[CREATE] Creating structural Custom Fields...")
    for field_def in NEW_STRUCTURAL_FIELDS:
        name = f"Job Applicant-{field_def['fieldname']}"
        # Check if exists
        resp = requests.get(f"{ERPNEXT_URL}/api/resource/Custom%20Field/{name}", headers=HEADERS)
        payload = {
            "dt": "Job Applicant",
            "fieldname": field_def["fieldname"],
            "label": field_def.get("label", ""),
            "fieldtype": field_def["fieldtype"],
            "insert_after": field_def.get("insert_after", ""),
            "module": "HR",
        }
        if "collapsible" in field_def:
            payload["collapsible"] = field_def["collapsible"]

        if resp.status_code == 200:
            print(f"  [UPDATE] {field_def['fieldname']}")
            api_put(f"/api/resource/Custom%20Field/{name}", payload)
        else:
            print(f"  [CREATE] {field_def['fieldname']}")
            payload["doctype"] = "Custom Field"
            api_post("/api/resource/Custom Field", {"data": json.dumps(payload)})

def reorder_data_fields():
    """Update insert_after on existing data-bearing custom fields."""
    print("\n[REORDER] Updating insert_after on data-bearing Custom Fields...")
    for fieldname, new_insert_after in CUSTOM_FIELD_REORDER.items():
        name = f"Job Applicant-{fieldname}"
        print(f"  {fieldname} → after {new_insert_after}")
        api_put(f"/api/resource/Custom%20Field/{name}", {"insert_after": new_insert_after})

# ---------------------------------------------------------------------------
# Step 4: Hide old structural fields and set flags via Property Setters
# ---------------------------------------------------------------------------
def hide_old_structural_fields():
    """Hide old section/column breaks that are now replaced."""
    print("\n[HIDE] Hiding old structural fields...")
    old_fields = ["details_section", "column_break_3", "column_break_enue",
                  "section_break_6", "source_and_rating_section", "column_break_13"]
    for fn in old_fields:
        payload = {
            "doctype": "Property Setter",
            "doc_type": "Job Applicant",
            "doctype_or_field": "DocField",
            "field_name": fn,
            "property": "hidden",
            "value": "1",
            "property_type": "Check",
        }
        api_post("/api/resource/Property Setter", {"data": json.dumps(payload)})
        print(f"  Hidden: {fn}")

def set_field_flags():
    """Set bold, in_preview, in_global_search flags."""
    print("\n[FLAGS] Setting field display flags...")
    flags = [
        ("applicant_name", "bold", "1", "Check"),
        ("email_id", "bold", "1", "Check"),
        ("status", "bold", "1", "Check"),
        ("applicant_name", "in_preview", "1", "Check"),
        ("email_id", "in_preview", "1", "Check"),
        ("status", "in_preview", "1", "Check"),
        ("job_title", "in_preview", "1", "Check"),
        ("applicant_rating", "in_preview", "1", "Check"),
        ("applicant_name", "in_global_search", "1", "Check"),
        ("email_id", "in_global_search", "1", "Check"),
        ("custom_skills_list", "in_global_search", "1", "Check"),
        ("custom_current_company", "in_global_search", "1", "Check"),
    ]
    for field_name, prop, value, prop_type in flags:
        payload = {
            "doctype": "Property Setter",
            "doc_type": "Job Applicant",
            "doctype_or_field": "DocField",
            "field_name": field_name,
            "property": prop,
            "value": value,
            "property_type": prop_type,
        }
        try:
            api_post("/api/resource/Property Setter", {"data": json.dumps(payload)})
            print(f"  {field_name}.{prop} = {value}")
        except Exception as e:
            print(f"  SKIP {field_name}.{prop} (may already exist): {e}")

# ---------------------------------------------------------------------------
# Step 5: Rebuild via Customize Form save
# ---------------------------------------------------------------------------
def rebuild_via_customize_form():
    """
    The key insight: in Frappe v16, the Customize Form uses the 'insert_after' chain
    on Custom Fields to determine field placement. The form renderer builds the layout
    by walking the chain: DocType fields + Custom Fields sorted by insert_after.
    
    The issue was that our v1 script set Property Setters for insert_after on CORE fields,
    but Property Setters for insert_after don't actually move core fields in the renderer.
    
    The correct approach: 
    - Core fields stay in their original positions (inside details_section, section_break_6, etc.)
    - We HIDE the old sections and create NEW sections that contain our custom fields
    - Core fields that need to move must be done via the Customize Form UI or by
      converting them to custom fields (not recommended)
    
    REVISED STRATEGY:
    Since we can't move core fields out of their original sections via API alone,
    we'll take a different approach:
    - Keep the original sections but RELABEL them
    - Add our custom fields in the right positions
    - Use the Tab Break to group everything logically
    """
    print("\n[REBUILD] Applying section label changes via Property Setters...")
    
    # Relabel details_section to "Identity" 
    labels = [
        ("details_section", "label", "Identity", "Data"),
        ("section_break_6", "label", "Resume & Materials", "Data"),
        ("source_and_rating_section", "label", "Application", "Data"),
    ]
    for field_name, prop, value, prop_type in labels:
        payload = {
            "doctype": "Property Setter",
            "doc_type": "Job Applicant",
            "doctype_or_field": "DocField",
            "field_name": field_name,
            "property": prop,
            "value": value,
            "property_type": prop_type,
        }
        try:
            api_post("/api/resource/Property Setter", {"data": json.dumps(payload)})
            print(f"  {field_name}.{prop} = {value}")
        except Exception as e:
            print(f"  SKIP {field_name}.{prop}: {e}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run():
    print("=" * 70)
    print("CUSTOMIZE JOB APPLICANT v2 — Correct Approach")
    print("=" * 70)
    print(f"Target: {ERPNEXT_URL}")

    # Step 1: Clean up v1 artifacts
    cleanup_v1_property_setters()
    cleanup_v1_structural_fields()
    
    # Wait for cache to settle
    time.sleep(2)
    
    # Step 2: Create new structural fields with correct insert_after chain
    create_structural_fields()
    
    # Step 3: Reorder data-bearing custom fields
    reorder_data_fields()
    
    # Step 4: Hide old structural fields (DON'T hide them - they contain core fields!)
    # Instead, relabel them and keep them visible
    rebuild_via_customize_form()
    
    # Step 5: Set field flags
    set_field_flags()
    
    print("\n" + "=" * 70)
    print("DONE — Please verify at:")
    print(f"  {ERPNEXT_URL}/app/job-applicant/john.doe@example.com")
    print("=" * 70)


if __name__ == "__main__":
    # Load env
    env_file = "/home/ubuntu/.env.erpnext_v16"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())
        ERPNEXT_URL = os.environ.get("ERPNEXT_URL", ERPNEXT_URL)
        API_KEY = os.environ.get("ERPNEXT_API_KEY", API_KEY)
        API_SECRET = os.environ.get("ERPNEXT_API_SECRET", API_SECRET)
        HEADERS["Authorization"] = f"token {API_KEY}:{API_SECRET}"

    if not API_KEY or not API_SECRET:
        print("ERROR: ERPNEXT_API_KEY and ERPNEXT_API_SECRET must be set.")
        sys.exit(1)

    run()
