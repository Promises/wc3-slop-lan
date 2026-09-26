//! A client for Warcraft III's menus: the game serves its menu page on a local port and talks to
//! it over a websocket at `/webui-socket/<guid>`; this connects to the same socket, sends what the
//! page would (`{"type":"webui","message":...,"payload":{}}`) and reads what the game says back.
//! Everything the game says reaches every connected socket, so the activator hears provider
//! changes whoever asked for them.

use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub struct Menus {
    stream: TcpStream,
    buffer: Vec<u8>,
    seed: u64,
}

impl Menus {
    pub fn connect(port: u16, guid: &str) -> io::Result<Menus> {
        let mut stream = TcpStream::connect(("127.0.0.1", port))?;
        stream.set_read_timeout(Some(Duration::from_secs(5)))?;
        let mut menus = Menus { stream: stream.try_clone()?, buffer: vec![], seed: seed() };
        let key = base64(&menus.random_bytes::<16>());
        // One write: the request arrives whole
        stream.write_all(
            format!(
                "GET /webui-socket/{guid} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
                 Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1:{port}\r\n\r\n"
            )
            .as_bytes(),
        )?;
        let mut reply = vec![];
        while !reply.windows(4).any(|w| w == b"\r\n\r\n") {
            let mut chunk = [0u8; 1024];
            let read = menus.stream.read(&mut chunk)?;
            if read == 0 {
                return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "the menus closed the connection"));
            }
            reply.extend_from_slice(&chunk[..read]);
        }
        let end = reply.windows(4).position(|w| w == b"\r\n\r\n").unwrap() + 4;
        if !reply.starts_with(b"HTTP/1.1 101") {
            let status = String::from_utf8_lossy(&reply[..reply.iter().position(|&b| b == b'\r').unwrap_or(0)]).into_owned();
            return Err(io::Error::new(io::ErrorKind::Other, format!("the menus refused the socket: {status}")));
        }
        menus.buffer = reply[end..].to_vec();
        Ok(menus)
    }

    /// Sends a message the way the menu page does.
    pub fn send(&mut self, message: &str) -> io::Result<()> {
        let text = serde_json::json!({"type": "webui", "message": message, "payload": {}}).to_string();
        self.frame(0x1, text.as_bytes())
    }

    /// The next message from the game within `timeout`, as JSON; None when nothing came.
    pub fn next(&mut self, timeout: Duration) -> io::Result<Option<serde_json::Value>> {
        let end = Instant::now() + timeout;
        loop {
            if let Some((opcode, payload)) = self.take_frame() {
                match opcode {
                    0x1 => {
                        if let Ok(value) = serde_json::from_slice(&payload) {
                            return Ok(Some(value));
                        }
                    }
                    0x8 => return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "the menus closed the socket")),
                    0x9 => self.frame(0xA, &payload)?,
                    _ => {}
                }
                continue;
            }
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Ok(None);
            }
            self.stream.set_read_timeout(Some(left.max(Duration::from_millis(10))))?;
            let mut chunk = [0u8; 65536];
            match self.stream.read(&mut chunk) {
                Ok(0) => return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "the menus closed the socket")),
                Ok(read) => self.buffer.extend_from_slice(&chunk[..read]),
                Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => return Ok(None),
                Err(e) => return Err(e),
            }
        }
    }

    /// Waits for a message of that type; returns its payload, or None if it did not come in time.
    pub fn wait_for(&mut self, kind: &str, timeout: Duration) -> io::Result<Option<serde_json::Value>> {
        let end = Instant::now() + timeout;
        while let Some(left) = end.checked_duration_since(Instant::now()) {
            match self.next(left)? {
                Some(message) if message["messageType"] == kind => return Ok(Some(message["payload"].clone())),
                Some(_) => continue,
                None => break,
            }
        }
        Ok(None)
    }

    fn take_frame(&mut self) -> Option<(u8, Vec<u8>)> {
        let b = &self.buffer;
        if b.len() < 2 {
            return None;
        }
        let masked = b[1] & 0x80 != 0;
        let (length, mut at) = match b[1] & 0x7f {
            126 if b.len() >= 4 => (u16::from_be_bytes([b[2], b[3]]) as usize, 4),
            127 if b.len() >= 10 => (u64::from_be_bytes(b[2..10].try_into().unwrap()) as usize, 10),
            126 | 127 => return None,
            n => (n as usize, 2),
        };
        let mask = if masked {
            if b.len() < at + 4 {
                return None;
            }
            at += 4;
            Some([b[at - 4], b[at - 3], b[at - 2], b[at - 1]])
        } else {
            None
        };
        if b.len() < at + length {
            return None;
        }
        let mut payload = b[at..at + length].to_vec();
        if let Some(mask) = mask {
            payload.iter_mut().enumerate().for_each(|(i, byte)| *byte ^= mask[i % 4]);
        }
        let opcode = b[0] & 0x0f;
        self.buffer.drain(..at + length);
        Some((opcode, payload))
    }

    fn frame(&mut self, opcode: u8, data: &[u8]) -> io::Result<()> {
        let mut out = vec![0x80 | opcode];
        match data.len() {
            n if n < 126 => out.push(0x80 | n as u8),
            n if n < 65536 => {
                out.push(0x80 | 126);
                out.extend_from_slice(&(n as u16).to_be_bytes());
            }
            n => {
                out.push(0x80 | 127);
                out.extend_from_slice(&(n as u64).to_be_bytes());
            }
        }
        let mask = self.random_bytes::<4>();
        out.extend_from_slice(&mask);
        out.extend(data.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
        self.stream.write_all(&out)
    }

    /// Masks and keys only have to be unpredictable to proxies in between, not secret
    fn random_bytes<const N: usize>(&mut self) -> [u8; N] {
        let mut out = [0u8; N];
        for byte in out.iter_mut() {
            self.seed ^= self.seed << 13;
            self.seed ^= self.seed >> 7;
            self.seed ^= self.seed << 17;
            *byte = self.seed as u8;
        }
        out
    }
}

