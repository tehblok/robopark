#!/usr/bin/env python3
"""Compile and exercise the production macOS WorkspaceServer in a temporary app.

The probe uses production WorkspaceServer.swift and AgentCore. Only Keychain and
WorkspaceSession are replaced with deterministic in-memory shims. It does not
start the production agent, contact the network, or print session credentials.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "apps" / "macos"
DEFAULT_SOURCE = MACOS / "Sources" / "RoboparkAgent" / "WorkspaceServer.swift"

PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "WorkspaceServerChecks",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "AgentCore"),
        .executableTarget(name: "WorkspaceServerChecks", dependencies: ["AgentCore"]),
    ]
)
"""

SHIMS = r"""
import Foundation
import AgentCore

enum NetworkFailure: Error { case invalid }

// Test-only Keychain replacement. No credentials leave this process.
enum SecureStore {
    private static var values: [String: Data] = [:]
    static func readData(_ account: String) throws -> Data? { values[account] }
    static func writeData(_ data: Data, account: String) throws { values[account] = data }
}

struct TestProfile { let instanceID = "workspace-server-check" }

@MainActor final class WorkspaceSession {
    var profile: TestProfile? = TestProfile()
    init() throws {}
    func proxy(_ request: LocalHTTPRequest) async -> LocalHTTPResponse {
        .json(["detail": "network_disabled_in_workspace_server_check"], status: 503)
    }
}
"""

MAIN = r"""
import Foundation
import AgentCore

private var checks = 0
private var failures: [String] = []

private func check(_ value: @autoclosure () -> Bool, _ name: String) {
    if value() { checks += 1 } else { failures.append(name) }
}

private func request(_ target: String, cookie: String) throws -> LocalHTTPRequest {
    let wire = "GET \(target) HTTP/1.1\r\nHost: 127.0.0.1:18444\r\nCookie: rp_local=\(cookie)\r\n\r\n"
    return try LocalHTTPRequest.parse(Data(wire.utf8))!
}

private func text(_ response: LocalHTTPResponse) -> String {
    String(data: response.body, encoding: .utf8) ?? ""
}

@main struct Run {
    @MainActor static func main() async throws {
        let server = try WorkspaceServer()
        let cookie = server.sessionCookie.value
        let arguments = CommandLine.arguments
        let packagingSmoke = arguments.contains("--packaging-smoke")

        let root = await server.handle(try request("/", cookie: cookie))
        check(root.status == 200, "authenticated root serves index")
        check(text(root).contains("/__robopark/config.js"), "root injects desktop config")

        let rootWithQuery = await server.handle(try request("/?source=check", cookie: cookie))
        check(rootWithQuery.status == 200, "root with query serves index")
        let explicitIndex = await server.handle(try request("/index.html", cookie: cookie))
        check(explicitIndex.status == 200, "explicit index serves index")

        if packagingSmoke {
            if let markerIndex = arguments.firstIndex(of: "--asset"), markerIndex + 1 < arguments.count {
                for asset in arguments[(markerIndex + 1)...] where asset.hasPrefix("/") {
                    let response = await server.handle(try request(asset, cookie: cookie))
                    check(response.status == 200 && !response.body.isEmpty, "packaged asset \(asset) is readable")
                }
            }
        } else {
            check(text(root).contains("workspace fixture"), "root returns deterministic fixture")
            let login = await server.handle(try request("/login", cookie: cookie))
            check(login.status == 200 && text(login).contains("workspace fixture"), "login uses SPA fallback")
            let deep = await server.handle(try request("/parks/42/dashboard", cookie: cookie))
            check(deep.status == 200 && text(deep).contains("workspace fixture"), "deep path uses SPA fallback")

            let javascript = await server.handle(try request("/assets/app.js", cookie: cookie))
            check(javascript.status == 200 && text(javascript) == "console.log('workspace fixture');\n",
                  "JavaScript asset body is served")
            check(javascript.headers["Content-Type"] == "application/javascript", "JavaScript MIME type")
            let stylesheet = await server.handle(try request("/assets/app.css", cookie: cookie))
            check(stylesheet.status == 200 && text(stylesheet) == "body { color: rgb(1, 2, 3); }\n",
                  "CSS asset body is served")
            check(stylesheet.headers["Content-Type"] == "text/css", "CSS MIME type")

            let missing = await server.handle(try request("/assets/missing.js", cookie: cookie))
            check(missing.status == 404, "missing asset returns 404")
            let unauthorized = await server.handle(try request("/", cookie: "invalid-local-cookie"))
            check(unauthorized.status == 403 && text(unauthorized).contains("local_session_required"),
                  "invalid local cookie returns 403")

            do {
                _ = try request("/../outside.txt", cookie: cookie)
                failures.append("traversal request is rejected")
            } catch {
                checks += 1
            }

            let escaped = await server.handle(try request("/assets/escape.js", cookie: cookie))
            check(escaped.status == 503 && text(escaped).contains("bundled_workspace_unavailable"),
                  "symlink escape returns 503")

            let workspace = Bundle.main.resourceURL!.appendingPathComponent("workspace", isDirectory: true)
            try FileManager.default.removeItem(at: workspace.appendingPathComponent("index.html"))
            let withoutIndex = await server.handle(try request("/login", cookie: cookie))
            check(withoutIndex.status == 503 && text(withoutIndex).contains("bundled_workspace_unavailable"),
                  "missing index returns 503")
        }

        if failures.isEmpty {
            print("PASS: \(checks) production WorkspaceServer bundle and routing checks")
            return
        }
        for failure in failures { fputs("FAIL: \(failure)\n", stderr) }
        Foundation.exit(1)
    }
}
"""


