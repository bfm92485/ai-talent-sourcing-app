#!/usr/bin/env python3
"""
AI Talent Sourcing Pipeline
Orchestrates: PDF/text → BAML extraction → ERPNext Job Applicant creation

Flows:
  A) Resume PDF → extract text → BAML parse → create Job Applicant
  B) LinkedIn/API profile text → BAML parse → create Job Applicant
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

# Add project root to path for baml_client import
sys.path.insert(0, str(Path(__file__).parent))

from baml_client import b
from baml_client.types import ParsedResume, WorkExperience, Education
from dotenv import load_dotenv

# Load env files
load_dotenv("/home/ubuntu/.env.erpnext_v16")


class ERPNextClient:
    """REST API client for ERPNext v16."""

    def __init__(self):
        self.base_url = os.getenv("ERPNEXT_URL")
        self.api_key = os.getenv("ERPNEXT_API_KEY")
        self.api_secret = os.getenv("ERPNEXT_API_SECRET")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.api_key}:{self.api_secret}",
            "Content-Type": "application/json",
        })

    def create_job_applicant(self, parsed: ParsedResume, source: str = "Resume Upload") -> dict:
        """Create a Job Applicant record from parsed resume data."""
        # Build experience child table rows
        experience_rows = []
        for exp in (parsed.experience or []):
            row = {
                "company": exp.company,
                "title": exp.title,
                "is_current": 1 if exp.is_current else 0,
            }
            if exp.start_date:
                row["start_date"] = self._normalize_date(exp.start_date)
            if exp.end_date:
                row["end_date"] = self._normalize_date(exp.end_date)
            if exp.responsibilities:
                row["responsibilities"] = exp.responsibilities
            experience_rows.append(row)

        # Build education child table rows
        education_rows = []
        for edu in (parsed.education or []):
            row = {"institution": edu.institution}
            if edu.degree:
                row["degree"] = edu.degree
            if edu.field_of_study:
                row["field_of_study"] = edu.field_of_study
            if edu.start_date:
                row["start_date"] = self._normalize_date(edu.start_date)
            if edu.end_date:
                row["end_date"] = self._normalize_date(edu.end_date)
            education_rows.append(row)

        # Build the Job Applicant payload
        payload = {
            "applicant_name": parsed.applicant_name,
            "email_id": parsed.email_id or f"{parsed.applicant_name.lower().replace(' ', '.')}@placeholder.com",
            "status": "Open",
            "custom_current_company": parsed.current_company or "",
            "custom_skills_list": ", ".join(parsed.skills) if parsed.skills else "",
            "custom_source": source,
            "custom_linkedin_url": self._ensure_url_prefix(parsed.linkedin_url),
            "custom_github_url": self._ensure_url_prefix(parsed.github_url),
            "custom_enrichment_status": "Complete",
            "custom_data_ttl_expiry": (date.today() + timedelta(days=90)).isoformat(),
            "custom_experience": experience_rows,
            "custom_education": education_rows,
        }

        # Add designation if available (it's a Link field, so create if needed)
        if parsed.designation:
            self._ensure_designation_exists(parsed.designation)
            payload["designation"] = parsed.designation

        # Add phone if available
        if parsed.phone:
            payload["phone"] = parsed.phone

        # Store any extra structured data in enrichment_extras
        extras = {}
        if parsed.summary:
            extras["summary"] = parsed.summary
        if extras:
            payload["custom_enrichment_extras"] = json.dumps(extras)

        resp = self.session.post(
            f"{self.base_url}/api/resource/Job Applicant",
            json=payload,
        )

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            print(f"  ✓ Created Job Applicant: {data.get('name')} ({parsed.applicant_name})")
            return data
        else:
            print(f"  ✗ Error creating Job Applicant: {resp.status_code}")
            print(f"    {resp.text[:500]}")
            return {"error": resp.text}

    def _ensure_url_prefix(self, url: Optional[str]) -> str:
        """Ensure URL has https:// prefix for ERPNext URL field validation."""
        if not url:
            return ""
        url = url.strip()
        if url and not url.startswith("http"):
            return f"https://{url}"
        return url

    def _ensure_designation_exists(self, designation: str):
        """Create a Designation record if it doesn't already exist."""
        resp = self.session.get(
            f"{self.base_url}/api/resource/Designation/{designation}"
        )
        if resp.status_code == 200:
            return  # Already exists
        # Create it - field is 'designation_name' in v16
        resp = self.session.post(
            f"{self.base_url}/api/resource/Designation",
            json={"designation_name": designation},
        )
        if resp.status_code == 200:
            print(f"    (Created Designation: {designation})")
        # If it fails, we'll let the main call handle the error

    def _normalize_date(self, date_str: str) -> str:
        """Normalize date string to YYYY-MM-DD format."""
        if not date_str:
            return ""
        # Handle YYYY-MM format
        if len(date_str) == 7 and "-" in date_str:
            return f"{date_str}-01"
        # Handle YYYY format
        if len(date_str) == 4 and date_str.isdigit():
            return f"{date_str}-01-01"
        return date_str


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    import fitz  # pymupdf
    doc = fitz.open(pdf_path)
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


