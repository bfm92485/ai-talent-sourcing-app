#!/usr/bin/env python3
"""
Canonical Job Applicant Layout Customization Script
====================================================
Produces the approved 4-tab layout for Job Applicant DocType in ERPNext v16.
Fully idempotent: safe to run multiple times, always converges to the same state.

Layout:
  Tab 1: Applicant
    - Section: Identity (Full Name, Email, Phone | Job Opening, Designation, Source | Country | Status, Rating | LinkedIn, GitHub, Current Company | Persona Tag)
    - Section: Skills (Skills textarea)
    - Section: Resume & Materials (Link, Cover Letter, Attachment)
  Tab 2: Experience & Education
    - Section: Experience (child table)
    - Section: Education (child table)
  Tab 3: AI Enrichment
    - Section: AI Assessment (AI Score, Enrichment Status, Data TTL Expiry | AI Justification)
    - Section: Raw Data (Enrichment Extras JSON) [collapsible]
  Tab 4: Salary Expectation (standard ERPNext)

Usage:
  export ERPNEXT_URL=https://your-site.example.com
  export ERPNEXT_API_KEY=xxx
  export ERPNEXT_API_SECRET=xxx
  python3 customize_job_applicant_canonical.py
"""
import os
import sys
import json
import time
import requests

# --- Configuration ---
URL = os.environ.get("ERPNEXT_URL", "https://erpnext-v16-talent-sourcing-production.up.railway.app")
KEY = os.environ.get("ERPNEXT_API_KEY", "9c0d1ff01026ff2")
SECRET = os.environ.get("ERPNEXT_API_SECRET", "37310956c8b05f7")
HEADERS = {"Authorization": f"token {KEY}:{SECRET}", "Content-Type": "application/json"}
DT = "Job Applicant"
MODULE = "HR"

