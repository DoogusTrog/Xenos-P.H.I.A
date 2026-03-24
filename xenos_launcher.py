import os  
import sys
import tkinter as tk
import logging
import subprocess
import threading

from tkinter import ttk, messagebox, filedialog, simpledialog

# Setup logging
try:
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xenos_log.txt')
    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
except Exception as e:
    print(f"Failed to initialize logging: {e}")
    # Fallback to console logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

root = tk.Tk()
root.title("Xenos Launcher")
# main window keeps default size (reverted as requested)
root.geometry("800x600")  # width x height
root.resizable(True, True)  # x, y resizable

# Widget setup must happen before mainloop

def launch_xenos():
    try:
        # Path to the Xenos AI application script
        xenos_script_path = os.path.join(os.path.dirname(__file__), "xenos_ai.py")
        
        # Check if the Xenos script exists
        if not os.path.exists(xenos_script_path):
            messagebox.showerror("Error", f"Xenos AI application script not found at:\n{xenos_script_path}")
            return
        
        logging.info(f"Launching Xenos AI: {xenos_script_path}")
        
        # Launch the Xenos AI application in a separate thread to avoid blocking the GUI
        def run_xenos_app():
            try:
                # Run the Python Xenos AI application
                process = subprocess.Popen(
                    [sys.executable, xenos_script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=os.path.dirname(xenos_script_path)
                )
                
                logging.info(f"Xenos AI launched with PID: {process.pid}")
                
                # Wait for the process to complete (optional - could run in background)
                stdout, stderr = process.communicate()
                
                if stderr:
                    logging.error(f"Xenos AI error: {stderr}")
                
                logging.info(f"Xenos AI exited with code: {process.returncode}")
                
            except Exception as e:
                logging.error(f"Failed to launch Xenos AI: {e}")
                # Show error in a thread-safe way
                root.after(0, lambda: messagebox.showerror("Error", f"Failed to launch Xenos AI:\n{str(e)}"))
        
        # Start the Xenos AI application in a background thread
        xenos_thread = threading.Thread(target=run_xenos_app, daemon=True)
        xenos_thread.start()
        
        logging.info("Xenos AI launch initiated")
        messagebox.showinfo("Xenos", "Xenos AI launched successfully!")
        
    except Exception as e:
        logging.error(f"Failed to launch Xenos: {e}")
        messagebox.showerror("Error", f"Failed to launch Xenos:\n{str(e)}")

def select_option():
    selected = var.get()
    
    if selected == "option1":
        launch_xenos()
    elif selected == "option2":
        messagebox.showinfo("Xenos", "Exiting Xenos Launcher...")
        root.quit()  # Use root.quit() instead of sys.exit() for cleaner Tkinter shutdown

var = tk.StringVar(value="option1")

radio_button1 = ttk.Radiobutton(root, text="Launch Xenos AI", variable=var, value="option1")
radio_button1.pack(pady=10)

radio_button2 = ttk.Radiobutton(root, text="Exit Xenos Launcher", variable=var, value="option2")
radio_button2.pack(pady=10)

select_button = ttk.Button(root, text="Confirm Xenos Selection", command=select_option)
select_button.pack(pady=20)

root.mainloop()  # start event loop