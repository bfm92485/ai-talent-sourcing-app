app_name = "ai_talent_sourcing"
app_title = "AI Talent Sourcing"
app_publisher = "Bryan Murphy"
app_description = "AI-powered talent sourcing pipeline integration with ERPNext"
app_email = "bryan.f.murphy@gmail.com"
app_license = "MIT"

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Job Applicant": {
        "after_insert": "ai_talent_sourcing.enrichment_handler.after_insert"
    }
}

# Scheduled Tasks
# ---------------
# Uncomment to enable periodic enrichment retry for failed records

# scheduler_events = {
#     "hourly": [
#         "ai_talent_sourcing.tasks.retry_failed_enrichments"
#     ],
# }