# --- Desired Custom Fields (in order) ---
# Each entry: (fieldname, fieldtype, label, insert_after, extra_props)
CUSTOM_FIELDS = [
    # Tab 1: Applicant
    ("custom_tab_applicant", "Tab Break", "Applicant", "", {}),
    ("custom_section_identity", "Section Break", "Identity", "custom_tab_applicant", {}),
    # Core fields (Full Name, Email, Phone) are already in this section via property setters
    # After designation, insert Candidate Source
    ("custom_source", "Select", "Candidate Source", "designation", {
        "options": "\nResume Upload\nAPI Search\nWeb Portal\nLinkedIn\nGitHub\nReferral",
        "description": "How this candidate was sourced"
    }),
    ("custom_referred_by", "Data", "Referred By", "custom_source", {
        "description": "Identity of the recruiter or person who referred this candidate (extracted from forwarded email)"
    }),
    # After country, insert column for Status/Rating (handled by core fields)
    # After status section, LinkedIn/GitHub/Company column
    ("custom_col_identity_2", "Column Break", "", "country", {}),
    ("custom_linkedin_url", "Data", "LinkedIn URL", "custom_col_identity_2", {
        "options": "URL", "description": "Candidate's LinkedIn profile URL"
    }),
    ("custom_github_url", "Data", "GitHub URL", "custom_linkedin_url", {
        "options": "URL", "description": "Candidate's GitHub profile URL"
    }),
    ("custom_current_company", "Data", "Current Company", "custom_github_url", {
        "description": "Candidate's current employer"
    }),
    ("custom_col_identity_3", "Column Break", "", "custom_current_company", {}),
    ("custom_persona_tag", "Data", "Persona Tag", "custom_col_identity_3", {
        "description": "The talent persona used for sourcing this candidate"
    }),
    # Skills section
    ("custom_section_skills", "Section Break", "Skills", "custom_persona_tag", {}),
    ("custom_skills_list", "Text", "Skills", "custom_section_skills", {
        "description": "Comma-separated list of skills extracted from resume or profile"
    }),
    # Resume & Materials section
    ("custom_section_resume", "Section Break", "Resume & Materials", "custom_skills_list", {}),
    # Core fields: resume_link, cover_letter, resume_attachment are here via property setters
    ("custom_col_resume_2", "Column Break", "", "resume_attachment", {}),

    # Tab 2: Experience & Education
    ("custom_tab_experience", "Tab Break", "Experience & Education", "notes", {}),
    ("custom_section_experience", "Section Break", "Experience", "custom_tab_experience", {}),
    ("custom_experience", "Table", "Experience", "custom_section_experience", {
        "options": "Candidate Experience"
    }),
    ("custom_section_education", "Section Break", "Education", "custom_experience", {}),
    ("custom_education", "Table", "Education", "custom_section_education", {
        "options": "Candidate Education"
    }),

    # Tab 3: AI Enrichment
    ("custom_tab_ai_enrichment", "Tab Break", "AI Enrichment", "custom_education", {}),
    ("custom_section_ai_assessment", "Section Break", "AI Assessment", "custom_tab_ai_enrichment", {}),
    ("custom_ai_score", "Float", "AI Score", "custom_section_ai_assessment", {
        "description": "AI-generated relevance score (0.0 - 1.0)"
    }),
    ("custom_enrichment_status", "Select", "Enrichment Status", "custom_ai_score", {
        "options": "\nPending\nIn Progress\nComplete\nFailed",
        "description": "Status of the data enrichment pipeline for this candidate"
    }),
    ("custom_data_ttl_expiry", "Date", "Data TTL Expiry", "custom_enrichment_status", {
        "description": "Date when candidate data expires per compliance policy"
    }),
    ("custom_col_ai_2", "Column Break", "", "custom_data_ttl_expiry", {}),
    ("custom_ai_justification", "Small Text", "AI Justification", "custom_col_ai_2", {
        "description": "Natural language explanation of the AI score"
    }),
    # Raw Data section (collapsible)
    ("custom_section_raw_data", "Section Break", "Raw Data", "custom_ai_justification", {
        "collapsible": 1
    }),
    ("custom_enrichment_extras", "Code", "Enrichment Extras (JSON)", "custom_section_raw_data", {
        "options": "JSON",
        "description": "Dynamic key-value pairs: certifications, volunteering, publications, etc."
    }),
]

# --- Property Setters for core fields ---
PROPERTY_SETTERS = [
    # Bold/prominent fields
    ("applicant_name", "bold", "1"),
    ("email_id", "bold", "1"),
    ("status", "bold", "1"),
    # In Global Search
    ("applicant_name", "in_global_search", "1"),
    ("email_id", "in_global_search", "1"),
    ("custom_skills_list", "in_global_search", "1"),
    # In Preview (list view)
    ("email_id", "in_preview", "1"),
    ("job_title", "in_preview", "1"),
    ("status", "in_preview", "1"),
]


def ensure_custom_field(fieldname, fieldtype, label, insert_after, extra_props):
    """Create or update a Custom Field to match desired state."""
    doc_name = f"{DT}-{fieldname}"
    
    # Check if exists
    resp = requests.get(f"{URL}/api/resource/Custom%20Field/{doc_name}", headers=HEADERS)
    
    payload = {
        "dt": DT,
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": label,
        "insert_after": insert_after,
        "module": MODULE,
        **extra_props
    }
    
    if resp.status_code == 200:
        # Update to ensure correct state
        existing = resp.json()["data"]
        needs_update = False
        update_payload = {}
        
        for key, val in payload.items():
            if key in ("dt", "fieldname", "module"):
                continue
            if str(existing.get(key, "")) != str(val):
                update_payload[key] = val
                needs_update = True
        
        if needs_update:
            resp = requests.put(f"{URL}/api/resource/Custom%20Field/{doc_name}", 
                              headers=HEADERS, json=update_payload)
            status = "UPDATED" if resp.status_code == 200 else f"UPDATE_FAILED({resp.status_code})"
        else:
            status = "OK (no change)"
    else:
        # Create
        resp = requests.post(f"{URL}/api/resource/Custom%20Field", headers=HEADERS, json=payload)
        if resp.status_code == 200:
            status = "CREATED"
        else:
            # Try without module
            payload.pop("module", None)
            resp = requests.post(f"{URL}/api/resource/Custom%20Field", headers=HEADERS, json=payload)
            status = "CREATED" if resp.status_code == 200 else f"CREATE_FAILED({resp.status_code})"
    
    print(f"  {fieldname:45s} [{fieldtype:12s}] → {status}")
    return "FAILED" not in status


