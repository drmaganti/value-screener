# Security and Credential Model

## Current Model

Value Screener runs through GitHub Actions on a scheduled workflow. The workflow uses external services such as Groq and Gmail SMTP, which require credentials. These values are stored as GitHub repository secrets and injected into the runtime environment. Secrets are not hardcoded in the repository.

## Machine Identity

In this project, the GitHub Actions runner acts as the machine identity. It is a non-human actor that needs temporary access to external services in order to complete the weekly screening workflow.

The runner requires access to:
- Groq API credentials for LLM-based classification
- Gmail SMTP credentials for sending the weekly digest
- Market data providers where applicable

## Risks

Key risks include:
- Accidental secret exposure through commits or logs
- Long-lived credentials remaining active after they are no longer needed
- Over-permissioned credentials
- Lack of visibility into when credentials are accessed
- Manual rotation of API keys or app passwords

## Future-State Design

In a production-grade version, secrets would be managed through a dedicated secrets manager such as 1Password. The workflow would retrieve secrets at runtime using a service account or secret reference rather than storing long-lived credentials directly in GitHub.

This would allow:
- Centralized credential management
- Cleaner secret rotation
- Reduced exposure of long-lived credentials
- Better control over which machine identity can access which secret
- Stronger auditability of secret usage

## Auditability

The system should log workflow execution, provider calls, classification outcomes, and digest delivery status without logging sensitive values. Logs should confirm that required credentials were available without exposing the credentials themselves.