async def flow_a_resume(pdf_path: str, source: str = "Resume Upload") -> dict:
    """
    Flow A: Resume PDF → text extraction → BAML parse → ERPNext Job Applicant
    """
    print(f"\n{'='*60}")
    print(f"Flow A: Resume → ERPNext")
    print(f"{'='*60}")

    # Step 1: Extract text from PDF
    print(f"\n[1/3] Extracting text from: {pdf_path}")
    if pdf_path.endswith(".pdf"):
        resume_text = extract_text_from_pdf(pdf_path)
    else:
        # Assume it's already text
        resume_text = Path(pdf_path).read_text()
    print(f"  Extracted {len(resume_text)} characters")

    # Step 2: BAML extraction
    print(f"\n[2/3] Running BAML ExtractResume...")
    parsed = await b.ExtractResume(resume_text=resume_text)
    print(f"  Name: {parsed.applicant_name}")
    print(f"  Email: {parsed.email_id}")
    print(f"  Skills: {len(parsed.skills)} found")
    print(f"  Experience: {len(parsed.experience)} roles")
    print(f"  Education: {len(parsed.education)} entries")

    # Step 3: Create ERPNext record
    print(f"\n[3/3] Creating ERPNext Job Applicant...")
    client = ERPNextClient()
    result = client.create_job_applicant(parsed, source=source)

    return result


async def flow_b_profile(profile_text: str, source: str = "API Search") -> dict:
    """
    Flow B: LinkedIn/API profile text → BAML parse → ERPNext Job Applicant
    """
    print(f"\n{'='*60}")
    print(f"Flow B: Profile → ERPNext")
    print(f"{'='*60}")

    # Step 1: BAML extraction (lighter model)
    print(f"\n[1/2] Running BAML ExtractProfile...")
    parsed = await b.ExtractProfile(profile_text=profile_text)
    print(f"  Name: {parsed.applicant_name}")
    print(f"  Email: {parsed.email_id}")
    print(f"  Skills: {len(parsed.skills)} found")
    print(f"  Experience: {len(parsed.experience)} roles")

    # Step 2: Create ERPNext record
    print(f"\n[2/2] Creating ERPNext Job Applicant...")
    client = ERPNextClient()
    result = client.create_job_applicant(parsed, source=source)

    return result


async def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python pipeline.py resume <path_to_pdf_or_txt>")
        print("  python pipeline.py profile <path_to_profile_txt>")
        print("  python pipeline.py test")
        sys.exit(1)

    command = sys.argv[1]

    if command == "resume" and len(sys.argv) >= 3:
        result = await flow_a_resume(sys.argv[2])
        print(f"\nResult: {json.dumps(result, indent=2, default=str)[:500]}")

    elif command == "profile" and len(sys.argv) >= 3:
        profile_text = Path(sys.argv[2]).read_text()
        result = await flow_b_profile(profile_text)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)[:500]}")

    elif command == "test":
        # Run with sample data
        sample_resume = """
John Doe
john.doe@example.com | (512) 555-0199
https://linkedin.com/in/johndoe | https://github.com/johndoe

PROFESSIONAL SUMMARY
Experienced DevOps engineer with 6 years building cloud infrastructure
and CI/CD pipelines for high-growth startups.

EXPERIENCE
Senior DevOps Engineer | CloudScale Inc. | March 2021 - Present
- Managed Kubernetes clusters serving 50M requests/day
- Implemented GitOps workflow with ArgoCD and Terraform
- Reduced infrastructure costs by 35% through right-sizing

DevOps Engineer | DataFlow Systems | Jan 2019 - Feb 2021
- Built CI/CD pipelines using Jenkins and GitHub Actions
- Automated infrastructure provisioning with Ansible and Terraform
- Maintained 99.9% uptime SLA for production services

Junior Systems Admin | TechStart LLC | Jun 2017 - Dec 2018
- Managed Linux servers and networking infrastructure
- Implemented monitoring with Prometheus and Grafana

EDUCATION
B.S. Computer Science | University of Texas at Austin | 2013 - 2017

SKILLS
Kubernetes, Docker, Terraform, AWS, GCP, Azure, Jenkins, ArgoCD,
Prometheus, Grafana, Python, Bash, Go, Linux, Networking, CI/CD,
Infrastructure as Code, GitOps, Ansible, Helm
"""
        result = await flow_a_resume_from_text(sample_resume)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)[:500]}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


async def flow_a_resume_from_text(resume_text: str, source: str = "Resume Upload") -> dict:
    """Flow A variant that takes raw text instead of a file path."""
    print(f"\n{'='*60}")
    print(f"Flow A: Resume Text → ERPNext")
    print(f"{'='*60}")

    print(f"\n[1/2] Running BAML ExtractResume...")
    parsed = await b.ExtractResume(resume_text=resume_text)
    print(f"  Name: {parsed.applicant_name}")
    print(f"  Email: {parsed.email_id}")
    print(f"  Skills: {len(parsed.skills)} found")
    print(f"  Experience: {len(parsed.experience)} roles")
    print(f"  Education: {len(parsed.education)} entries")

    print(f"\n[2/2] Creating ERPNext Job Applicant...")
    client = ERPNextClient()
    result = client.create_job_applicant(parsed, source=source)

    return result


if __name__ == "__main__":
    asyncio.run(main())
