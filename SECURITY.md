# Security Policy

## Supported use

Thug-Fugu is a local-first orchestration tool for local LLM backends. The built-in HTTP server is intended for local development and private-network use. It is not intended to be exposed directly to the public internet.

## Reporting security issues

If you find a security issue, please do not open a public issue with exploit details.

Instead, report it privately through GitHub's private vulnerability reporting if it is enabled for this repository, or contact the maintainer directly through the repository owner's GitHub profile.

When reporting, include:

- Affected version or commit
- Minimal reproduction steps
- Expected and actual behavior
- Any relevant logs with secrets removed

## Secrets and credentials

Do not commit raw API keys, `.env` files, private keys, certificates, or machine-local credentials.

Use environment-variable expansion in config files, for example:

```json
{
  "api_key": "${OPENAI_COMPATIBLE_API_KEY}"
}
```

## Deployment security

For external access, place the server behind infrastructure that provides:

- TLS termination
- Authentication
- Request size limits
- Rate limiting
- Access logging appropriate for your environment

See `docs/operations/security-profile.md` for the current operational security profile.
