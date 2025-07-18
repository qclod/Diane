# diane_gui.py
import tkinter as tk
from tkinter import scrolledtext, font
import queue

class DianeGUI:
    def __init__(self, root, backend_queue, ui_queue):
        self.root = root
        self.backend_queue = backend_queue
        self.ui_queue = ui_queue
        
        self.current_app_state = "idle"

        self.root.title("Diane AI")
        
        self.root.minsize(650, 450)
        self.root.geometry("650x450")
        
        self.root.configure(bg="#2b2b2b")

        self.mono_font = font.Font(family="Consolas", size=11)
        self.mono_font_bold = font.Font(family="Consolas", size=11, weight="bold")
        self.ui_font = font.Font(family="Segoe UI", size=9, weight="bold")
        self.status_font = font.Font(family="Consolas", size=10)
        self.hotkey_font = font.Font(family="Consolas", size=9)

        # --- GEOMETRY MANAGEMENT ---
        bottom_frame = tk.Frame(self.root, bg="#2b2b2b")
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        main_frame = tk.Frame(self.root, bg="#2b2b2b")
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=(5,0))

        # --- Main Frame Content (Top) ---
        self.history_text = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, font=self.mono_font, bg="#f0f0f0", padx=10, pady=2, relief=tk.FLAT, borderwidth=0)
        self.history_text.pack(expand=True, fill='both')
        
        self.history_text.tag_configure("bold", font=self.mono_font_bold)
        
        self.history_text.config(state='disabled')
        
        # --- Bottom Frame Content ---
        entry_frame = tk.Frame(bottom_frame, bg="#d0d0d0")
        entry_frame.pack(side=tk.TOP, fill=tk.X, pady=(2,0)) 
        self.entry_label = tk.Label(entry_frame, text="Input:", font=self.mono_font_bold, bg="#d0d0d0", fg="#555555")
        self.entry_label.pack(side='left', padx=(10, 5), pady=4)

        self.live_text_entry = tk.Text(entry_frame, font=self.mono_font, bg="#d0d0d0", fg="#1a1a1a", relief=tk.FLAT, 
                                       height=1, wrap=tk.WORD, padx=2, pady=2, insertbackground="#1a1a1a")
        self.live_text_entry.pack(side='left', fill='x', expand=True, pady=4)
        
        self.live_text_entry.bind("<Control-Return>", self.send_input_from_event)
        self.live_text_entry.bind("<KeyRelease>", self._adjust_text_height)
        
        self.status_bar = tk.Label(bottom_frame, text="Initializing...", bd=0, relief=tk.FLAT, anchor=tk.W, font=self.status_font, bg="#4a4a4a", fg="#ffffff", padx=10, pady=4)
        self.status_bar.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))

        button_grid = tk.Frame(bottom_frame, bg="#2b2b2b")
        button_grid.pack(side=tk.TOP, fill=tk.X, pady=(5,0))
        button_grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="button_group")

        self.btn_voice_lite = self.create_button(button_grid, "Voice (Lite)", lambda: self.send_to_backend('start_voice', 'lite'), 0, 0, self.ui_font)
        self.btn_voice_flash = self.create_button(button_grid, "Voice (Flash)", lambda: self.send_to_backend('start_voice', 'flash'), 0, 1, self.ui_font)
        self.btn_voice_pro = self.create_button(button_grid, "Voice (Pro)", lambda: self.send_to_backend('start_voice', 'pro'), 0, 2, self.ui_font)
        
        self.btn_text_lite = self.create_button(button_grid, "Text (Lite)", lambda: self.send_to_backend('start_text', 'lite'), 1, 0, self.ui_font)
        self.btn_text_flash = self.create_button(button_grid, "Text (Flash)", lambda: self.send_to_backend('start_text', 'flash'), 1, 1, self.ui_font)
        self.btn_text_pro = self.create_button(button_grid, "Text (Pro)", lambda: self.send_to_backend('start_text', 'pro'), 1, 2, self.ui_font)

        self.btn_send = self.create_button(button_grid, "Send", self.send_input, 2, 0, self.ui_font, "#4a90e2", "#ffffff")
        self.btn_pause_resume = self.create_button(button_grid, "Pause/Resume", lambda: self.send_to_backend('toggle_pause_audio'), 2, 1, self.ui_font)
        self.btn_cancel = self.create_button(button_grid, "Cancel", lambda: self.send_to_backend('cancel_action'), 2, 2, self.ui_font, "#d0021b", "#ffffff")
        
        self.hotkey_label = tk.Label(bottom_frame, bd=0, relief=tk.FLAT, anchor=tk.CENTER, font=self.hotkey_font, bg="#3a3a3a", fg="#cccccc", justify=tk.CENTER)
        self.hotkey_label.pack(side=tk.BOTTOM, fill=tk.X, pady=(5,0))
        
        hotkey_text_lines = [
            "Activate Voice: Ctrl+Alt+[L/O/P] | Activate Text: Ctrl+Shift+[L/O/P]",
            "Send: Ctrl+Shift/Alt+M | Pause/Resume: Ctrl+Shift/Alt+I | Cancel: Ctrl+Shift/Alt+K"
        ]
        self.hotkey_label.config(text="\n".join(hotkey_text_lines))

        self.all_buttons = [ self.btn_voice_lite, self.btn_voice_flash, self.btn_voice_pro, self.btn_text_lite, self.btn_text_flash, self.btn_text_pro, self.btn_send, self.btn_cancel, self.btn_pause_resume ]
        self.activation_buttons = self.all_buttons[:6]

        self.disable_entry_box()
        self.root.after(100, self.process_queue)

    def _adjust_text_height(self, event=None):
        min_height = 1
        max_height = 5
        lines_tuple = self.live_text_entry.count("1.0", tk.END, "displaylines")
        line_count = lines_tuple[0] if lines_tuple else 0
        new_height = max(min_height, min(line_count, max_height))
        if new_height != self.live_text_entry.cget("height"):
            self.live_text_entry.config(height=new_height)

    def create_button(self, parent, text, command, row, col, font, bg="#555555", fg="#ffffff"):
        btn = tk.Button(parent, text=text, command=command, font=font, bg=bg, fg=fg, relief=tk.FLAT, padx=10, pady=5)
        btn.grid(row=row, column=col, sticky="ew", padx=2, pady=2)
        return btn

    def send_to_backend(self, command, data=None): self.backend_queue.put((command, data))
    def stop_listening(self): self.send_to_backend('stop_listening')

    def send_input_from_event(self, event):
        self.send_input()
        return "break"

    def send_input(self):
        user_input = self.live_text_entry.get("1.0", tk.END).strip()
        if self.live_text_entry['state'] == 'normal' or self.btn_send['state'] == 'normal':
            if user_input:
                self.send_to_backend('send_request', user_input)

    def process_queue(self):
        try:
            while True:
                message_type, content = self.ui_queue.get_nowait()
                if message_type == "history": self.add_to_history(content)
                elif message_type == "status": self.update_status(content)
                elif message_type == "ui_state": self.update_ui_state(content)
                elif message_type == "request_gui_input": self.send_input()
                elif message_type == "update_entry":
                    self.update_live_entry(content)
        except queue.Empty:
            self.root.after(100, self.process_queue)

    def add_to_history(self, text):
        self.history_text.config(state='normal')
        
        try:
            prefix, message = text.split(":", 1)
            prefix += ":"
            message = message.lstrip()
            
            self.history_text.insert(tk.END, prefix, "bold")
            self.history_text.insert(tk.END, " " + message + "\n\n")

        except ValueError:
            self.history_text.insert(tk.END, text + "\n\n")

        self.history_text.config(state='disabled')
        self.history_text.see(tk.END)

    def update_status(self, text): self.status_bar.config(text=text)

    def update_live_entry(self, text):
        if self.current_app_state != "listening":
            return
            
        self.live_text_entry.config(state='normal')
        self.live_text_entry.delete("1.0", tk.END)
        self.live_text_entry.insert("1.0", text)
        self.live_text_entry.config(state='disabled')
        self.live_text_entry.see(tk.END)
        self._adjust_text_height()

    def enable_text_mode(self):
        self.entry_label.config(text="Input:")
        self.live_text_entry.config(state='normal', fg="#1a1a1a")
        self.live_text_entry.delete("1.0", tk.END)
        self._adjust_text_height()
        self.live_text_entry.focus_set()

    def disable_entry_box(self):
        self.live_text_entry.config(state='normal')
        self.live_text_entry.delete("1.0", tk.END)
        self.live_text_entry.config(state='disabled', fg="#555555")
        self._adjust_text_height()
        self.entry_label.config(text="Input:")
        
    def update_ui_state(self, state_config):
        self.current_app_state = state_config.get('app_state', 'idle')

        if self.current_app_state == 'listening':
            self.live_text_entry.config(state='disabled', fg="#1a1a1a")
        elif state_config.get('entry_box_enabled', False):
            self.enable_text_mode()
        else:
            self.disable_entry_box()

        for btn in self.activation_buttons: btn.config(state=state_config.get('activations', 'disabled'))
        self.btn_send.config(state=state_config.get('send', 'disabled'))
        self.btn_cancel.config(state=state_config.get('cancel', 'disabled'))
        self.btn_pause_resume.config(state=state_config.get('pause_resume', 'disabled'))
        
        self.btn_send.config(text=state_config.get('send_text', 'Send'), command=self.send_input if state_config.get('send_command') == 'send' else self.stop_listening)
        self.btn_pause_resume.config(text=state_config.get('pause_resume_text', 'Pause/Resume'))
