import Foundation

public enum TestDeploymentAssignment {
    public static let origin = "https://roboparktest.ru.tuna.am"
    public static let instanceID = "d6ed3b43-c41b-4614-833e-c5ec30936ef3"
    public static let environment = "test"

    public static func matches(origin candidateOrigin: String, instanceID: String, environment: String) -> Bool {
        candidateOrigin == origin && instanceID == self.instanceID && environment == self.environment
    }
}
