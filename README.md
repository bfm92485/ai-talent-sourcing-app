# AI Talent Sourcing

Custom Frappe application for AI-powered talent sourcing pipeline integration with ERPNext.

## Installation

```bash
bench get-app ai_talent_sourcing https://github.com/bfm92485/ai-talent-sourcing-app --branch main
bench --site <site-name> install-app ai_talent_sourcing
```

## Modules

- **Talent Candidate**: Custom DocType for managing sourced candidates with AI scoring
- **Candidate Enrichment**: Linked records storing raw enrichment payloads for audit
- **Persona Config**: Master configuration for waterfall routing logic per hiring persona

## Requirements

- Frappe Framework v16
- ERPNext v16
- HRMS v16

## License

MIT
