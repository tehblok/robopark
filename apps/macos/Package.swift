// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RoboparkAgent",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "RoboparkAgent", targets: ["RoboparkAgent"])],
    targets: [
        .target(name: "AgentCore"),
        .executableTarget(name: "RoboparkAgent", dependencies: ["AgentCore"]),
        .executableTarget(name: "AgentCoreChecks", dependencies: ["AgentCore"], path: "Tests/AgentCoreChecks"),
        .executableTarget(name: "DesktopChecks", dependencies: ["AgentCore"], path: "Tests/DesktopChecks")
    ]
)
