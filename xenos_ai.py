"""
------------------------------------------------------------------------------------------------------------------------
ooooooo  ooooo                                             ooooooooo.       ooooo   ooooo     ooooo           .o.       
 `8888    d8'                                              `888   `Y88.     `888'   `888'     `888'          .888.      
   Y888..8P     .ooooo.  ooo. .oo.    .ooooo.   .oooo.o     888   .d88'      888     888       888          .8"888.     
    `8888'     d88' `88b `888P"Y88b  d88' `88b d88(  "8     888ooo88P'       888ooooo888       888         .8' `888.    
   .8PY888.    888ooo888  888   888  888   888 `"Y88b.      888              888     888       888        .88ooo8888.   
  d8'  `888b   888    .o  888   888  888   888 o.  )88b     888         .o.  888     888  .o.  888  .o.  .8'     `888.  
o888o  o88888o `Y8bod8P' o888o o888o `Y8bod8P' 8""888P'    o888o        Y8P o888o   o888o Y8P o888o Y8P o88o     o8888o 
v0.0.1

This is Xenos, a Python-driven localized version of LLaMa 3.2 with image processing,
PID hooking, and an always-on-top overlay for responses. It uses Ollama for the
LLM backend and PyAutoGUI for screenshots. The app list is filtered to exclude
system processes. (Mostly.)
-------------------------------------------------------------------------------------------------------------------------
"""



import tkinter as tk
from tkinter import ttk
import threading
import psutil
import pyautogui
import ollama
import io
import base64
import time
import win32gui
import win32con
import win32process

# ─── RAM Storage ───────────────────────────────────────────
current_screenshot = None
monitored_app = None
net_last = psutil.net_io_counters()
net_last_time = time.time()
conversation_history = []

# ─── System Process Filter ─────────────────────────────────
SYSTEM_PROCESSES = {
    "system", "system idle process", "registry", "memory compression",
    "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "taskeng.exe",
    "taskhostw.exe", "conhost.exe", "fontdrvhost.exe", "spoolsv.exe",
    "searchindexer.exe", "wuauclt.exe", "audiodg.exe", "dashost.exe",
    "sihost.exe", "ctfmon.exe", "rundll32.exe", "dllhost.exe",
    "msdtc.exe", "lsaiso.exe", "wlanext.exe", "unsecapp.exe",
    "wmiprvse.exe", "wmiapsrv.exe", "wbemcons.exe", "sppsvc.exe",
    "vmmem", "idle", "interrupts", "ntoskrnl.exe"
}

def is_system_process(name, pid):
    if pid <= 4:
        return True
    if name.lower() in SYSTEM_PROCESSES:
        return True
    hwnd = get_hwnd_from_pid(pid)
    if not hwnd:
        return True
    return False

# ─── Window Focus & Screenshot ─────────────────────────────
def get_hwnd_from_pid(pid):
    result = []
    def callback(hwnd, _):
        try:
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid and win32gui.IsWindowVisible(hwnd):
                result.append(hwnd)
        except:
            pass
    win32gui.EnumWindows(callback, None)
    return result[0] if result else None

def focus_and_screenshot(pid):
    global current_screenshot

    hwnd = get_hwnd_from_pid(pid)

    if hwnd:
        placement = win32gui.GetWindowPlacement(hwnd)
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.6)

            fg = win32gui.GetForegroundWindow()
            if fg != hwnd:
                win32gui.BringWindowToTop(hwnd)
                time.sleep(0.4)

            screenshot = pyautogui.screenshot()
            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG")
            current_screenshot = buffer.getvalue()
            buffer.close()

            set_status(f"Screenshot captured from {monitored_app['name']} ✅")
        finally:
            win32gui.ShowWindow(hwnd, placement[1])
            win32gui.SetWindowPlacement(hwnd, placement)
    else:
        set_status("Window not found, falling back to full screen capture...")
        screenshot = pyautogui.screenshot()
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        current_screenshot = buffer.getvalue()
        buffer.close()
        set_status("Fallback screenshot captured ✅")

# ─── Overlay Window ────────────────────────────────────────
overlay = None
overlay_txt = None  # global ref so we can write to it from anywhere

