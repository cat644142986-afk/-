use super::*;
use std::net::TcpListener;
use std::thread;

const ASSET_ID: &str = "ast_0123456789abcdef0123456789abcdef";

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn create(label: &str) -> Self {
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "product-atelier-{label}-{}-{sequence}",
            std::process::id()
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn serve_once(response: Vec<u8>) -> (u16, thread::JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = Vec::new();
        let mut byte = [0u8; 1];
        while !request.ends_with(b"\r\n\r\n") {
            assert_eq!(stream.read(&mut byte).unwrap(), 1);
            request.push(byte[0]);
            assert!(request.len() < MAX_HTTP_HEADER_BYTES);
        }
        stream.write_all(&response).unwrap();
        request
    });
    (port, handle)
}

fn response(status: &str, body: &[u8], declared_length: usize) -> Vec<u8> {
    let mut response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/octet-stream\r\nContent-Length: {declared_length}\r\nConnection: close\r\n\r\n"
    )
    .into_bytes();
    response.extend_from_slice(body);
    response
}

#[test]
fn validates_production_asset_ids_and_safe_names() {
    assert!(validate_asset_id(ASSET_ID).is_ok());
    for invalid in [
        "",
        "ast:0123456789abcdef0123456789abcdef",
        "ast_0123456789ABCDEF0123456789ABCDEF",
        "ast_0123456789abcdef0123456789abcdeg",
        "../ast_0123456789abcdef0123456789abcdef",
    ] {
        assert!(validate_asset_id(invalid).is_err(), "accepted {invalid}");
    }
    assert!(validate_suggested_name("商品视频.webm").is_ok());
    for invalid in [
        "",
        ".",
        "..",
        " ../video.webm",
        "folder/video.webm",
        "video?.webm",
    ] {
        assert!(
            validate_suggested_name(invalid).is_err(),
            "accepted {invalid}"
        );
    }
}

#[test]
fn streams_binary_from_exact_loopback_asset_endpoint() {
    let body: Vec<u8> = (0..200_000).map(|index| (index % 251) as u8).collect();
    let (port, server) = serve_once(response("200 OK", &body, body.len()));
    let mut exported = Vec::new();

    assert_eq!(
        download_asset_from_sidecar(port, ASSET_ID, &mut exported).unwrap(),
        body.len() as u64
    );
    assert_eq!(exported, body);

    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with(&format!(
        "GET /api/assets/{ASSET_ID}/content?download=true HTTP/1.1\r\n"
    )));
    assert!(request.contains(&format!("\r\nHost: 127.0.0.1:{port}\r\n")));
    assert!(request.contains("\r\nAccept-Encoding: identity\r\n"));
}

#[test]
fn refuses_redirects_and_non_success_statuses() {
    for (status, extra_header) in [
        ("302 Found", "Location: https://example.invalid/asset\r\n"),
        ("404 Not Found", ""),
    ] {
        let response = format!(
            "HTTP/1.1 {status}\r\n{extra_header}Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        .into_bytes();
        let (port, server) = serve_once(response);
        let error = download_asset_from_sidecar(port, ASSET_ID, &mut Vec::new()).unwrap_err();
        assert!(error.contains("ASSET_EXPORT_HTTP_STATUS"), "{error}");
        server.join().unwrap();
    }
}

#[test]
fn truncated_response_keeps_destination_and_removes_temporary_file() {
    let directory = TestDirectory::create("truncated-export");
    let target = directory.0.join("result.webm");
    std::fs::write(&target, b"existing-result").unwrap();
    let (port, server) = serve_once(response("200 OK", b"short", 100));

    let error = download_asset_to_path(port, ASSET_ID, &target).unwrap_err();
    assert!(error.contains("ASSET_EXPORT_TRUNCATED"), "{error}");
    assert_eq!(std::fs::read(&target).unwrap(), b"existing-result");
    assert_eq!(std::fs::read_dir(&directory.0).unwrap().count(), 1);
    server.join().unwrap();
}

#[test]
fn successful_export_atomically_replaces_existing_binary() {
    let directory = TestDirectory::create("successful-export");
    let target = directory.0.join("result.bin");
    std::fs::write(&target, b"old").unwrap();
    let body = b"\x00\x01\xffProduct Atelier binary\x00";
    let (port, server) = serve_once(response("200 OK", body, body.len()));

    assert_eq!(
        download_asset_to_path(port, ASSET_ID, &target).unwrap(),
        body.len() as u64
    );
    assert_eq!(std::fs::read(&target).unwrap(), body);
    assert_eq!(std::fs::read_dir(&directory.0).unwrap().count(), 1);
    server.join().unwrap();
}
