// AI Talent Sourcing - Resume Parser
// Extracts structured candidate data from resume text, matching ERPNext schema

class WorkExperience {
  company string
  title string
  start_date string? @description("ISO date YYYY-MM-DD or YYYY-MM format")
  end_date string? @description("ISO date YYYY-MM-DD or YYYY-MM format, null if current")
  is_current bool
  responsibilities string? @description("Key responsibilities, comma-separated")
}

class Education {
  institution string
  degree string?
  field_of_study string?
  start_date string? @description("ISO date YYYY-MM-DD or YYYY-MM format")
  end_date string? @description("ISO date YYYY-MM-DD or YYYY-MM format")
}

class ParsedResume {
  applicant_name string @description("Full name of the candidate")
  email_id string? @description("Primary email address")
  phone string? @description("Primary phone number")
  current_company string? @description("Most recent employer")
  designation string? @description("Most recent job title")
  skills string[] @description("List of technical and soft skills")
  experience WorkExperience[] @description("Work history, most recent first")
  education Education[] @description("Education history, most recent first")
  linkedin_url string? @description("LinkedIn profile URL if found")
  github_url string? @description("GitHub profile URL if found")
  summary string? @description("Brief professional summary, 1-2 sentences")
}

// Main resume extraction function
function ExtractResume(resume_text: string) -> ParsedResume {
  client ResumeParser
  prompt #"
    You are an expert resume parser for a talent sourcing system.
    Extract structured data from the following resume text.

    Rules:
    - Extract ALL skills mentioned (technical, tools, frameworks, soft skills)
    - For dates, use ISO format (YYYY-MM-DD or YYYY-MM). If only year given, use YYYY-01-01
    - Mark the most recent job as is_current=true if no end date is mentioned
    - current_company should be the company from the most recent/current role
    - designation should be the title from the most recent/current role
    - If LinkedIn or GitHub URLs are present in the text, extract them
    - Keep responsibilities concise (key bullet points, comma-separated)

    Resume text:
    ---
    {{ resume_text }}
    ---

    {{ ctx.output_format }}
  "#
}

// Lighter extraction for LinkedIn/API profiles (less text, more structured)
function ExtractProfile(profile_text: string) -> ParsedResume {
  client GeminiFlashLite
  prompt #"
    Extract structured candidate data from this profile information.
    This may come from LinkedIn, GitHub, or another platform.

    Rules:
    - Extract all available fields
    - For missing fields, return null
    - Skills should include technologies, tools, and domain expertise
    - Dates in ISO format (YYYY-MM-DD or YYYY-MM)

    Profile:
    ---
    {{ profile_text }}
    ---

    {{ ctx.output_format }}
  "#
}

// Test with a sample resume
test sample_resume {
  functions [ExtractResume]
  args {
    resume_text #"
      Jane Smith
      jane.smith@email.com | (555) 123-4567
      linkedin.com/in/janesmith | github.com/janesmith

      SUMMARY
      Senior software engineer with 8 years of experience in full-stack development
      and machine learning systems.

      EXPERIENCE
      Senior ML Engineer | TechCorp Inc. | Jan 2022 - Present
      - Led development of recommendation engine serving 10M users
      - Architected real-time inference pipeline using Kubernetes and Ray
      - Mentored team of 4 junior engineers

      Software Engineer | StartupXYZ | Mar 2019 - Dec 2021
      - Built React/Node.js web application from scratch
      - Implemented CI/CD pipeline with GitHub Actions
      - Reduced API latency by 40% through caching optimization

      Junior Developer | WebAgency | Jun 2016 - Feb 2019
      - Developed client websites using WordPress and custom PHP
      - Managed MySQL databases and server infrastructure

      EDUCATION
      M.S. Computer Science | Stanford University | 2014 - 2016
      B.S. Mathematics | UC Berkeley | 2010 - 2014

      SKILLS
      Python, TypeScript, React, Node.js, PyTorch, TensorFlow, Kubernetes,
      Docker, AWS, GCP, PostgreSQL, Redis, GraphQL, REST APIs, Git,
      Machine Learning, Deep Learning, NLP, System Design
    "#
  }
}