fn seed() -> u64 {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(1);
    nanos ^ 0x9E37_79B9_7F4A_7C15 | 1
}

fn base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, &b)| n | (b as u32) << (16 - 8 * i));
        for i in 0..4 {
            out.push(if i <= chunk.len() { TABLE[(n >> (18 - 6 * i) & 0x3f) as usize] as char } else { '=' });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn base64_matches_the_standard() {
        assert_eq!(base64(b"hello world!!"), "aGVsbG8gd29ybGQhIQ==");
        assert_eq!(base64(&[0u8; 16]), "AAAAAAAAAAAAAAAAAAAAAA==");
    }

    #[test]
    fn talks_to_a_socket_like_the_menus() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = vec![];
            while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                let mut chunk = [0u8; 1024];
                let read = socket.read(&mut chunk).unwrap();
                request.extend_from_slice(&chunk[..read]);
            }
            assert!(String::from_utf8_lossy(&request).starts_with("GET /webui-socket/77 HTTP/1.1"));
            socket.write_all(b"HTTP/1.1 101 Switching Protocols\r\n\r\n").unwrap();
            // What the client sent, unmasked
            let mut head = [0u8; 2];
            socket.read_exact(&mut head).unwrap();
            let mut mask = [0u8; 4];
            socket.read_exact(&mut mask).unwrap();
            let mut data = vec![0u8; (head[1] & 0x7f) as usize];
            socket.read_exact(&mut data).unwrap();
            data.iter_mut().enumerate().for_each(|(i, b)| *b ^= mask[i % 4]);
            let reply = br#"{"messageType":"OnNetProviderChanged","payload":{"providerId":"LOOP"}}"#;
            socket.write_all(&[0x81, reply.len() as u8]).unwrap();
            socket.write_all(reply).unwrap();
            String::from_utf8(data).unwrap()
        });
        let mut menus = Menus::connect(port, "77").unwrap();
        menus.send("InitializeLocalNetProvider").unwrap();
        let payload = menus.wait_for("OnNetProviderChanged", Duration::from_secs(5)).unwrap().unwrap();
        assert_eq!(payload["providerId"], "LOOP");
        let sent: serde_json::Value = serde_json::from_str(&server.join().unwrap()).unwrap();
        assert_eq!(sent["message"], "InitializeLocalNetProvider");
        assert_eq!(sent["type"], "webui");
    }
}
