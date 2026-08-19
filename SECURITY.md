# Security Policy

OpenDubs is a research robot. This document describes its **current security posture** (so you
deploy it safely) and how to report vulnerabilities.

## Deployment posture — read before exposing a robot to a network

The on-board services are currently designed for a **trusted local network only** and have **no
authentication**:

- **Dashboard** (FastAPI, kiosk) — no auth; exposes telemetry and control endpoints.
- **rosbridge** (`:9090`) and **Foxglove bridge** — no auth; full ROS graph access to anyone who can
  reach the port.
- **Robot control** ultimately actuates motors and a contactor. Treat network access to the robot as
  equivalent to physical access.

**Do not expose these services to the public internet or an untrusted/shared network.** Keep the
robot behind its own router/firewall, and add authentication and transport security **before** any
exposure to students or other untrusted users. This is a known gap and a planned hardening item.

## No secrets in the repository

Per-unit identity and secrets (hostnames, Wi-Fi credentials, VNC passwords, ROS domain IDs) are
**never committed** — they are provided at provision time via environment variables (e.g.
`OPENDUBS_VNC_PASS`) or a gitignored `ops/` directory. If you find a credential, key, or private
network detail committed anywhere in this repository, please report it (below) — that's a bug.

## Reporting a vulnerability

Please report security issues **privately** rather than opening a public issue:

- Use GitHub's private "Report a vulnerability" (Security Advisories) on this repository, or
- Contact the maintainers directly.

> Maintainers: add a security contact email here before publishing.

We'll acknowledge the report, work with you on a fix, and credit you if you'd like.