def fixture_workspace(workspace: Path, outside: Path) -> None:
    (workspace / "assets").mkdir(parents=True)
    (workspace / "index.html").write_text(
        "<!doctype html><html><head></head><body>workspace fixture</body></html>\n"
    )
    (workspace / "assets" / "app.js").write_text(
        "console.log('workspace fixture');\n"
    )
    (workspace / "assets" / "app.css").write_text(
        "body { color: rgb(1, 2, 3); }\n"
    )
    outside.write_text("this file must never be served\n")
    (workspace / "assets" / "escape.js").symlink_to(outside)


def packaged_assets(workspace: Path) -> list[str]:
    """Return one JS and one CSS path when present, without following symlinks."""
    assets: list[str] = []
    for suffix in (".js", ".css"):
        candidates = sorted(
            path
            for path in workspace.rglob(f"*{suffix}")
            if path.is_file() and not path.is_symlink()
        )
        if candidates:
            assets.append("/" + candidates[0].relative_to(workspace).as_posix())
    return assets


def resolve_app_workspace(app: Path) -> Path:
    workspace = app.resolve() / "Contents" / "Resources" / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"app has no Contents/Resources/workspace directory: {app}")
    if not (workspace / "index.html").is_file():
        raise ValueError(f"app workspace has no index.html: {app}")
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile production WorkspaceServer.swift and run hermetic bundle checks."
    )
    parser.add_argument(
        "--app",
        type=Path,
        help="also use workspace resources copied read-only from an existing .app",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--source-ref",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the generated package under /private/tmp"
    )
    args = parser.parse_args()
    source = args.source.resolve()
    source_bytes: bytes | None = None
    if args.source_ref:
        source_result = subprocess.run(
            [
                "git",
                "show",
                f"{args.source_ref}:apps/macos/Sources/RoboparkAgent/WorkspaceServer.swift",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            check=False,
        )
        if source_result.returncode != 0:
            parser.error(f"cannot read WorkspaceServer.swift from git ref {args.source_ref}")
        source_bytes = source_result.stdout
    elif not source.is_file():
        parser.error(f"WorkspaceServer source does not exist: {source}")

    supplied_workspace: Path | None = None
    if args.app is not None:
        try:
            supplied_workspace = resolve_app_workspace(args.app)
        except ValueError as error:
            parser.error(str(error))

    temporary = Path(tempfile.mkdtemp(prefix="robopark-workspace-server-"))
    try:
        source_root = temporary / "Sources"
        shutil.copytree(MACOS / "Sources" / "AgentCore", source_root / "AgentCore")
        check_source = source_root / "WorkspaceServerChecks"
        check_source.mkdir(parents=True)
        if source_bytes is None:
            shutil.copy2(source, check_source / "WorkspaceServer.swift")
        else:
            (check_source / "WorkspaceServer.swift").write_bytes(source_bytes)
        (check_source / "Shims.swift").write_text(SHIMS)
        (check_source / "main.swift").write_text(MAIN)
        (temporary / "Package.swift").write_text(PACKAGE)

        cache = Path("/private/tmp/robopark-workspace-server-checks")
        cache.mkdir(parents=True, exist_ok=True)
        command = [
            "swift",
            "build",
            "-c",
            "release",
            "--package-path",
            str(temporary),
            "--scratch-path",
            str(cache),
            "--cache-path",
            str(cache / "cache"),
            "--config-path",
            str(cache / "config"),
            "--security-path",
            str(cache / "security"),
            "--disable-sandbox",
            "--product",
            "WorkspaceServerChecks",
        ]
        build = subprocess.run(
            command,
            env={
                **os.environ,
                "CLANG_MODULE_CACHE_PATH": str(cache / "clang"),
                "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules"),
            },
            check=False,
        )
        if build.returncode != 0:
            return build.returncode

        app = temporary / "WorkspaceServerChecks.app"
        executable_dir = app / "Contents" / "MacOS"
        resource_dir = app / "Contents" / "Resources"
        executable_dir.mkdir(parents=True)
        resource_dir.mkdir(parents=True)
        shutil.copy2(cache / "release" / "WorkspaceServerChecks", executable_dir)
        (app / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleExecutable": "WorkspaceServerChecks",
                    "CFBundleIdentifier": "ru.robopark.workspace-server-checks",
                    "CFBundlePackageType": "APPL",
                }
            )
        )

        workspace = resource_dir / "workspace"
        outside = temporary / "outside.js"
        fixture_workspace(workspace, outside)
        fixture_result = subprocess.run(
            [str(executable_dir / "WorkspaceServerChecks")], check=False
        )

        smoke_returncode = 0
        if supplied_workspace is not None:
            shutil.rmtree(workspace)
            shutil.copytree(supplied_workspace, workspace, symlinks=True)
            run_arguments = ["--packaging-smoke"]
            assets = packaged_assets(workspace)
            if assets:
                run_arguments.extend(["--asset", *assets])
            smoke_result = subprocess.run(
                [str(executable_dir / "WorkspaceServerChecks"), *run_arguments],
                check=False,
            )
            smoke_returncode = smoke_result.returncode
        return fixture_result.returncode or smoke_returncode
    finally:
        if args.keep:
            destination = Path("/private/tmp/robopark-workspace-server-kept")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(temporary, destination, symlinks=True)
            print(f"kept generated test package at {destination}")
        shutil.rmtree(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
