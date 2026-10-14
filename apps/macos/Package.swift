// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RoboparkAgent",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "RoboparkAgent", targets: ["RoboparkAgent"]),
        .executable(name: "RoboparkUpdater", targets: ["RoboparkUpdater"]),
        .executable(name: "RoboparkComputeWorker", targets: ["RoboparkComputeWorker"]),
    ],
    targets: [
        .target(name: "AgentCore"),
        .target(name: "ComputePeerCore", dependencies: ["AgentCore"]),
        .executableTarget(name: "RoboparkAgent", dependencies: ["AgentCore", "ComputePeerCore"]),
        .executableTarget(name: "RoboparkUpdater", dependencies: ["AgentCore"]),
        .executableTarget(name: "RoboparkComputeWorker", dependencies: ["AgentCore"]),
        .executableTarget(name: "AgentCoreChecks", dependencies: ["AgentCore"], path: "Tests/AgentCoreChecks"),
        .executableTarget(name: "DesktopChecks", dependencies: ["AgentCore"], path: "Tests/DesktopChecks"),
        .executableTarget(name: "ComputePeerChecks", dependencies: ["AgentCore", "ComputePeerCore"], path: "Tests/ComputePeerChecks"),
    ]
)
