import CoreFoundation
import CryptoKit
import Foundation
import Security

enum MOOSGatewayV1 {
    static let version = 1
    static let maximumFrameBytes = 16 * 1024
    static let pairingPrefix = "moos-pair-v1"
    static let nonceBytes = 32
    static let deviceKeyBytes = 32

    struct PairingCredential: Equatable, Sendable {
        let deviceID: String
        let key: Data
    }

    enum ClientError: Error, Equatable, LocalizedError {
        case invalidPairingCode
        case incompatibleGateway
        case authenticationFailed
        case malformedResponse
        case missingPermission
        case randomGenerationFailed

        var errorDescription: String? {
            switch self {
            case .invalidPairingCode:
                return "The MOOS pairing code is invalid."
            case .incompatibleGateway:
                return "The Host uses an incompatible MOOS Gateway version."
            case .authenticationFailed:
                return "This device is not authorized by the MOOS Host."
            case .malformedResponse:
                return "The Host returned an invalid Gateway response."
            case .missingPermission:
                return "This device is not allowed to read MOOS status."
            case .randomGenerationFailed:
                return "A secure authentication nonce could not be created."
            }
        }
    }

    static func parsePairingCode(_ value: String) throws -> PairingCredential {
        let parts = value.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 3,
              String(parts[0]) == pairingPrefix,
              let identifier = UUID(uuidString: String(parts[1])),
              identifier.uuidString.lowercased() == String(parts[1]),
              let key = decodeBase64URL(String(parts[2]), expectedBytes: deviceKeyBytes) else {
            throw ClientError.invalidPairingCode
        }
        return PairingCredential(deviceID: String(parts[1]), key: key)
    }

    static func authenticate(
        connection: any MOOSGatewayConnection,
        credential: PairingCredential,
        clientNonce suppliedClientNonce: Data? = nil
    ) async throws {
        guard credential.key.count == deviceKeyBytes,
              let identifier = UUID(uuidString: credential.deviceID),
              identifier.uuidString.lowercased() == credential.deviceID else {
            throw ClientError.invalidPairingCode
        }
        let challengeData = try await connection.receiveFrame(
            maximumFrameBytes: maximumFrameBytes
        )
        let challenge = try parseObject(challengeData)
        try requireVersion(challenge)
        guard Set(challenge.keys) == ["gatewayVersion", "type", "nonce"],
              challenge["type"] as? String == "challenge",
              let nonceText = challenge["nonce"] as? String,
              let serverNonce = decodeBase64URL(
                nonceText, expectedBytes: nonceBytes
              ) else {
            throw ClientError.malformedResponse
        }

        let clientNonce: Data
        if let suppliedClientNonce {
            guard suppliedClientNonce.count == nonceBytes else {
                throw ClientError.randomGenerationFailed
            }
            clientNonce = suppliedClientNonce
        } else {
            clientNonce = try secureRandomData(count: nonceBytes)
        }
        let proof = authenticationProof(
            key: credential.key,
            serverNonce: serverNonce,
            clientNonce: clientNonce,
            deviceID: credential.deviceID
        )
        let authenticate: [String: Any] = [
            "gatewayVersion": version,
            "type": "authenticate",
            "deviceId": credential.deviceID,
            "clientNonce": encodeBase64URL(clientNonce),
            "proof": encodeBase64URL(proof),
        ]
        try await connection.sendFrame(try encodeObject(authenticate))

        let responseData = try await connection.receiveFrame(
            maximumFrameBytes: maximumFrameBytes
        )
        let response = try parseObject(responseData)
        try requireVersion(response)
        if response["type"] as? String == "error" {
            guard Set(response.keys) == [
                "gatewayVersion", "type", "code", "message",
            ],
            let code = response["code"] as? String, !code.isEmpty,
            let message = response["message"] as? String, !message.isEmpty else {
                throw ClientError.malformedResponse
            }
            throw ClientError.authenticationFailed
        }
        guard Set(response.keys) == [
            "gatewayVersion", "type", "deviceId", "permissions", "serverProof",
        ],
        response["type"] as? String == "authenticated",
        response["deviceId"] as? String == credential.deviceID,
        let permissions = response["permissions"] as? [Any],
        permissions.allSatisfy({ $0 is String }),
        let proofText = response["serverProof"] as? String,
        let serverProof = decodeBase64URL(
            proofText, expectedBytes: deviceKeyBytes
        ) else {
            throw ClientError.malformedResponse
        }
        let serverPayload = authenticationPayload(
            context: "MOOS-GATEWAY-SERVER-V1\n",
            serverNonce: serverNonce,
            clientNonce: clientNonce,
            deviceID: credential.deviceID
        )
        guard HMAC<SHA256>.isValidAuthenticationCode(
            serverProof,
            authenticating: serverPayload,
            using: SymmetricKey(data: credential.key)
        ) else {
            throw ClientError.authenticationFailed
        }
        guard permissions.contains(where: { ($0 as? String) == "status" }) else {
            throw ClientError.missingPermission
        }
    }

