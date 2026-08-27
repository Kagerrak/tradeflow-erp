# Security Policy

## Supported versions

Only the latest release on the `main` branch is actively supported with security updates. Earlier tagged releases and unmerged feature branches are not monitored for vulnerabilities.

## Reporting a vulnerability

If you discover a security issue in TradeFlow ERP, please report it privately rather than opening a public issue or pull request.

Email: **romzhey@gmail.com**

Please include:

- A clear description of the vulnerability
- Steps to reproduce, or a minimal proof of concept
- The affected component(s), version or commit
- Any suggested remediation

You can expect an initial response within **5 business days**. We will coordinate disclosure and credit you unless you request otherwise.

## Scope

The following are in scope for vulnerability reports:

- Source code in this repository
- Default configuration files and Docker Compose definitions
- Public-facing API endpoints documented in `openapi/openapi.json`

The following are out of scope:

- Self-hosted deployments with custom configuration
- Third-party services integrated via environment variables
- Issues that require physical or social-engineering access

## Disclosure policy

We follow a private disclosure process. Once a fix is released, we will publish a security advisory and credit the reporter.

## Safe harbor

Good-faith security research is authorized and protected. Do not access data you do not own, and do not degrade service for other users.