def show_overlay(text):
    global overlay, overlay_txt

    if overlay and tk.Toplevel.winfo_exists(overlay):
        overlay.destroy()

    overlay = tk.Toplevel(root)
    overlay.title("")
    overlay.geometry("500x420+20+20")
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.88)
    overlay.overrideredirect(True)

    overlay_bg = "#111111"
    overlay.configure(bg=overlay_bg)

    # ─── Drag Bar ───────────────────────────────────────────
    drag_bar = tk.Frame(overlay, bg="#1a1a1a", height=28)
    drag_bar.pack(fill=tk.X)

    tk.Label(drag_bar, text="Xenos P.H.I.A",
             bg="#1a1a1a", fg="#00ff99",
             font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=10)

    # screenshot status indicator
    shot_label = tk.Label(drag_bar, text="● LIVE",
                          bg="#1a1a1a", fg="#00ff99",
                          font=("Consolas", 8))
    shot_label.pack(side=tk.LEFT, padx=4)

    tk.Button(drag_bar, text="✕", bg="#1a1a1a", fg="#ff5555",
              bd=0, font=("Arial", 9), activebackground="#1a1a1a",
              activeforeground="#ff0000",
              command=overlay.destroy).pack(side=tk.RIGHT, padx=5)

    # new screenshot button in drag bar
    tk.Button(drag_bar, text="⟳ New Shot", bg="#1a1a1a", fg="#00ff99",
              bd=0, font=("Consolas", 8), activebackground="#1a1a1a",
              activeforeground="#00cc77",
              command=lambda: request_new_screenshot(shot_label)).pack(side=tk.RIGHT, padx=5)

    def start_drag(event):
        overlay._drag_x = event.x
        overlay._drag_y = event.y

    def do_drag(event):
        x = overlay.winfo_x() + event.x - overlay._drag_x
        y = overlay.winfo_y() + event.y - overlay._drag_y
        overlay.geometry(f"+{x}+{y}")

    drag_bar.bind("<ButtonPress-1>", start_drag)
    drag_bar.bind("<B1-Motion>", do_drag)

    # ─── Response Text ──────────────────────────────────────
    overlay_txt = tk.Text(overlay, bg=overlay_bg, fg="#ffffff",
                          font=("Arial", 10), wrap=tk.WORD,
                          padx=10, pady=10, bd=0, height=10)
    overlay_txt.pack(fill=tk.BOTH, expand=True)
    overlay_txt.insert(tk.END, text)
    overlay_txt.config(state=tk.DISABLED)

    # ─── Divider ────────────────────────────────────────────
    tk.Frame(overlay, bg="#00ff99", height=1).pack(fill=tk.X, padx=8)

    # ─── Input Bar ──────────────────────────────────────────
    input_frame = tk.Frame(overlay, bg="#1a1a1a")
    input_frame.pack(fill=tk.X, padx=8, pady=6)

    overlay_entry = tk.Entry(input_frame, bg="#2d2d2d", fg="#ffffff",
                              font=("Arial", 10),
                              insertbackground="#00ff99", bd=0)
    overlay_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=5)
    overlay_entry.focus_set()

    def overlay_send(event=None):
        question = overlay_entry.get().strip()
        if not question:
            return
        overlay_entry.delete(0, tk.END)

        overlay_txt.config(state=tk.NORMAL)
        overlay_txt.insert(tk.END, f"\n\nYou: {question}")
        overlay_txt.config(state=tk.DISABLED)
        overlay_txt.see(tk.END)

        def run():
            # reuse existing screenshot unless new one was requested
            if current_screenshot is None:
                focus_and_screenshot(monitored_app['pid'])
                root.after(0, lambda: shot_label.config(text="● LIVE"))
            else:
                root.after(0, lambda: shot_label.config(text="● CACHED"))

            answer = ask_llama(question)

            def update():
                overlay_txt.config(state=tk.NORMAL)
                overlay_txt.insert(tk.END, f"\n\nAI: {answer}")
                overlay_txt.config(state=tk.DISABLED)
                overlay_txt.see(tk.END)
                append_chat("You", question)
                append_chat("AI", answer)

            root.after(0, update)

        threading.Thread(target=run, daemon=True).start()

    overlay_entry.bind("<Return>", overlay_send)

    tk.Button(input_frame, text="Ask", bg="#00ff99", fg="#111111",
              bd=0, font=("Consolas", 9, "bold"), padx=10, pady=2,
              activebackground="#00cc77", activeforeground="#111111",
              command=overlay_send).pack(side=tk.RIGHT)

