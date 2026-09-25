//! Connections: game clients (W3GS over TCP) and the control port (one text line per command),
//! both turned into events for the one task that owns the game.

use flo_w3gs::net::W3GSStream;
use flo_w3gs::packet::Packet;
use std::net::SocketAddr;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, oneshot};

#[derive(Debug)]
pub enum Event {
    Connected { conn: u32, tx: mpsc::UnboundedSender<Packet>, local: SocketAddr },
    Received { conn: u32, packet: Packet },
    Closed { conn: u32 },
    Control { line: String, reply: oneshot::Sender<String> },
}

/// Pumps one client: what it sends becomes events, what the game sends it goes out.
pub async fn serve_client(conn: u32, mut stream: W3GSStream, events: mpsc::UnboundedSender<Event>) {
    let (tx, mut rx) = mpsc::unbounded_channel::<Packet>();
    events.send(Event::Connected { conn, tx, local: stream.local_addr() }).ok();
    loop {
        tokio::select! {
            incoming = stream.recv() => match incoming {
                Ok(Some(packet)) => { events.send(Event::Received { conn, packet }).ok(); }
                Ok(None) => break,
                Err(e) => { tracing::warn!("connection {}: {}", conn, e); break; }
            },
            outgoing = rx.recv() => match outgoing {
                Some(packet) => if let Err(e) = stream.send(packet).await {
                    tracing::warn!("send to connection {}: {}", conn, e);
                    break;
                },
                None => break,
            },
        }
    }
    events.send(Event::Closed { conn }).ok();
}

/// The control port: loopback only, a line in, a line back.
pub async fn serve_control(port: u16, events: mpsc::UnboundedSender<Event>) {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.expect("control port");
    tracing::info!("control on 127.0.0.1:{}", port);
    loop {
        let Ok((socket, _)) = listener.accept().await else { continue };
        let events = events.clone();
        tokio::spawn(async move {
            let (read, mut write) = socket.into_split();
            let mut lines = BufReader::new(read).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let (reply, answer) = oneshot::channel();
                events.send(Event::Control { line, reply }).ok();
                let text = answer.await.unwrap_or_else(|_| "host gone".into());
                if write.write_all(format!("{}\n", text).as_bytes()).await.is_err() {
                    break;
                }
            }
        });
    }
}
