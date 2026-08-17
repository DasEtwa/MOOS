enum ShellWidgetKind: String, Codable, Equatable, Sendable {
    case agent
    case metric
    case goal
}

struct WidgetMetadata: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let kind: ShellWidgetKind
    let title: String
    let symbolName: String
}

struct ShellWidget: Identifiable, Equatable, Sendable {
    let metadata: WidgetMetadata
    let value: String
    let detail: String
    let progress: Double?

    var id: String { metadata.id }
    var kind: ShellWidgetKind { metadata.kind }
    var title: String { metadata.title }
    var symbolName: String { metadata.symbolName }
}