def request_new_screenshot(label=None):
    """Force a fresh screenshot on next question"""
    global current_screenshot
    current_screenshot = None
    if label:
        label.config(text="● NEW", fg="#ffaa00")
    set_status("Screenshot cleared — will capture fresh on next question.")

# ─── App List ──────────────────────────────────────────────
def get_running_apps():
    seen = set()
    apps = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info['name']
            pid  = proc.info['pid']
            if not name:
                continue
            if name in seen:
                continue
            if is_system_process(name, pid):
                continue
            seen.add(name)
            apps.append({'pid': pid, 'name': name})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return apps

# ─── AI ────────────────────────────────────────────────────
def ask_llama(question):
    global current_screenshot, conversation_history

    b64 = base64.b64encode(current_screenshot).decode("utf-8")

    # build message with image attached to current question
    conversation_history.append({
        "role": "user",
        "content": question,
        "images": [b64]
    })

    # keep history to last 10 exchanges so context doesnt balloon
    trimmed = conversation_history[-10:]

    response = ollama.chat(
        model="llama3.2-vision",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a sharp, concise AI assistant with vision. "
                    "You are analyzing screenshots of the user's active application. "
                    "Answer any question you are asked confidently, but admit when incapable."
                    "Answer questions about what you see clearly and directly. "
                    "Remember what was said earlier in this conversation. "
                    "Do not repeat yourself. Do not be verbose. However, Do NOT leave out details that are relevant to the user's question. "
                )
            }
        ] + trimmed
    )

    reply = response["message"]["content"]

    # store assistant reply in history
    conversation_history.append({
        "role": "assistant",
        "content": reply
    })

    return reply

# ─── System Stats ──────────────────────────────────────────
def get_ram_usage():
    mem = psutil.virtual_memory()
    used_gb = mem.used / (1024 ** 3)
    total_gb = mem.total / (1024 ** 3)
    return f"RAM: {used_gb:.1f}GB / {total_gb:.1f}GB ({mem.percent}%)"

def get_cpu_usage():
    return f"CPU: {psutil.cpu_percent()}%"

def get_network_usage():
    global net_last, net_last_time
    net_now = psutil.net_io_counters()
    now = time.time()
    elapsed = now - net_last_time

    sent_kb = (net_now.bytes_sent - net_last.bytes_sent) / 1024 / elapsed
    recv_kb = (net_now.bytes_recv - net_last.bytes_recv) / 1024 / elapsed

    net_last = net_now
    net_last_time = now

    return f"NET: ↑{sent_kb:.1f}KB/s ↓{recv_kb:.1f}KB/s"

def update_stats():
    ram_var.set(get_ram_usage())
    cpu_var.set(get_cpu_usage())
    net_var.set(get_network_usage())
    root.after(1000, update_stats)

# ─── UI Helpers ────────────────────────────────────────────
def set_status(msg):
    status_var.set(f"Status: {msg}")

def append_chat(speaker, msg):
    chat_box.config(state=tk.NORMAL)
    chat_box.insert(tk.END, f"{speaker}: {msg}\n\n")
    chat_box.see(tk.END)
    chat_box.config(state=tk.DISABLED)

# ─── Button Actions ────────────────────────────────────────
def hook_into_app():
    global monitored_app, conversation_history
    selected = app_listbox.curselection()
    if not selected:
        set_status("No app selected.")
        return
    monitored_app = apps[selected[0]]
    conversation_history = []  # fresh context on new hook
    set_status(f"Monitoring: {monitored_app['name']} (PID: {monitored_app['pid']})")
    append_chat("System", f"Hooked into {monitored_app['name']}")

def refresh_apps():
    global apps
    apps = get_running_apps()
    app_listbox.delete(0, tk.END)
    for app in apps:
        app_listbox.insert(tk.END, f"{app['name']}  (PID: {app['pid']})")
    set_status("App list refreshed.")

