import Foundation

public struct ResourceTelemetry: Equatable {
    public let cpuPercent: Double
    public let memoryPercent: Double
    public let thermalState: String

    public init(cpuPercent: Double, memoryPercent: Double, thermalState: String) {
        self.cpuPercent = min(100, max(0, cpuPercent.isFinite ? cpuPercent : 100))
        self.memoryPercent = min(100, max(0, memoryPercent.isFinite ? memoryPercent : 100))
        self.thermalState = ["normal", "fair", "serious", "critical"].contains(thermalState) ? thermalState : "normal"
    }

    public func capacity(base: Int, activeJobs: Int, ready: Bool, paused: Bool) -> Int {
        guard ready, !paused, base > 0, activeJobs >= 0,
              thermalState != "serious", thermalState != "critical",
              cpuPercent < 95, memoryPercent < 90 else { return 0 }
        let thermalLimit = thermalState == "fair" || cpuPercent >= 80 || memoryPercent >= 80 ? 1 : min(8, base)
        return thermalLimit
    }
}
