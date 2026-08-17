enum ShellWidgetKind: Equatable, Sendable {
    case agent
    case metric
    case goal
}

struct ShellWidget: Identifiable, Equatable, Sendable {
    let id: String
    let kind: ShellWidgetKind
    let title: String
    let value: String
    let detail: String
    let symbolName: String
    let progress: Double?
}