def on_send():
    global monitored_app
    question = ask_entry.get().strip()
    if not question:
        return
    if not monitored_app:
        set_status("Hook into an app first.")
        return

    ask_entry.delete(0, tk.END)
    append_chat("You", question)
    set_status("Taking screenshot...")

    def run():
        # only take new screenshot if we dont have one cached
        if current_screenshot is None:
            focus_and_screenshot(monitored_app['pid'])
        else:
            set_status("Using cached screenshot...")

        set_status("Analyzing...")
        answer = ask_llama(question)
        append_chat("AI", answer)
        root.after(0, lambda: show_overlay(answer))
        set_status("Ready.")

    threading.Thread(target=run, daemon=True).start()

# ─── Build UI ──────────────────────────────────────────────
root = tk.Tk()
root.title("Xenos AI")
root.geometry("620x750")

BG      = "#1e1e1e"
FG      = "#ffffff"
ACCENT  = "#00ff99"
BTN_BG  = "#2d2d2d"
DIM     = "#aaaaaa"

root.configure(bg=BG)

tk.Label(root, text="Running Applications",
         bg=BG, fg=FG, font=("Arial", 11, "bold")).pack(pady=(10,2))

app_listbox = tk.Listbox(root, height=8, bg=BTN_BG, fg=FG,
                          selectbackground=ACCENT, selectforeground="#111111",
                          font=("Arial", 10), bd=0)
app_listbox.pack(fill=tk.X, padx=10)

app_btn_frame = tk.Frame(root, bg=BG)
app_btn_frame.pack(fill=tk.X, padx=10, pady=5)

tk.Button(app_btn_frame, text="Refresh",
          bg=BTN_BG, fg=FG, bd=0,
          command=refresh_apps).pack(side=tk.LEFT, padx=2)
tk.Button(app_btn_frame, text="Hook Into App",
          bg=ACCENT, fg="#111111", bd=0,
          command=hook_into_app).pack(side=tk.LEFT, padx=2)
tk.Button(app_btn_frame, text="⟳ New Screenshot",
          bg=BTN_BG, fg=ACCENT, bd=0,
          command=request_new_screenshot).pack(side=tk.LEFT, padx=2)

status_var = tk.StringVar(value="Status: Ready.")
tk.Label(root, textvariable=status_var,
         bg=BG, fg=DIM, font=("Arial", 9)).pack(pady=2)

tk.Label(root, text="Chat", bg=BG, fg=FG,
         font=("Arial", 11, "bold")).pack(pady=(5,2))

chat_box = tk.Text(root, height=15, bg=BTN_BG, fg=FG,
                   font=("Arial", 10), state=tk.DISABLED,
                   wrap=tk.WORD, padx=8, pady=8, bd=0)
chat_box.pack(fill=tk.BOTH, expand=True, padx=10)

input_frame = tk.Frame(root, bg=BG)
input_frame.pack(fill=tk.X, padx=10, pady=5)

ask_entry = tk.Entry(input_frame, bg=BTN_BG, fg=FG,
                     font=("Arial", 11),
                     insertbackground=ACCENT, bd=0)
ask_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
ask_entry.bind("<Return>", lambda e: on_send())

tk.Button(input_frame, text="Send",
          bg=ACCENT, fg="#111111", bd=0,
          command=on_send).pack(side=tk.RIGHT)

# ─── Stats Bar ─────────────────────────────────────────────
stats_frame = tk.Frame(root, bg="#111111")
stats_frame.pack(fill=tk.X, side=tk.BOTTOM)

ram_var = tk.StringVar(value="RAM: ...")
cpu_var = tk.StringVar(value="CPU: ...")
net_var = tk.StringVar(value="NET: ...")

tk.Label(stats_frame, textvariable=ram_var,
         bg="#111111", fg="#00ff99",
         font=("Consolas", 8)).pack(side=tk.LEFT, padx=8, pady=4)

tk.Label(stats_frame, textvariable=cpu_var,
         bg="#111111", fg="#ffaa00",
         font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)

tk.Label(stats_frame, textvariable=net_var,
         bg="#111111", fg="#3a86ff",
         font=("Consolas", 8)).pack(side=tk.LEFT, padx=8)

# ─── Start ─────────────────────────────────────────────────
apps = []
refresh_apps()
update_stats()
root.mainloop()