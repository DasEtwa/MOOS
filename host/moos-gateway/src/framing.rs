use serde_json::Value;

pub const MAX_FRAME_BYTES: usize = 16 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FrameError {
    TooLarge,
    InvalidJson,
    InvalidType,
    Truncated,
}

#[derive(Debug)]
pub enum FrameResult {
    Frame(Value),
    Error(FrameError),
}

pub struct FrameDecoder {
    buffer: Vec<u8>,
    max_frame_bytes: usize,
    discarding_oversize: bool,
}

impl FrameDecoder {
    pub fn new() -> Self {
        Self::with_max_frame_bytes(MAX_FRAME_BYTES)
    }

    pub fn with_max_frame_bytes(max_frame_bytes: usize) -> Self {
        Self {
            buffer: Vec::new(),
            max_frame_bytes: max_frame_bytes.max(1),
            discarding_oversize: false,
        }
    }

    pub fn feed(&mut self, data: &[u8]) -> Vec<FrameResult> {
        let mut results = Vec::new();
        let mut offset = 0;
        while offset < data.len() {
            if self.discarding_oversize {
                if let Some(relative) = data[offset..].iter().position(|byte| *byte == b'\n') {
                    self.discarding_oversize = false;
                    offset += relative + 1;
                } else {
                    break;
                }
                continue;
            }

            if let Some(relative) = data[offset..].iter().position(|byte| *byte == b'\n') {
                let newline = offset + relative;
                self.buffer.extend_from_slice(&data[offset..newline]);
                offset = newline + 1;
                if self.buffer.len() > self.max_frame_bytes {
                    self.buffer.clear();
                    results.push(FrameResult::Error(FrameError::TooLarge));
                    continue;
                }
                results.push(decode_object(&self.buffer));
                self.buffer.clear();
            } else {
                self.buffer.extend_from_slice(&data[offset..]);
                if self.buffer.len() > self.max_frame_bytes {
                    self.buffer.clear();
                    self.discarding_oversize = true;
                    results.push(FrameResult::Error(FrameError::TooLarge));
                }
                break;
            }
        }
        results
    }

    pub fn finish(&mut self) -> Option<FrameResult> {
        if self.buffer.is_empty() && !self.discarding_oversize {
            None
        } else {
            self.buffer.clear();
            self.discarding_oversize = false;
            Some(FrameResult::Error(FrameError::Truncated))
        }
    }
}

impl Default for FrameDecoder {
    fn default() -> Self {
        Self::new()
    }
}

fn decode_object(encoded: &[u8]) -> FrameResult {
    match serde_json::from_slice::<Value>(encoded) {
        Ok(value) if value.is_object() => FrameResult::Frame(value),
        Ok(_) => FrameResult::Error(FrameError::InvalidType),
        Err(_) => FrameResult::Error(FrameError::InvalidJson),
    }
}

pub fn encode_frame(value: &Value) -> Result<Vec<u8>, String> {
    if !value.is_object() {
        return Err("frame must be a JSON object".to_owned());
    }
    let mut encoded = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    if encoded.len() > MAX_FRAME_BYTES {
        return Err("frame is too large".to_owned());
    }
    encoded.push(b'\n');
    Ok(encoded)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn fragmented_and_concatenated_frames_decode() {
        let mut decoder = FrameDecoder::new();
        assert!(decoder.feed(b"{\"a\":").is_empty());
        let frames = decoder.feed(b"1}\n{\"b\":2}\n");
        assert_eq!(frames.len(), 2);
        assert!(matches!(&frames[0], FrameResult::Frame(value) if value == &json!({"a": 1})));
        assert!(matches!(&frames[1], FrameResult::Frame(value) if value == &json!({"b": 2})));
    }

    #[test]
    fn exact_limit_is_allowed_and_oversize_is_bounded() {
        let mut exact = FrameDecoder::with_max_frame_bytes(4);
        assert!(matches!(
            exact.feed(b"{}  \n").as_slice(),
            [FrameResult::Frame(_)]
        ));
        let mut oversized = FrameDecoder::with_max_frame_bytes(4);
        assert!(matches!(
            oversized.feed(b"12345").as_slice(),
            [FrameResult::Error(FrameError::TooLarge)]
        ));
        assert!(matches!(
            oversized.feed(b"ignored\n{}\n").as_slice(),
            [FrameResult::Frame(_)]
        ));
    }

    #[test]
    fn truncated_frame_is_rejected() {
        let mut decoder = FrameDecoder::new();
        assert!(decoder.feed(b"{\"a\":1}").is_empty());
        assert!(matches!(
            decoder.finish(),
            Some(FrameResult::Error(FrameError::Truncated))
        ));
    }

    #[test]
    fn invalid_json_and_non_object_values_are_rejected() {
        let mut decoder = FrameDecoder::new();
        let results = decoder.feed(b"not-json\n[]\n");
        assert!(matches!(
            results.as_slice(),
            [
                FrameResult::Error(FrameError::InvalidJson),
                FrameResult::Error(FrameError::InvalidType)
            ]
        ));
    }
}
