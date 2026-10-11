# Security Policy

OpenDubs is a research robot. This document describes its **current security posture** (so you
deploy it safely) and how to report vulnerabilities.

## Deployment posture — read before exposing a robot to a network

The on-board services are currently designed for a **trusted local network only** and have **no
authentication**:

- **Dashboard** (FastAPI, kiosk, `:8080` HTTP and `:8443` HTTPS) — no auth; exposes telemetry and
  control endpoints. It refuses a request or WebSocket opened by a web page from another site (its
  `Origin` check). That stops a page in a browser on the robot's network from driving the robot.
  Tools and scripts, which send no `Origin`, are let through.
- **ROS access from the network** goes through the dashboard's `/rosbridge`
  (`ws://<robot>:8080/rosbridge`, `wss://<robot>:8443/rosbridge`). It relays the ordinary rosbridge
  protocol, gives full access to the ROS graph, and has the same `Origin` check. rosbridge itself
  listens only on the robot (`127.0.0.1:9090`).
- **Foxglove bridge** (`:8765`) — no auth; full ROS graph access to anyone who can reach the port.
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

- Contact the maintainers directly. Once the repository is public, you can also use GitHub's private
  "Report a vulnerability" (Security Advisories) on it.

> Maintainers: add a security contact email here before publishing.

We'll acknowledge the report, work with you on a fix, and credit you if you'd like.
