# Security Policy

## Supported Versions

Message Visualizer is currently in **alpha**. Only the `main` branch
receives security updates — there are no point releases yet.

| Version | Supported |
|---|---|
| `main` (head) | ✅ |
| Tagged releases | ❌ (no tags published yet) |

## Reporting a vulnerability

If you discover a security issue, **please don't open a public GitHub
issue**. Use one of these private channels instead:

1. **Preferred** — Open a [GitHub Security Advisory](https://github.com/homegrownhuman/msgviz/security/advisories/new).
   GitHub will create a private discussion thread between you and the
   maintainers.

2. **Fallback** — Open a regular issue *without describing the
   vulnerability*, asking the maintainers to email you. We'll respond
   privately with a secure channel.

When you report, please include:

- The version / commit you tested against
- Steps to reproduce
- The impact you've established (read-only? RCE? path traversal?)
- Whether you've already discussed this publicly elsewhere

You'll receive an initial response within a few business days. For
issues we accept, we'll work with you on a disclosure timeline (90 days
is the default).

## What's in scope

- The application code under `msgviz/`, `app/`, `scripts/`, `tools/`
- The HTTP API and WebSocket endpoints
- The bundled demo dataset (`demo/`) only insofar as it could enable a
  cross-tenant issue (e.g. path traversal that escapes the demo)

## What's out of scope

- The Whisper / OCR / ffmpeg binaries themselves — those have their
  own security trackers
- Self-inflicted issues from running Message Visualizer on untrusted
  networks without your own auth layer (the README is explicit:
  `msgviz serve` binds to `127.0.0.1` by default; if you expose it
  publicly you're responsible for adding auth — see
  [docs/API.md](../docs/API.md#cors-auth-https))
- Vulnerabilities in your own iMessage / WhatsApp data
- Performance or resource-exhaustion bugs that require attacker access
  to the local machine (you already have a much bigger problem)

## What we won't do

- Pay bug bounties (this is a hobby/personal project, not a funded one)
- Hide a vulnerability indefinitely — if we can't fix it within a
  reasonable timeframe we'll publish the advisory with mitigation steps
- Treat reports as adversarial; we appreciate the work

Thanks for helping keep Message Visualizer's users safe.
