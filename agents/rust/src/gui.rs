//! The enrollment window: one text box, one button.
//!
//! Deliberately a plain Win32 window rather than a UI framework -- it keeps the
//! installer a single ~1 MB .exe with nothing to install first, which is the whole
//! point of handing someone a key and an .exe.

use std::cell::RefCell;
use std::ffi::c_void;
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    CreateFontW, DeleteObject, GetSysColorBrush, InvalidateRect, SetBkMode, SetTextColor,
    UpdateWindow, COLOR_BTNFACE, HDC, HFONT, TRANSPARENT,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Controls::EM_SETLIMITTEXT;
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, SetFocus};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const ID_EDIT: usize = 101;
const ID_ENROLL: usize = 102;
const ID_CLOSE: usize = 103;
const ID_STATUS: usize = 104;
const WM_ENROLL_DONE: u32 = WM_APP + 1;

const INK: COLORREF = rgb(0x3a, 0x3a, 0x3a);
const OK_INK: COLORREF = rgb(0x0d, 0x75, 0x2b);
const BAD_INK: COLORREF = rgb(0xa8, 0x00, 0x14);

const fn rgb(r: u8, g: u8, b: u8) -> COLORREF {
    r as u32 | ((g as u32) << 8) | ((b as u32) << 16)
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

struct Ui {
    edit: HWND,
    enroll_btn: HWND,
    close_btn: HWND,
    status: HWND,
    fonts: Vec<HFONT>,
    status_color: COLORREF,
    busy: bool,
    enrolled: bool,
}

thread_local! {
    static UI: RefCell<Option<Ui>> = const { RefCell::new(None) };
}

/// Filled by the worker thread, read by the UI thread when WM_ENROLL_DONE arrives.
static RESULT: Mutex<Option<Result<String, String>>> = Mutex::new(None);

// ---------------- helpers ----------------
unsafe fn set_text(h: HWND, s: &str) {
    SetWindowTextW(h, wide(s).as_ptr());
}

unsafe fn set_status(ui: &mut Ui, text: &str, color: COLORREF) {
    ui.status_color = color;
    set_text(ui.status, text);
    // Repaint so the new colour is picked up by WM_CTLCOLORSTATIC.
    InvalidateRect(ui.status, std::ptr::null(), 1);
}

unsafe fn edit_text(h: HWND) -> String {
    let mut buf = [0u16; 64];
    let n = GetWindowTextW(h, buf.as_mut_ptr(), buf.len() as i32);
    String::from_utf16_lossy(&buf[..n.max(0) as usize])
}

#[allow(clippy::too_many_arguments)]
unsafe fn control(parent: HWND, class: &str, text: &str, style: u32, x: i32, y: i32, w: i32,
                  h: i32, id: usize, font: HFONT) -> HWND {
    let hwnd = CreateWindowExW(
        0,
        wide(class).as_ptr(),
        wide(text).as_ptr(),
        WS_CHILD | WS_VISIBLE | style,
        x, y, w, h,
        parent,
        id as *mut c_void,
        GetModuleHandleW(std::ptr::null()),
        std::ptr::null(),
    );
    SendMessageW(hwnd, WM_SETFONT, font as WPARAM, 1);
    hwnd
}

unsafe fn font(dpi: u32, points: i32, weight: i32, face: &str) -> HFONT {
    CreateFontW(
        -(points * dpi as i32 / 72), 0, 0, 0, weight,
        0, 0, 0,
        1,  // DEFAULT_CHARSET
        0,  // OUT_DEFAULT_PRECIS
        0,  // CLIP_DEFAULT_PRECIS
        5,  // CLEARTYPE_QUALITY
        0,  // DEFAULT_PITCH | FF_DONTCARE
        wide(face).as_ptr(),
    )
}

// ---------------- enrollment ----------------
unsafe fn start_enroll(hwnd: HWND) {
    let key = UI.with(|cell| {
        let mut borrow = cell.borrow_mut();
        let Some(ui) = borrow.as_mut() else { return None };
        if ui.busy || ui.enrolled {
            return None;
        }
        let raw = edit_text(ui.edit);
        match crate::normalize_key(&raw) {
            Some(k) => {
                ui.busy = true;
                EnableWindow(ui.enroll_btn, 0);
                EnableWindow(ui.edit, 0);
                set_status(ui, "Enrolling this device...", INK);
                Some(k)
            }
            None => {
                set_status(ui,
                    "That doesn't look like an enrollment key.\nKeys look like TSSM-4K7P-9XQ2.",
                    BAD_INK);
                None
            }
        }
    });

    let Some(key) = key else { return };
    let target = hwnd as isize;
    std::thread::spawn(move || {
        let outcome = crate::enroll_and_install(&key);
        if let Ok(mut slot) = RESULT.lock() {
            *slot = Some(outcome);
        }
        unsafe { PostMessageW(target as HWND, WM_ENROLL_DONE, 0, 0) };
    });
}

unsafe fn finish_enroll() {
    let outcome = RESULT.lock().ok().and_then(|mut s| s.take());
    UI.with(|cell| {
        let mut borrow = cell.borrow_mut();
        let Some(ui) = borrow.as_mut() else { return };
        ui.busy = false;
        match outcome {
            Some(Ok(_)) => {
                ui.enrolled = true;
                set_status(ui,
                    "This device is now managed by TSS MDM.\nYou can close this window.", OK_INK);
                ShowWindow(ui.enroll_btn, SW_HIDE);
                set_text(ui.close_btn, "Close");
                EnableWindow(ui.close_btn, 1);
                SetFocus(ui.close_btn);
            }
            Some(Err(e)) => {
                set_status(ui, &e, BAD_INK);
                EnableWindow(ui.enroll_btn, 1);
                EnableWindow(ui.edit, 1);
                SetFocus(ui.edit);
            }
            None => {
                set_status(ui, "Enrollment did not complete. Try again.", BAD_INK);
                EnableWindow(ui.enroll_btn, 1);
                EnableWindow(ui.edit, 1);
            }
        }
    });
}

// ---------------- window ----------------
unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        WM_COMMAND => {
            let id = (wp & 0xFFFF) as usize;
            if id == ID_ENROLL {
                start_enroll(hwnd);
            } else if id == ID_CLOSE || id == IDCANCEL as usize {
                DestroyWindow(hwnd);
            }
            0
        }
        WM_ENROLL_DONE => {
            finish_enroll();
            0
        }
        WM_CTLCOLORSTATIC => {
            let hdc = wp as HDC;
            // try_borrow, not borrow: this can fire while a handler holds the state.
            let color = UI.with(|cell| {
                cell.try_borrow()
                    .ok()
                    .and_then(|ui| {
                        ui.as_ref().filter(|u| u.status == lp as HWND).map(|u| u.status_color)
                    })
                    .unwrap_or(INK)
            });
            SetTextColor(hdc, color);
            SetBkMode(hdc, TRANSPARENT as i32);
            GetSysColorBrush(COLOR_BTNFACE) as LRESULT
        }
        WM_CLOSE => {
            DestroyWindow(hwnd);
            0
        }
        WM_DESTROY => {
            UI.with(|cell| {
                if let Some(ui) = cell.borrow_mut().take() {
                    for f in ui.fonts {
                        DeleteObject(f as *mut c_void);
                    }
                }
            });
            PostQuitMessage(0);
            0
        }
        _ => DefWindowProcW(hwnd, msg, wp, lp),
    }
}

