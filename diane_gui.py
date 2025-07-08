# diane_gui.py
import tkinter as tk
from tkinter import scrolledtext, font
import queue

class DianeGUI:
    def __init__(self, root, text_input_queue):
        self.root = root
        self.text_input_queue = text_input_queue
        self.root.title("Diane AI")
        self.root.geometry("750x550")
        self.root.configure(bg="#2b2b2b")
        self.root.minsize(650, 300)

        history_font = font.Font(family="Segoe UI", size=11)
        entry_font = font.Font(family="Segoe UI", size=10, weight="bold")
        status_font = font.Font(family="Consolas", size=10)
        hotkey_font = font.Font(family="Consolas", size=9)
        
        hotkey_text = "Voice: Ctrl+Alt+[L/O/P] -> Speak -> Esc  |  Text: Ctrl+Shift+[L/O/P] -> Type -> Enter"
        self.hotkey_label = tk.Label(self.root, text=hotkey_text, bd=0, relief=tk.FLAT, anchor=tk.CENTER, font=hotkey_font, bg="#3a3a3a", fg="#cccccc")
        self.hotkey_label.pack(side='bottom', fill='x')
        
        self.status_bar = tk.Label(self.root, text="Press a hotkey to start...", bd=0, relief=tk.FLAT, anchor=tk.W, font=status_font, bg="#4a4a4a", fg="#ffffff", padx=10)
        self.status_bar.pack(side='bottom', fill='x', pady=(5,0))
        
        entry_frame = tk.Frame(self.root, bg="#d0d0d0")
        entry_frame.pack(side='bottom', fill='x', padx=5, pady=5)
        self.entry_label = tk.Label(entry_frame, text="Live:", font=entry_font, bg="#d0d0d0", fg="#555555")
        self.entry_label.pack(side='left', padx=(10, 5))
        self.live_text_entry = tk.Entry(entry_frame, font=entry_font, bg="#d0d0d0", fg="#1a1a1a", relief=tk.FLAT, disabledbackground="#d0d0d0", disabledforeground="#555555")
        self.live_text_entry.pack(side='left', fill='x', expand=True, pady=5)
        self.live_text_entry.bind("<Return>", self.on_text_entry_submit)
        self.live_text_entry.bind("<Escape>", self.on_text_entry_cancel)
        
        self.history_text = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, font=history_font, bg="#f0f0f0", padx=10, pady=10, relief=tk.FLAT, borderwidth=0)
        self.history_text.pack(expand=True, fill='both', padx=5, pady=(5,0))
        self.history_text.config(state='disabled')
        
        self.ui_queue = queue.Queue()
        self.disable_entry_box()
        self.root.after(100, self.process_queue)

    def on_text_entry_submit(self, event=None):
        user_input = self.live_text_entry.get()
        if user_input:
            self.text_input_queue.put(user_input)
            self.disable_entry_box()
        return "break"

    def on_text_entry_cancel(self, event=None):
        self.text_input_queue.put("_CANCEL_")
        self.disable_entry_box()
        return "break"

    def process_queue(self):
        try:
            while True:
                message_type, content = self.ui_queue.get_nowait()
                if message_type == "history": self.add_to_history(content)
                elif message_type == "status": self.update_status(content)
                elif message_type == "live_transcript": self.update_live_transcript(content)
                elif message_type == "activate_text_input": self.enable_text_mode()
                elif message_type == "clear_entry": self.disable_entry_box()
        except queue.Empty:
            self.root.after(100, self.process_queue)

    def add_to_history(self, text):
        self.history_text.config(state='normal')
        self.history_text.insert(tk.END, text + "\n\n")
        self.history_text.config(state='disabled')
        self.history_text.see(tk.END)

    def update_status(self, text):
        self.status_bar.config(text=text)

    def update_live_transcript(self, text):
        self.entry_label.config(text="Live:")
        self.live_text_entry.config(state='normal')
        self.live_text_entry.delete(0, tk.END)
        self.live_text_entry.insert(0, text)
        self.live_text_entry.xview_moveto(1.0)
        self.live_text_entry.config(state='disabled')

    def enable_text_mode(self):
        self.entry_label.config(text="Text:")
        self.live_text_entry.config(state='normal')
        self.live_text_entry.delete(0, tk.END)
        self.live_text_entry.focus_set()

    def disable_entry_box(self):
        self.live_text_entry.delete(0, tk.END)
        self.live_text_entry.config(state='disabled')
        self.entry_label.config(text="Live:")

    def queue_history_update(self, text): self.ui_queue.put(("history", text))
    def queue_status_update(self, text): self.ui_queue.put(("status", text))
    def queue_live_transcript_update(self, text): self.ui_queue.put(("live_transcript", text))
    def queue_activate_text_input(self): self.ui_queue.put(("activate_text_input", None))
    def queue_clear_entry(self): self.ui_queue.put(("clear_entry", None))
