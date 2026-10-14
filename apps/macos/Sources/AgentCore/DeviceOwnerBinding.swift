import Foundation

/// Persisted native identity state for a Mac that belongs to one Robopark user.
/// `ownerUserID` is set only from an authenticated device-claim response.
public struct DeviceOwnerBinding: Codable, Equatable {
    public let ownerUserID: Int?
    public let activeUserID: Int?

    public init(ownerUserID: Int?, activeUserID: Int?) {
        self.ownerUserID = ownerUserID
        self.activeUserID = activeUserID
    }

    public func acceptingAuthenticatedUser(_ userID: Int) throws -> DeviceOwnerBinding {
        guard userID > 0,
              ownerUserID == nil || ownerUserID == userID,
              activeUserID == nil || activeUserID == userID else {
            throw AgentError.ownerMismatch
        }
        return DeviceOwnerBinding(ownerUserID: ownerUserID, activeUserID: userID)
    }

    public func bindingAuthoritativeOwner(_ userID: Int) throws -> DeviceOwnerBinding {
        guard userID > 0,
              ownerUserID == nil || ownerUserID == userID,
              activeUserID == nil || activeUserID == userID else {
            throw AgentError.ownerMismatch
        }
        return DeviceOwnerBinding(ownerUserID: userID, activeUserID: activeUserID)
    }

    public func signedOut() -> DeviceOwnerBinding {
        DeviceOwnerBinding(ownerUserID: ownerUserID, activeUserID: nil)
    }
}

/// Identity fields returned by the authenticated, server-authoritative device claim.
public struct AuthenticatedDeviceClaim: Decodable {
    public let deviceID: String
    public let ownerUserID: Int
    public let ownerTrackerLogin: String?
    public let status: String
    public let instanceID: String
    public let environment: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case ownerUserID = "owner_user_id"
        case ownerTrackerLogin = "owner_tracker_login"
        case status
        case instanceID = "instance_id"
        case environment
    }

    public func validate(expectedOwnerUserID: Int, existingDeviceID: String?) throws {
        guard UUID(uuidString: deviceID) != nil,
              ownerUserID > 0, ownerUserID == expectedOwnerUserID,
              status == "approved",
              existingDeviceID == nil || existingDeviceID == deviceID else {
            throw AgentError.ownerMismatch
        }
    }
}
