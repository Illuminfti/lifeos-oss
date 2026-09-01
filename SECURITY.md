# Security policy

LifeOS processes highly sensitive personal data. Do not open a public issue containing credentials, provider sessions, webhook bodies, private wiki excerpts, raw captures, identity maps, or operator logs.

## Reporting

Report vulnerabilities through GitHub's private security-advisory flow for this repository. Include the affected commit, component, reproduction using synthetic data, impact, and proposed mitigation. Do not test against accounts or systems you do not own or have explicit permission to assess.

## Supported version

Only the latest commit on `main` and active release branches receive security fixes during the alpha.

## Credential rules

- Pass `env:` or absolute `file:` secret handles only.
- Keep secret files outside the repository and brain at mode `0600` or stricter.
- Never commit Telegram sessions, OAuth tokens, app secrets, webhook verification tokens, cookies, or provider exports containing personal data.
- Rotate any credential exposed to terminal history, logs, CI, an issue, or a pull request.

## Public contribution rules

Fixtures must be synthetic. Contributions must not contain private estate names, paths, hostnames, wiki text, souls, Guardian/Hermes configuration, or operator logs. Screenpipe must remain an API integration; do not vendor or reimplement its recorder.

See `docs/threat-model.md` for trust boundaries and known alpha limitations.
