use std::error::Error;
use std::fmt::{self, Display, Formatter};

use serde_json::{Map, Value, json};

pub const PROTOCOL_VERSION: u64 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ControlOperation {
    Status,
    PersonalStatus,
    PersonalStart,
    PersonalStop,
    PersonalTerminalOpen,
}

impl ControlOperation {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Status => "status",
            Self::PersonalStatus => "personal.status",
            Self::PersonalStart => "personal.start",
            Self::PersonalStop => "personal.stop",
            Self::PersonalTerminalOpen => "personal.terminal.open",
        }
    }

    pub fn is_remotely_authorizable(self) -> bool {
        self == Self::Status
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ControlRequest {
    pub operation: ControlOperation,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtocolError {
    pub code: &'static str,
    pub message: &'static str,
}

impl ProtocolError {
    fn new(code: &'static str, message: &'static str) -> Self {
        Self { code, message }
    }
}

impl Display for ProtocolError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl Error for ProtocolError {}

fn object(value: &Value) -> Result<&Map<String, Value>, ProtocolError> {
    value
        .as_object()
        .ok_or_else(|| ProtocolError::new("invalid_request", "request must be one JSON object"))
}

fn require_version(object: &Map<String, Value>, code: &'static str) -> Result<(), ProtocolError> {
    if object.get("protocolVersion").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err(ProtocolError::new(
            code,
            "protocolVersion must be integer 1",
        ));
    }
    Ok(())
}

fn operation(value: &str) -> Option<ControlOperation> {
    match value {
        "status" => Some(ControlOperation::Status),
        "personal.status" => Some(ControlOperation::PersonalStatus),
        "personal.start" => Some(ControlOperation::PersonalStart),
        "personal.stop" => Some(ControlOperation::PersonalStop),
        "personal.terminal.open" => Some(ControlOperation::PersonalTerminalOpen),
        _ => None,
    }
}

pub fn parse_control_request(value: &Value) -> Result<ControlRequest, ProtocolError> {
    let object = object(value)?;
    require_version(object, "unsupported_protocol")?;
    if object
        .keys()
        .any(|key| key != "protocolVersion" && key != "operation")
    {
        return Err(ProtocolError::new(
            "invalid_request",
            "unknown request field",
        ));
    }
    let name = object
        .get("operation")
        .and_then(Value::as_str)
        .ok_or_else(|| ProtocolError::new("invalid_request", "operation must be a string"))?;
    let operation = operation(name)
        .ok_or_else(|| ProtocolError::new("unknown_operation", "operation is not supported"))?;
    Ok(ControlRequest { operation })
}

pub fn parse_control_response(
    value: &Value,
    expected_operation: Option<ControlOperation>,
) -> Result<(), ProtocolError> {
    let object = value.as_object().ok_or_else(|| {
        ProtocolError::new("invalid_response", "response must be one JSON object")
    })?;
    require_version(object, "unsupported_protocol")?;
    let ok = object
        .get("ok")
        .and_then(Value::as_bool)
        .ok_or_else(|| ProtocolError::new("invalid_response", "response ok must be boolean"))?;
    if !ok {
        let error = object
            .get("error")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                ProtocolError::new("invalid_response", "response requires an error object")
            })?;
        for field in ["code", "message"] {
            if error
                .get(field)
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
            {
                return Err(ProtocolError::new(
                    "invalid_response",
                    "error fields must be text",
                ));
            }
        }
        return Ok(());
    }

    let operation_name = object
        .get("operation")
        .and_then(Value::as_str)
        .ok_or_else(|| ProtocolError::new("invalid_response", "invalid response operation"))?;
    let actual = operation(operation_name)
        .ok_or_else(|| ProtocolError::new("invalid_response", "invalid response operation"))?;
    if expected_operation.is_some_and(|expected| expected != actual) {
        return Err(ProtocolError::new(
            "invalid_response",
            "response operation does not match request",
        ));
    }
    let events = object
        .get("events")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            ProtocolError::new("invalid_response", "response events must be object entries")
        })?;
    if events.iter().any(|event| !event.is_object()) {
        return Err(ProtocolError::new(
            "invalid_response",
            "response events must be object entries",
        ));
    }
    let data = object
        .get("data")
        .and_then(Value::as_object)
        .ok_or_else(|| ProtocolError::new("invalid_response", "response data must be an object"))?;
    if actual == ControlOperation::PersonalTerminalOpen {
        if data.get("channel").and_then(Value::as_str) != Some("serial-console") {
            return Err(ProtocolError::new(
                "invalid_response",
                "invalid terminal channel",
            ));
        }
        return Ok(());
    }
    let personal = data
        .get("personal")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            ProtocolError::new("invalid_response", "response requires Personal status")
        })?;
    if personal.get("identity").and_then(Value::as_str) != Some("personal") {
        return Err(ProtocolError::new(
            "invalid_response",
            "invalid Personal identity",
        ));
    }
    let state = personal.get("state").and_then(Value::as_str);
    if !matches!(
        state,
        Some("starting" | "running" | "stopping" | "stopped" | "failed" | "unknown")
    ) {
        return Err(ProtocolError::new(
            "invalid_response",
            "invalid Personal state",
        ));
    }
    if personal
        .get("result")
        .is_some_and(|result| !result.is_null() && !result.is_string())
    {
        return Err(ProtocolError::new(
            "invalid_response",
            "invalid Personal result",
        ));
    }
    Ok(())
}

pub fn make_control_error(code: &str, message: &str) -> Result<Value, ProtocolError> {
    if code.is_empty() || message.is_empty() {
        return Err(ProtocolError::new(
            "invalid_response",
            "error fields must be text",
        ));
    }
    let value = json!({
        "protocolVersion": PROTOCOL_VERSION,
        "ok": false,
        "error": {"code": code, "message": message},
    });
    parse_control_response(&value, None)?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_are_strict_and_typed() {
        assert_eq!(
            parse_control_request(&json!({"protocolVersion": 1, "operation": "status"})),
            Ok(ControlRequest {
                operation: ControlOperation::Status
            })
        );
        assert!(
            parse_control_request(&json!({"protocolVersion": true, "operation": "status"}))
                .is_err()
        );
        assert!(
            parse_control_request(
                &json!({"protocolVersion": 1, "operation": "status", "extra": 1})
            )
            .is_err()
        );
    }

    #[test]
    fn backend_response_requires_matching_operation() {
        let response = json!({
            "protocolVersion": 1,
            "ok": true,
            "operation": "status",
            "data": {"personal": {"identity": "personal", "state": "running", "result": "success"}},
            "events": [],
        });
        assert!(parse_control_response(&response, Some(ControlOperation::Status)).is_ok());
        assert!(parse_control_response(&response, Some(ControlOperation::PersonalStatus)).is_err());
    }
}
