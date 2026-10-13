# Bridge lifecycle regression

Run `python3 scripts/check_macos_bridge_lifecycle.py`. It compiles the exact
production `Bridge.swift` with AgentCore in a temporary package. In-memory
Keychain/trust/network shims make delayed claims deterministic and make no
external request. It covers wake during claim, explicit disconnect cancellation,
lost-claim key reuse, and failed registration persistence.