def ensure_property_setter(fieldname, property_name, value):
    """Create or update a Property Setter."""
    doc_name = f"{DT}-main-{fieldname}-{property_name}"
    
    resp = requests.get(f"{URL}/api/resource/Property%20Setter/{doc_name}", headers=HEADERS)
    
    if resp.status_code == 200:
        existing = resp.json()["data"]
        if str(existing.get("value", "")) != str(value):
            resp = requests.put(f"{URL}/api/resource/Property%20Setter/{doc_name}",
                              headers=HEADERS, json={"value": value})
            return "UPDATED" if resp.status_code == 200 else "UPDATE_FAILED"
        return "OK"
    else:
        payload = {
            "name": doc_name,
            "doctype_or_field": "DocField",
            "doc_type": DT,
            "field_name": fieldname,
            "property": property_name,
            "value": value,
            "property_type": "Check" if value in ("0", "1") else "Data"
        }
        resp = requests.post(f"{URL}/api/resource/Property%20Setter", headers=HEADERS, json=payload)
        return "CREATED" if resp.status_code == 200 else "CREATE_FAILED"


def cleanup_obsolete_fields():
    """Remove structural fields that are no longer needed."""
    obsolete = [
        "custom_section_application",
        "custom_col_application_2",
    ]
    for fieldname in obsolete:
        doc_name = f"{DT}-{fieldname}"
        resp = requests.get(f"{URL}/api/resource/Custom%20Field/{doc_name}", headers=HEADERS)
        if resp.status_code == 200:
            requests.delete(f"{URL}/api/resource/Custom%20Field/{doc_name}", headers=HEADERS)
            print(f"  Deleted obsolete: {fieldname}")


def main():
    print(f"{'='*70}")
    print(f"Job Applicant Layout Customization (Canonical)")
    print(f"Target: {URL}")
    print(f"{'='*70}")
    
    # Step 1: Clean up obsolete fields
    print("\n[1/4] Cleaning up obsolete fields...")
    cleanup_obsolete_fields()
    
    # Step 2: Ensure all custom fields exist with correct state
    print(f"\n[2/4] Ensuring {len(CUSTOM_FIELDS)} custom fields...")
    success_count = 0
    for fieldname, fieldtype, label, insert_after, extra in CUSTOM_FIELDS:
        if ensure_custom_field(fieldname, fieldtype, label, insert_after, extra):
            success_count += 1
    print(f"  → {success_count}/{len(CUSTOM_FIELDS)} fields OK")
    
    # Step 3: Apply property setters
    print(f"\n[3/4] Applying {len(PROPERTY_SETTERS)} property setters...")
    for fieldname, prop, value in PROPERTY_SETTERS:
        status = ensure_property_setter(fieldname, prop, value)
        print(f"  {fieldname:30s} {prop:20s} = {value:5s} → {status}")
    
    # Step 4: Clear cache
    print("\n[4/4] Clearing cache...")
    resp = requests.post(f"{URL}/api/method/frappe.sessions.clear", headers=HEADERS)
    print(f"  Cache cleared: {resp.status_code}")
    
    print(f"\n{'='*70}")
    print("DONE. Layout applied successfully.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
