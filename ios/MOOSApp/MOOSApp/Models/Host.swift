import Foundation
import Network

struct Host: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let displayName: String
    let address: String?
    let port: UInt16?
    let deviceID: String?

    init(
        id: String,
        displayName: String,
        address: String? = nil,
        port: UInt16? = nil,
        deviceID: String? = nil
    ) {
        self.id = id
        self.displayName = displayName
        self.address = address
        self.port = port
        self.deviceID = deviceID
    }
}

struct HostConfiguration: Codable, Equatable, Sendable {
    let address: String
    let port: UInt16
    let deviceID: String
    let displayName: String?

    init(
        address: String,
        port: UInt16,
        deviceID: String,
        displayName: String? = nil
    ) throws {
        let trimmedAddress = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isTailscaleAddress(trimmedAddress) else {
            throw HostConfigurationError.invalidAddress
        }
        guard port > 0 else {
            throw HostConfigurationError.invalidPort
        }
        guard let identifier = UUID(uuidString: deviceID),
              identifier.uuidString.lowercased() == deviceID else {
            throw HostConfigurationError.invalidDeviceID
        }
        let normalizedDisplayName: String?
        if let displayName {
            guard let normalized = MOOSDisplayName.normalized(displayName) else {
                throw HostConfigurationError.invalidDisplayName
            }
            normalizedDisplayName = normalized
        } else {
            normalizedDisplayName = nil
        }
        self.address = trimmedAddress
        self.port = port
        self.deviceID = deviceID
        self.displayName = normalizedDisplayName
    }

    private static func isTailscaleAddress(_ value: String) -> Bool {
        if let address = IPv4Address(value) {
            let bytes = [UInt8](address.rawValue)
            return bytes.count == 4
                && bytes[0] == 100
                && (bytes[1] & 0xC0) == 0x40
        }
        if let address = IPv6Address(value) {
            let bytes = [UInt8](address.rawValue)
            return bytes.count == 16
                && Array(bytes.prefix(6)) == [0xFD, 0x7A, 0x11, 0x5C, 0xA1, 0xE0]
        }
        return false
    }

    var host: Host {
        Host(
            id: "\(address):\(port)",
            displayName: displayName ?? "MOOS Host",
            address: address,
            port: port,
            deviceID: deviceID
        )
    }
}

enum HostConfigurationError: Error, Equatable {
    case invalidAddress
    case invalidPort
    case invalidDeviceID
    case invalidDisplayName
}

enum MOOSDisplayName {
    static let maximumLength = 64

    static func normalized(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              trimmed.count <= maximumLength,
              trimmed.unicodeScalars.allSatisfy({
                  !CharacterSet.controlCharacters.contains($0)
              }) else {
            return nil
        }
        return trimmed
    }
}
