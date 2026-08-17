import Foundation

struct SynchronizedUptime: Equatable, Sendable {
    let secondsAtSynchronization: TimeInterval
    let synchronizedAt: Date

    func seconds(at date: Date) -> TimeInterval {
        secondsAtSynchronization + max(0, date.timeIntervalSince(synchronizedAt))
    }

    func formatted(at date: Date) -> String {
        let totalMinutes = Int(seconds(at: date)) / 60
        let days = totalMinutes / (24 * 60)
        let hours = (totalMinutes % (24 * 60)) / 60
        let minutes = totalMinutes % 60

        if days > 0 {
            return "\(days)d \(hours)h"
        }
        if hours > 0 {
            return "\(hours)h \(minutes)m"
        }
        return "\(minutes)m"
    }
}

struct LiveWidgetValue: Equatable, Sendable {
    let value: String
    let detail: String
    let progress: Double?
}

struct LiveMOOSState: Equatable, Sendable {
    let connectionState: ConnectionState
    let personalSystemState: PersonalSystemState
    let widgets: [String: LiveWidgetValue]
    let sessions: [AppSession]
    let latencyMilliseconds: Int?
    let uptime: SynchronizedUptime?
    let synchronizedAt: Date?
}