    static func authenticationProof(
        key: Data,
        serverNonce: Data,
        clientNonce: Data,
        deviceID: String
    ) -> Data {
        let payload = authenticationPayload(
            context: "MOOS-GATEWAY-AUTH-V1\n",
            serverNonce: serverNonce,
            clientNonce: clientNonce,
            deviceID: deviceID
        )
        return Data(
            HMAC<SHA256>.authenticationCode(
                for: payload,
                using: SymmetricKey(data: key)
            )
        )
    }

    static func serverAuthenticationProof(
        key: Data,
        serverNonce: Data,
        clientNonce: Data,
        deviceID: String
    ) -> Data {
        let payload = authenticationPayload(
            context: "MOOS-GATEWAY-SERVER-V1\n",
            serverNonce: serverNonce,
            clientNonce: clientNonce,
            deviceID: deviceID
        )
        return Data(
            HMAC<SHA256>.authenticationCode(
                for: payload,
                using: SymmetricKey(data: key)
            )
        )
    }

    private static func authenticationPayload(
        context: String,
        serverNonce: Data,
        clientNonce: Data,
        deviceID: String
    ) -> Data {
        var payload = Data(context.utf8)
        payload.append(Data(encodeBase64URL(serverNonce).utf8))
        payload.append(0x0A)
        payload.append(Data(encodeBase64URL(clientNonce).utf8))
        payload.append(0x0A)
        payload.append(Data(deviceID.utf8))
        payload.append(0x0A)
        return payload
    }

    private static func secureRandomData(count: Int) throws -> Data {
        var data = Data(count: count)
        let status = data.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, count, buffer.baseAddress!)
        }
        guard status == errSecSuccess else {
            throw ClientError.randomGenerationFailed
        }
        return data
    }

    private static func encodeObject(_ object: [String: Any]) throws -> Data {
        guard JSONSerialization.isValidJSONObject(object) else {
            throw ClientError.malformedResponse
        }
        var data = try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        )
        guard data.count <= maximumFrameBytes else {
            throw ClientError.malformedResponse
        }
        data.append(0x0A)
        return data
    }

    private static func parseObject(_ data: Data) throws -> [String: Any] {
        guard data.count <= maximumFrameBytes,
              let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any] else {
            throw ClientError.malformedResponse
        }
        return dictionary
    }

    private static func requireVersion(_ object: [String: Any]) throws {
        guard let number = object["gatewayVersion"] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: number.objCType)),
              number.intValue == version else {
            throw ClientError.incompatibleGateway
        }
    }

    private static func encodeBase64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private static func decodeBase64URL(
        _ value: String,
        expectedBytes: Int
    ) -> Data? {
        guard !value.isEmpty, !value.contains("=") else {
            return nil
        }
        var standard = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        standard += String(repeating: "=", count: (4 - standard.count % 4) % 4)
        guard let decoded = Data(base64Encoded: standard),
              decoded.count == expectedBytes,
              encodeBase64URL(decoded) == value else {
            return nil
        }
        return decoded
    }
}