pub fn run() {
    unsafe {
        let instance = GetModuleHandleW(std::ptr::null());
        let class = wide("TssMdmEnroll");
        let wc = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(wndproc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: LoadIconW(std::ptr::null_mut(), IDI_APPLICATION),
            hCursor: LoadCursorW(std::ptr::null_mut(), IDC_ARROW),
            hbrBackground: GetSysColorBrush(COLOR_BTNFACE),
            lpszMenuName: std::ptr::null(),
            lpszClassName: class.as_ptr(),
        };
        if RegisterClassW(&wc) == 0 {
            return;
        }

        let style = WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
        let hwnd = CreateWindowExW(
            WS_EX_APPWINDOW | WS_EX_CONTROLPARENT,
            class.as_ptr(),
            wide("TSS MDM Enrollment").as_ptr(),
            style,
            CW_USEDEFAULT, CW_USEDEFAULT, 100, 100,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            instance,
            std::ptr::null(),
        );
        if hwnd.is_null() {
            return;
        }

        let dpi = {
            let d = GetDpiForWindow(hwnd);
            if d == 0 { 96 } else { d }
        };
        let s = |v: i32| v * dpi as i32 / 96;

        let body = font(dpi, 9, 400, "Segoe UI");
        let title = font(dpi, 12, 600, "Segoe UI");
        let keyfont = font(dpi, 14, 400, "Consolas");

        // Layout in 96-dpi units; `s` scales it to the real display.
        let (pad, width) = (20, 420);
        let inner = width - pad * 2;

        control(hwnd, "STATIC", "Enter your enrollment key",
                0, s(pad), s(18), s(inner), s(24), 0, title);
        control(hwnd, "STATIC",
                &format!("Your IT team gave you a key. This PC will be enrolled into\n{}.",
                         crate::server_host()),
                0, s(pad), s(46), s(inner), s(34), 0, body);

        let edit = control(hwnd, "EDIT", "",
                           WS_TABSTOP | WS_BORDER | ES_CENTER as u32 | ES_UPPERCASE as u32 | ES_AUTOHSCROLL as u32,
                           s(pad), s(88), s(inner), s(34), ID_EDIT, keyfont);
        SendMessageW(edit, EM_SETLIMITTEXT, 32, 0);

        let status = control(hwnd, "STATIC",
                             if crate::load_conf().is_some() {
                                 "This PC is already enrolled. A new key will re-enrol it."
                             } else {
                                 "Example: TSSM-4K7P-9XQ2"
                             },
                             0, s(pad), s(132), s(inner), s(40), ID_STATUS, body);

        let btn_y = s(186);
        let (bw, bh) = (s(104), s(32));
        let enroll_btn = control(hwnd, "BUTTON", "Enroll", WS_TABSTOP | BS_DEFPUSHBUTTON as u32,
                                 s(width - pad) - bw * 2 - s(8), btn_y, bw, bh, ID_ENROLL, body);
        let close_btn = control(hwnd, "BUTTON", "Cancel", WS_TABSTOP | BS_PUSHBUTTON as u32,
                                s(width - pad) - bw, btn_y, bw, bh, ID_CLOSE, body);

        UI.with(|cell| {
            *cell.borrow_mut() = Some(Ui {
                edit, enroll_btn, close_btn, status,
                fonts: vec![body, title, keyfont],
                status_color: INK,
                busy: false,
                enrolled: false,
            });
        });

        // Size the window around that layout, then centre it.
        let mut rect = RECT { left: 0, top: 0, right: s(width), bottom: s(234) };
        AdjustWindowRect(&mut rect, style, 0);
        let (w, h) = (rect.right - rect.left, rect.bottom - rect.top);
        let x = (GetSystemMetrics(SM_CXSCREEN) - w) / 2;
        let y = (GetSystemMetrics(SM_CYSCREEN) - h) / 2;
        SetWindowPos(hwnd, std::ptr::null_mut(), x, y.max(0), w, h, SWP_NOZORDER);

        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);
        SetFocus(edit);

        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            // Gives Tab navigation, Enter -> Enroll and Esc -> Cancel for free.
            if IsDialogMessageW(hwnd, &msg) == 0 {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }
}
