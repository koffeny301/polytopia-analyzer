//! Оболочка Polytopia Analyzer: нативное окно + локальный сервер (sidecar).
//!
//! Sidecar — это PyInstaller-сборка `polytopia-analyzer`, запущенная в режиме
//! `web --port <N> --no-browser`. Оболочка выбирает свободный порт, поднимает
//! sidecar, ждёт готовности `/api/health` и перенаправляет окно на сервер.
//! При закрытии приложения sidecar получает SIGTERM (и SIGKILL по таймауту).

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

const TRIPLE: &str = "x86_64-unknown-linux-gnu";
const SIDECAR_NAME: &str = "polytopia-analyzer";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);
const POLL_INTERVAL: Duration = Duration::from_millis(250);
const SPAWN_ATTEMPTS: u32 = 3;

struct SidecarChild(Mutex<Option<Child>>);

/// Свободный порт от ОС (слушатель сразу закрывается — порт может быть занят
/// другим процессом в промежутке, поэтому запусков несколько).
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

/// Где лежит бинарник sidecar: env-переопределение → каталог текущего exe
/// (имя без тройки и с тройкой) → каталог binaries/ рядом с exe (dev-сборка).
fn sidecar_path() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("POLYTOPIA_ANALYZER_BIN") {
        let p = PathBuf::from(p);
        if p.is_file() {
            return Some(p);
        }
    }
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    let candidates = [
        dir.join(SIDECAR_NAME),
        dir.join(format!("{SIDECAR_NAME}-{TRIPLE}")),
        dir.join("binaries").join(format!("{SIDECAR_NAME}-{TRIPLE}")),
    ];
    candidates.into_iter().find(|p| p.is_file())
}

/// Проверка готовности: сырой HTTP/1.0 GET без внешних crate'ов.
fn health_ok(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(400)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(400)));
    let req = "GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n";
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    let _ = s.read_to_string(&mut buf);
    buf.contains("\"ok\": true") || buf.contains("\"ok\":true")
}

/// Проксирует stdout/stderr sidecar'а в журнал оболочки (потоки-насосы).
fn pipe_logs(mut child: Child) -> Child {
    if let Some(out) = child.stdout.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(out).lines().map_while(Result::ok) {
                eprintln!("[sidecar] {line}");
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                eprintln!("[sidecar:err] {line}");
            }
        });
    }
    child
}

/// SIGTERM всей группе процессов, затем SIGKILL через 3 секунды.
/// Sidecar живёт в собственной сессии (setsid), поэтому группа = его pid.
fn terminate(child: &mut Child) {
    let pid = child.id() as i32;
    unsafe { libc::kill(-pid, libc::SIGTERM) };
    match child.wait_timeout(Duration::from_secs(3)) {
        Ok(_) => {}
        Err(_) => {
            unsafe { libc::kill(-pid, libc::SIGKILL) };
            let _ = child.wait();
        }
    }
}

trait WaitTimeout {
    fn wait_timeout(&mut self, dur: Duration) -> Result<(), ()>;
}

impl WaitTimeout for Child {
    fn wait_timeout(&mut self, dur: Duration) -> Result<(), ()> {
        let deadline = Instant::now() + dur;
        loop {
            match self.try_wait() {
                Ok(Some(_)) => return Ok(()),
                Ok(None) => {
                    if Instant::now() >= deadline {
                        return Err(());
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(_) => return Err(()),
            }
        }
    }
}

fn show_error(window: &tauri::WebviewWindow, msg: &str) {
    let escaped = msg.replace('\\', "\\\\").replace('\'', "\\'");
    let _ = window.eval(&format!("showAnalyzerError('{escaped}')"));
}

/// Поднимает sidecar и ждёт health; возвращает порт и процесс.
fn spawn_sidecar(bin: &std::path::Path) -> Result<(u16, Child), String> {
    let mut last_err = String::from("неизвестная ошибка");
    for _ in 0..SPAWN_ATTEMPTS {
        let port = free_port();
        let child = unsafe {
            Command::new(bin)
                .args(["web", "--port", &port.to_string(), "--no-browser"])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                // Новая сессия: группу можно погасить целиком; PR_SET_PDEATHSIG —
                // страховка: ядро пришлёт SIGTERM sidecar'у, если оболочка умрёт
                // любым способом (SIGKILL, крах, закрытие окна мимо RunEvent).
                .pre_exec(|| {
                    unsafe {
                        libc::setsid();
                        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                    }
                    Ok(())
                })
                .spawn()
        };
        match child {
            Ok(c) => {
                let mut c = pipe_logs(c);
                let deadline = Instant::now() + HEALTH_TIMEOUT;
                loop {
                    if health_ok(port) {
                        return Ok((port, c));
                    }
                    // Процесс умер, не дождавшись здоровья — пробуем заново.
                    if let Ok(Some(status)) = c.try_wait() {
                        last_err = format!("сервер завершился преждевременно ({status})");
                        break;
                    }
                    if Instant::now() >= deadline {
                        last_err = format!(
                            "сервер не ответил на /api/health за {} c (порт {port})",
                            HEALTH_TIMEOUT.as_secs()
                        );
                        terminate(&mut c);
                        break;
                    }
                    std::thread::sleep(POLL_INTERVAL);
                }
            }
            Err(e) => last_err = format!("не удалось запустить {bin:?}: {e}"),
        }
    }
    Err(last_err)
}

pub fn run() {
    tauri::Builder::default()
        .manage(SidecarChild(Mutex::new(None)))
        .setup(|app| {
            let window = app.get_webview_window("main")
                .ok_or("окно main не найдено")?;

            let bin = sidecar_path().ok_or_else(|| {
                "не найден бинарник polytopia-analyzer рядом с приложением".to_string()
            });

            match bin {
                Ok(bin) => match spawn_sidecar(&bin) {
                    Ok((port, child)) => {
                        *app.state::<SidecarChild>().0.lock().unwrap() = Some(child);
                        let _ = window.eval(&format!(
                            "location.replace('http://127.0.0.1:{port}/')"
                        ));
                    }
                    Err(msg) => show_error(&window, &msg),
                },
                Err(msg) => show_error(&window, &msg),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("ошибка инициализации Tauri")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) =
                    app_handle.state::<SidecarChild>().0.lock().unwrap().take()
                {
                    terminate(&mut child);
                }
            }
        });
}
