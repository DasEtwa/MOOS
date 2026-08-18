import CoreFoundation
import Foundation

enum MOOSProtocolV1 {
    static let version = 1
    static let maximumFrameBytes = 16 * 1024

    enum Operation: String, CaseIterable, Codable, Sendable {
        case status
        case personalStatus = "personal.status"
        case personalStart = "personal.start"
        case personalStop = "personal.stop"
        case personalTerminalOpen = "personal.terminal.open"
    }

    struct ControlRequest: Codable, Equatable, Sendable {
        let protocolVersion: Int
        let operation: Operation

        init(operation: Operation) {
            protocolVersion = MOOSProtocolV1.version
            self.operation = operation
        }
    }

    struct StatusResponse: Equatable, Sendable {
        let personalSystem: PersonalSystem
    }

    enum ClientError: Error, Equatable, LocalizedError {
        case unsupportedProtocol
        case malformedResponse(String)
        case server(code: String, message: String)

        var errorDescription: String? {
            switch self {
            case .unsupportedProtocol:
                return "The host uses an incompatible MOOS protocol version."
            case .malformedResponse:
                return "The host returned an invalid Protocol v1 response."
            case .server(_, let message):
                return message
            }
        }
    }

    static func encodeRequest(_ request: ControlRequest) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        var data = try encoder.encode(request)
        guard data.count <= maximumFrameBytes else {
            throw ClientError.malformedResponse("request exceeds the frame limit")
        }
        data.append(0x0A)
        return data
    }

    static func parseStatusResponse(_ data: Data) throws -> StatusResponse {
        guard data.count <= maximumFrameBytes else {
            throw ClientError.malformedResponse("response exceeds the frame limit")
        }

        let value: Any
        do {
            value = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw ClientError.malformedResponse("response is not valid JSON")
        }
        guard let object = value as? [String: Any] else {
            throw ClientError.malformedResponse("response is not an object")
        }

        guard let version = object["protocolVersion"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: version.objCType)),
              version.intValue == Self.version else {
            throw ClientError.unsupportedProtocol
        }
        guard let okValue = object["ok"] as? NSNumber,
              CFGetTypeID(okValue) == CFBooleanGetTypeID() else {
            throw ClientError.malformedResponse("ok is not a boolean")
        }

        if !okValue.boolValue {
            guard let error = object["error"] as? [String: Any],
                  let code = error["code"] as? String, !code.isEmpty,
                  let message = error["message"] as? String, !message.isEmpty else {
                throw ClientError.malformedResponse("error response is incomplete")
            }
            throw ClientError.server(code: code, message: message)
        }

        guard object["operation"] as? String == Operation.status.rawValue else {
            throw ClientError.malformedResponse("operation does not match status")
        }
        guard let events = object["events"] as? [Any],
              events.allSatisfy({ $0 is [String: Any] }) else {
            throw ClientError.malformedResponse("events is not an object array")
        }
        guard let responseData = object["data"] as? [String: Any],
              let personal = responseData["personal"] as? [String: Any],
              personal["identity"] as? String == "personal",
              let stateValue = personal["state"] as? String,
              let state = PersonalSystemState(rawValue: stateValue) else {
            throw ClientError.malformedResponse("Personal status is invalid")
        }
        if let result = personal["result"], !(result is NSNull), !(result is String) {
            throw ClientError.malformedResponse("Personal result is invalid")
        }

        return StatusResponse(
            personalSystem: PersonalSystem(
                id: "personal",
                displayName: "Personal MOOS",
                state: state
            )
        )
    }
}

struct MOOSProtocolV1Client: Sendable {
    private let connection: any MOOSConnection

    init(connection: any MOOSConnection) {
        self.connection = connection
    }

    func status() async throws -> MOOSProtocolV1.StatusResponse {
        let request = try MOOSProtocolV1.encodeRequest(
            .init(operation: .status)
        )
        let response = try await connection.request(
            request,
            maximumResponseBytes: MOOSProtocolV1.maximumFrameBytes
        )
        return try MOOSProtocolV1.parseStatusResponse(response)
    }

    func close() async {
        await connection.close()
    }
}
