# WorkspaceSession integration check

Run `python3 scripts/check_macos_workspace_session.py` from the repository root.
It copies the exact production `WorkspaceSession.swift` and AgentCore sources to
a temporary Swift package, then executes them against a hermetic HTTPS
`URLProtocol`. The in-memory shims replace only Keychain, trust verification and
TLS policy; no credentials, user data, or external network are used.

The check covers native-only server cookies, persistence, relative API routing,
encoded login/logout paths, logout cleanup, delayed response generation fencing,
failed-Keychain-delete tombstones, deployment switches and the response-size
limit.
