import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
import sqlite3
import os
import sys
import qrcode
import re
import webbrowser
import requests
import threading
from PIL import Image, ImageTk
from datetime import datetime
import json

# --- CONFIGURATION & CONSTANTS ---
APP_NAME = "LinkForge"
VERSION = "1.0.0"
DB_NAME = "linkforge.db"
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".linkforge")

# UI Colors (Midnight Violet Premium Dark Theme)
COLORS = {
    "bg_main": "#0B0B0F",      # Deep midnight black
    "bg_sidebar": "#101016",   # Slightly lighter sidebar
    "bg_card": "#181820",      # Elevated card
    "accent": "#8B5CF6",       # Primary violet
    "accent_hover": "#A78BFA", # Lighter violet
    "accent_subtle": "#1E1B4B", # Very deep violet tint for active states
    "text_primary": "#F4F4F8", # Off-white for high contrast
    "text_secondary": "#A1A1B5", # Muted grey-blue
    "border": "#2B2B38",       # Subtle border
    "success": "#34D399",      # Emerald green
    "error": "#F87171"         # Soft coral red
}

FONTS = {
    "h1": ("Segoe UI", 24, "bold"),
    "h2": ("Segoe UI", 18, "bold"),
    "h3": ("Segoe UI", 14, "bold"),
    "body": ("Segoe UI", 10),
    "body_bold": ("Segoe UI", 10, "bold"),
    "small": ("Segoe UI", 9)
}

# --- DATABASE LAYER ---
class Database:
    def __init__(self):
        if not os.path.exists(APP_DATA_DIR):
            os.makedirs(APP_DATA_DIR)
        
        self.db_path = os.path.join(APP_DATA_DIR, DB_NAME)
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Table for links and QR records
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL, -- 'url' or 'qr'
                    title TEXT NOT NULL,
                    content TEXT NOT NULL, -- Original URL or QR Text
                    short_url TEXT,
                    qr_config TEXT, -- JSON string of colors/settings
                    is_favorite INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def add_record(self, rtype, title, content, short_url=None, qr_config=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO records (type, title, content, short_url, qr_config)
                VALUES (?, ?, ?, ?, ?)
            ''', (rtype, title, content, short_url, qr_config))
            conn.commit()
            return cursor.lastrowid

    def get_all_records(self, filter_type=None, search_query=None, favorites_only=False):
        query = "SELECT * FROM records WHERE 1=1"
        params = []
        
        if filter_type:
            query += " AND type = ?"
            params.append(filter_type)
        
        if search_query:
            query += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{search_query}%", f"%{search_query}%"])
            
        if favorites_only:
            query += " AND is_favorite = 1"
            
        query += " ORDER BY created_at DESC"
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    def toggle_favorite(self, record_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE records SET is_favorite = 1 - is_favorite WHERE id = ?", (record_id,))
            conn.commit()

    def delete_record(self, record_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM records WHERE id = ?", (record_id,))
            conn.commit()

    def get_stats(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            total_links = cursor.execute("SELECT COUNT(*) FROM records WHERE type='url'").fetchone()[0]
            total_qrs = cursor.execute("SELECT COUNT(*) FROM records WHERE type='qr'").fetchone()[0]
            total_favorites = cursor.execute("SELECT COUNT(*) FROM records WHERE is_favorite=1").fetchone()[0]
            return total_links, total_qrs, total_favorites

# --- UTILS ---
def validate_url(url):
    regex = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

def shorten_url_api(url, timeout=20):
    """
    Integrates spoo.me API to shorten the provided URL.
    Official API: https://spoo.me/
    """
    endpoint = "https://spoo.me/"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {"url": url}
    
    try:
        response = requests.post(endpoint, data=payload, headers=headers, timeout=timeout)
        
        if response.status_code == 429:
            return {"success": False, "error": "Rate limit exceeded. Please try again later."}
            
        response.raise_for_status()
        data = response.json()
        
        if "short_url" in data:
            return {"success": True, "short_url": data["short_url"]}
        else:
            return {"success": False, "error": "API response missing 'short_url'."}
            
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out. The spoo.me server might be busy."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Connection error. Please check your internet."}
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP Error: {e.response.status_code}"
        try:
            # Try to get error message from response body if available
            error_data = e.response.json()
            if "error" in error_data:
                error_msg += f" - {error_data['error']}"
        except:
            pass
        return {"success": False, "error": error_msg}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Network error: {str(e)}"}
    except json.JSONDecodeError:
        return {"success": False, "error": "Failed to parse API response."}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}

class SidebarButton(tk.Frame):
    def __init__(self, master, text, command, active=False):
        super().__init__(master, bg=COLORS["bg_sidebar"], height=50)
        self.pack_propagate(False)
        self.text = text
        self.command = command
        self.active = active
        
        self.indicator = tk.Frame(self, bg=COLORS["accent"] if active else COLORS["bg_sidebar"], width=4)
        self.indicator.pack(side="left", fill="y")
        
        bg_color = COLORS["accent_subtle"] if active else COLORS["bg_sidebar"]
        fg_color = COLORS["text_primary"] if active else COLORS["text_secondary"]
        
        self.configure(bg=bg_color)
        self.label = tk.Label(self, text=text, font=FONTS["body_bold"] if active else FONTS["body"], 
                              bg=bg_color, fg=fg_color,
                              padx=20)
        self.label.pack(side="left", fill="both", expand=True)
        
        for widget in (self, self.label):
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)
            widget.bind("<Button-1>", lambda e: self.command())
            
    def on_enter(self, e):
        if not self.active:
            self.configure(bg="#1A1A24")
            self.label.configure(bg="#1A1A24", fg=COLORS["text_primary"])
            
    def on_leave(self, e):
        if not self.active:
            self.configure(bg=COLORS["bg_sidebar"])
            self.label.configure(bg=COLORS["bg_sidebar"], fg=COLORS["text_secondary"])

    def set_active(self, active):
        self.active = active
        bg_color = COLORS["accent_subtle"] if active else COLORS["bg_sidebar"]
        fg_color = COLORS["text_primary"] if active else COLORS["text_secondary"]
        
        self.configure(bg=bg_color)
        self.indicator.configure(bg=COLORS["accent"] if active else COLORS["bg_sidebar"])
        self.label.configure(bg=bg_color, fg=fg_color, font=FONTS["body_bold"] if active else FONTS["body"])

class Page(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.setup_ui()
        
    def setup_ui(self):
        pass
    
    def on_show(self):
        pass

class DashboardPage(Page):
    def setup_ui(self):
        # Header
        header_frame = tk.Frame(self, bg=COLORS["bg_main"])
        header_frame.pack(fill="x", padx=40, pady=(40, 20))
        
        tk.Label(header_frame, text="Your links, beautifully organized.", font=FONTS["h1"],
                 bg=COLORS["bg_main"], fg=COLORS["text_primary"]).pack(anchor="w")
        tk.Label(header_frame, text="Create QR codes, manage your links, and keep everything in one place.", 
                 font=FONTS["h3"], bg=COLORS["bg_main"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 0))

        # Stats Row
        stats_row = tk.Frame(self, bg=COLORS["bg_main"])
        stats_row.pack(fill="x", padx=40, pady=20)
        
        self.stat_cards = {}
        stats = [("Total Saved Links", "0"), ("QR Codes Generated", "0"), ("Favorite Items", "0")]
        for label, val in stats:
            card = tk.Frame(stats_row, bg=COLORS["bg_card"], padx=25, pady=25, 
                            highlightthickness=1, highlightbackground=COLORS["border"])
            card.pack(side="left", expand=True, fill="both", padx=8)
            tk.Label(card, text=label, font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w")
            v_label = tk.Label(card, text=val, font=FONTS["h2"], bg=COLORS["bg_card"], fg=COLORS["accent"])
            v_label.pack(anchor="w", pady=(8, 0))
            self.stat_cards[label] = v_label

        # Action Cards
        action_row = tk.Frame(self, bg=COLORS["bg_main"])
        action_row.pack(fill="x", padx=40, pady=20)
        
        # Action Card 1: Shorten
        shorten_card = tk.Frame(action_row, bg=COLORS["bg_card"], padx=30, pady=30, 
                                highlightthickness=1, highlightbackground=COLORS["border"])
        shorten_card.pack(side="left", expand=True, fill="both", padx=(8, 16))
        tk.Label(shorten_card, text="Shorten a URL", font=FONTS["h3"], bg=COLORS["bg_card"], fg=COLORS["text_primary"]).pack(anchor="w")
        tk.Label(shorten_card, text="Turn long links into clean, shareable URLs.", 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(8, 20))
        btn1 = tk.Button(shorten_card, text="Get Started", command=lambda: self.app.show_page("Shortener"),
                  bg=COLORS["accent"], fg="white", bd=0, padx=20, pady=10, font=FONTS["body_bold"],
                  activebackground=COLORS["accent_hover"], activeforeground="white", cursor="hand2")
        btn1.pack(anchor="w")
        btn1.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["accent_hover"]))
        btn1.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["accent"]))

        # Action Card 2: QR Code
        qr_card = tk.Frame(action_row, bg=COLORS["bg_card"], padx=30, pady=30, 
                           highlightthickness=1, highlightbackground=COLORS["border"])
        qr_card.pack(side="left", expand=True, fill="both", padx=(8, 8))
        tk.Label(qr_card, text="Create a QR Code", font=FONTS["h3"], bg=COLORS["bg_card"], fg=COLORS["text_primary"]).pack(anchor="w")
        tk.Label(qr_card, text="Generate custom QR codes for URLs or text.", 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(8, 20))
        btn2 = tk.Button(qr_card, text="Open QR Studio", command=lambda: self.app.show_page("QRStudio"),
                  bg=COLORS["accent"], fg="white", bd=0, padx=20, pady=10, font=FONTS["body_bold"],
                  activebackground=COLORS["accent_hover"], activeforeground="white", cursor="hand2")
        btn2.pack(anchor="w")
        btn2.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["accent_hover"]))
        btn2.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["accent"]))

        # Recent Activity
        tk.Label(self, text="Recent Activity", font=FONTS["h3"], bg=COLORS["bg_main"], fg=COLORS["text_primary"]).pack(anchor="w", padx=40, pady=(40, 15))
        self.recent_frame = tk.Frame(self, bg=COLORS["bg_main"])
        self.recent_frame.pack(fill="both", expand=True, padx=40)

    def on_show(self):
        l, q, f = self.db.get_stats()
        self.stat_cards["Total Saved Links"].configure(text=str(l))
        self.stat_cards["QR Codes Generated"].configure(text=str(q))
        self.stat_cards["Favorite Items"].configure(text=str(f))
        
        self.refresh_recent()

    def refresh_recent(self):
        for widget in self.recent_frame.winfo_children():
            widget.destroy()
            
        records = self.db.get_all_records()[:5] # Get last 5
        if not records:
            tk.Label(self.recent_frame, text="No recent activity to show.", 
                     font=FONTS["body"], fg=COLORS["text_secondary"]).pack(pady=20)
            return
            
        for r in records:
            row = tk.Frame(self.recent_frame, bg=COLORS["bg_card"], padx=15, pady=10,
                           highlightthickness=1, highlightbackground=COLORS["border"])
            row.pack(fill="x", pady=2)
            
            icon = "🔗" if r[1] == 'url' else "📱"
            tk.Label(row, text=icon, bg=COLORS["bg_card"]).pack(side="left")
            tk.Label(row, text=r[2], font=FONTS["body_bold"], bg=COLORS["bg_card"]).pack(side="left", padx=15)
            tk.Label(row, text=r[3][:40] + ("..." if len(r[3]) > 40 else ""), 
                     font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(side="left", padx=15)
            
            btn_frame = tk.Frame(row, bg=COLORS["bg_card"])
            btn_frame.pack(side="right")
            
            if r[1] == 'url':
                tk.Button(btn_frame, text="Open", command=lambda u=r[3]: webbrowser.open(u),
                          bg=COLORS["bg_card"], fg=COLORS["accent"], bd=0, font=FONTS["small"]).pack(side="left", padx=5)
            
            tk.Button(btn_frame, text="Copy", command=lambda c=r[3]: self.copy_to_clipboard(c),
                      bg=COLORS["bg_card"], fg=COLORS["text_secondary"], bd=0, font=FONTS["small"]).pack(side="left", padx=5)

    def copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("Success", "Copied to clipboard!")

class LinkForgeApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1200x760")
        self.root.configure(bg=COLORS["bg_main"])
        
        self.db = Database()
        self.setup_styles()
        self.current_page = None
        self.pages = {}
        
        self._setup_layout()
        self.show_page("Dashboard")
        
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background=COLORS["bg_main"])
        style.configure("Sidebar.TFrame", background=COLORS["bg_sidebar"])
        style.configure("TLabel", background=COLORS["bg_main"], foreground=COLORS["text_primary"], font=FONTS["body"])
        
        # Modern Scrollbar
        style.configure("Vertical.TScrollbar", background=COLORS["bg_card"], bordercolor=COLORS["border"], 
                        arrowcolor=COLORS["text_secondary"], troughcolor=COLORS["bg_main"], relief="flat")
        
        # Combobox styling
        style.configure("TCombobox", fieldbackground=COLORS["bg_card"], background=COLORS["bg_card"], 
                        foreground=COLORS["text_primary"], bordercolor=COLORS["border"], arrowcolor=COLORS["text_secondary"])
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["bg_card"])], 
                  foreground=[("readonly", COLORS["text_primary"])])

    def _setup_layout(self):
        # Sidebar
        self.sidebar = tk.Frame(self.root, bg=COLORS["bg_sidebar"], width=260)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        
        # Logo
        tk.Label(self.sidebar, text="LinkForge", font=FONTS["h2"], 
                 bg=COLORS["bg_sidebar"], fg=COLORS["accent"], pady=30).pack()
        
        # Navigation
        self.nav_items = {}
        nav_configs = [
            ("Dashboard", "Dashboard"),
            ("URL Shortener", "Shortener"),
            ("QR Studio", "QRStudio"),
            ("My Library", "Library"),
            ("Settings", "Settings")
        ]
        
        for label, page_id in nav_configs:
            btn = SidebarButton(self.sidebar, label, lambda p=page_id: self.show_page(p))
            btn.pack(fill="x")
            self.nav_items[page_id] = btn
            
        # Status Indicator
        status_frame = tk.Frame(self.sidebar, bg=COLORS["bg_sidebar"], pady=20)
        status_frame.pack(side="bottom", fill="x")
        tk.Label(status_frame, text="● Offline Mode", font=FONTS["small"], 
                 bg=COLORS["bg_sidebar"], fg=COLORS["text_secondary"], padx=20).pack(side="left")

        # Content Area
        self.content_area = tk.Frame(self.root, bg=COLORS["bg_main"])
        self.content_area.pack(side="right", expand=True, fill="both")

    def show_page(self, page_id):
        if self.current_page:
            self.pages[self.current_page].pack_forget()
            self.nav_items[self.current_page].set_active(False)
            
        if page_id not in self.pages:
            if page_id == "Dashboard":
                self.pages[page_id] = DashboardPage(self.content_area, self)
            elif page_id == "Shortener":
                self.pages[page_id] = ShortenerPage(self.content_area, self)
            elif page_id == "QRStudio":
                self.pages[page_id] = QRStudioPage(self.content_area, self)
            elif page_id == "Library":
                self.pages[page_id] = LibraryPage(self.content_area, self)
            elif page_id == "Settings":
                self.pages[page_id] = SettingsPage(self.content_area, self)
        
        self.current_page = page_id
        self.pages[page_id].pack(expand=True, fill="both")
        self.pages[page_id].on_show()
        self.nav_items[page_id].set_active(True)

# Placeholder classes for other pages
class ShortenerPage(Page):
    def setup_ui(self):
        self.is_processing = False
        
        container = tk.Frame(self, bg=COLORS["bg_main"])
        container.pack(expand=True, fill="both", padx=40, pady=40)

        tk.Label(container, text="URL Shortener", font=FONTS["h2"]).pack(anchor="w")
        tk.Label(container, text="Create clean, shareable short links using CleanURI.", 
                 font=FONTS["body"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 30))

        # Input Card
        input_card = tk.Frame(container, bg=COLORS["bg_card"], padx=35, pady=35,
                              highlightthickness=1, highlightbackground=COLORS["border"])
        input_card.pack(fill="x")

        tk.Label(input_card, text="Long URL", font=FONTS["body_bold"], bg=COLORS["bg_card"], fg=COLORS["text_primary"]).pack(anchor="w")
        self.url_entry = tk.Entry(input_card, bg=COLORS["bg_main"], fg=COLORS["text_primary"],
                                  insertbackground=COLORS["text_primary"], highlightthickness=1, highlightbackground=COLORS["border"],
                                  font=FONTS["body"], bd=0)
        self.url_entry.pack(fill="x", pady=(15, 25), ipady=10)
        self.url_entry.insert(0, "https://example.com/your-long-url-here")

        btn_container = tk.Frame(input_card, bg=COLORS["bg_card"])
        btn_container.pack(fill="x")

        self.shorten_btn = tk.Button(btn_container, text="Shorten URL", command=self.on_shorten, 
                                     bg=COLORS["accent"], fg="white", font=FONTS["body_bold"], 
                                     padx=25, pady=12, bd=0, activebackground=COLORS["accent_hover"],
                                     activeforeground="white", cursor="hand2")
        self.shorten_btn.pack(side="left")
        self.shorten_btn.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["accent_hover"]))
        self.shorten_btn.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["accent"]))
        
        self.retry_btn = tk.Button(btn_container, text="Retry", command=self.on_shorten, 
                                   bg=COLORS["accent"], fg="white", font=FONTS["body_bold"], 
                                   padx=25, pady=12, bd=0, activebackground=COLORS["accent_hover"],
                                   activeforeground="white", cursor="hand2")
        self.retry_btn.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["accent_hover"]))
        self.retry_btn.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["accent"]))
        # retry_btn hidden initially

        tk.Button(btn_container, text="Clear", command=self.on_clear, 
                  bg=COLORS["bg_main"], fg=COLORS["text_secondary"], font=FONTS["body"], 
                  padx=20, pady=10, bd=0, activebackground=COLORS["border"],
                  activeforeground=COLORS["text_primary"], cursor="hand2").pack(side="left", padx=15)

        self.loading_label = tk.Label(btn_container, text="Processing...", font=FONTS["small"],
                                      bg=COLORS["bg_card"], fg=COLORS["accent"])
        # Hidden by default

        # Result Area
        self.result_frame = tk.Frame(container, bg=COLORS["bg_main"])
        self.result_frame.pack(fill="x", pady=40)

    def on_clear(self):
        self.url_entry.delete(0, tk.END)
        self.retry_btn.pack_forget()
        for widget in self.result_frame.winfo_children():
            widget.destroy()

    def on_shorten(self):
        if self.is_processing:
            return
            
        url = self.url_entry.get().strip()
        if not url or not validate_url(url):
            messagebox.showerror("Error", "Please enter a valid URL (include http:// or https://).")
            return

        self.is_processing = True
        self.shorten_btn.configure(state="disabled")
        self.retry_btn.pack_forget()
        self.loading_label.pack(side="left", padx=10)
        
        for widget in self.result_frame.winfo_children():
            widget.destroy()

        # Start background thread
        thread = threading.Thread(target=self.process_shortening, args=(url,))
        thread.daemon = True
        thread.start()

    def process_shortening(self, url):
        result = shorten_url_api(url)
        # Use root.after to update UI from thread
        self.app.root.after(0, self.handle_api_result, url, result)

    def handle_api_result(self, original_url, result):
        self.is_processing = False
        self.shorten_btn.configure(state="normal")
        self.loading_label.pack_forget()

        if result["success"]:
            short_url = result["short_url"]
            # Prevent duplicates by checking if original_url exists with same short_url
            # (Simplified check: just save new record)
            self.db.add_record('url', f"Short: {original_url[:20]}...", original_url, short_url=short_url)
            self.display_result(original_url, short_url)
            messagebox.showinfo("Success", "URL shortened and saved to library!")
        else:
            self.retry_btn.pack(side="left", padx=10)
            messagebox.showerror("API Error", result["error"])

    def display_result(self, original, short):
        res_card = tk.Frame(self.result_frame, bg=COLORS["bg_card"], padx=20, pady=20,
                             highlightthickness=1, highlightbackground=COLORS["border"])
        res_card.pack(fill="x")
        
        tk.Label(res_card, text="Original URL", font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w")
        tk.Label(res_card, text=original[:80] + ("..." if len(original) > 80 else ""), font=FONTS["body"], bg=COLORS["bg_card"]).pack(anchor="w", pady=(2, 10))

        tk.Label(res_card, text="Shortened URL", font=FONTS["body_bold"], bg=COLORS["bg_card"]).pack(anchor="w")
        
        short_row = tk.Frame(res_card, bg=COLORS["bg_card"])
        short_row.pack(fill="x", pady=10)
        
        tk.Label(short_row, text=short, font=FONTS["h3"], bg=COLORS["bg_card"], fg=COLORS["accent"]).pack(side="left")
        
        tk.Button(short_row, text="Generate QR", command=lambda: self.go_to_qr(short),
                  bg=COLORS["accent"], fg="white", bd=0, padx=10, pady=5, font=FONTS["small"]).pack(side="right", padx=5)
        tk.Button(short_row, text="Copy", command=lambda: self.app.copy_to_clipboard(short),
                  bg=COLORS["bg_card"], fg=COLORS["text_secondary"], bd=0, font=FONTS["small"]).pack(side="right", padx=5)
        tk.Button(short_row, text="Open", command=lambda: webbrowser.open(short),
                  bg=COLORS["bg_card"], fg=COLORS["text_secondary"], bd=0, font=FONTS["small"]).pack(side="right")

    def go_to_qr(self, short_url):
        """Navigate to QR Studio and pre-fill the short URL."""
        self.app.show_page("QRStudio")
        qr_page = self.pages.get("QRStudio")
        if qr_page:
            qr_page.mode_var.set("URL")
            qr_page.input_text.delete("1.0", tk.END)
            qr_page.input_text.insert("1.0", short_url)
            qr_page.generate_qr()

class QRStudioPage(Page):
    def setup_ui(self):
        self.qr_image = None
        self.fg_color = "#000000"
        self.bg_color = "#FFFFFF"
        self.current_qr_content = ""

        container = tk.Frame(self, bg=COLORS["bg_main"])
        container.pack(expand=True, fill="both", padx=40, pady=40)

        # Left Side: Inputs
        left_panel = tk.Frame(container, bg=COLORS["bg_main"])
        left_panel.pack(side="left", fill="both", expand=True, padx=(0, 20))

        tk.Label(left_panel, text="QR Studio", font=FONTS["h2"]).pack(anchor="w")
        tk.Label(left_panel, text="Generate high-quality QR codes for better scan reliability.", 
                 font=FONTS["body"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 20))

        # Mode Selector
        mode_frame = tk.Frame(left_panel, bg=COLORS["bg_main"])
        mode_frame.pack(fill="x", pady=10)
        self.mode_var = tk.StringVar(value="URL")
        tk.Radiobutton(mode_frame, text="URL", variable=self.mode_var, value="URL", 
                       bg=COLORS["bg_main"], fg=COLORS["text_primary"], selectcolor=COLORS["bg_card"],
                       activebackground=COLORS["bg_main"], activeforeground=COLORS["accent"]).pack(side="left")
        tk.Radiobutton(mode_frame, text="Plain Text", variable=self.mode_var, value="Text", 
                       bg=COLORS["bg_main"], fg=COLORS["text_primary"], selectcolor=COLORS["bg_card"],
                       activebackground=COLORS["bg_main"], activeforeground=COLORS["accent"]).pack(side="left", padx=20)

        # Input Area
        tk.Label(left_panel, text="Content", font=FONTS["body_bold"], bg=COLORS["bg_main"], fg=COLORS["text_primary"]).pack(anchor="w", pady=(10, 5))
        self.input_text = tk.Text(left_panel, height=5, bg=COLORS["bg_card"], fg=COLORS["text_primary"],
                                  insertbackground=COLORS["text_primary"], highlightthickness=1, highlightbackground=COLORS["border"],
                                  font=FONTS["body"], padx=15, pady=15, bd=0)
        self.input_text.pack(fill="x")
        self.input_text.bind("<KeyRelease>", self.on_input_change)

        # Customization
        tk.Label(left_panel, text="Customization", font=FONTS["body_bold"], bg=COLORS["bg_main"], fg=COLORS["text_primary"]).pack(anchor="w", pady=(25, 10))
        
        # Color Selection
        color_frame = tk.Frame(left_panel, bg=COLORS["bg_main"])
        color_frame.pack(fill="x")

        self.fg_btn = tk.Button(color_frame, text="Foreground (Black)", command=self.pick_fg, 
                                bg="#000000", fg="#FFFFFF", width=18, pady=5, bd=0, cursor="hand2")
        self.fg_btn.pack(side="left", padx=(0, 10))
        self.bg_btn = tk.Button(color_frame, text="Background (White)", command=self.pick_bg, 
                                bg="#FFFFFF", fg="#000000", width=18, pady=5, bd=0, cursor="hand2")
        self.bg_btn.pack(side="left")

        # Error Correction Level
        tk.Label(left_panel, text="Error Correction Level", font=FONTS["body_bold"], bg=COLORS["bg_main"], fg=COLORS["text_primary"]).pack(anchor="w", pady=(20, 10))
        self.ec_var = tk.StringVar(value="Medium (M)")
        ec_options = ["Low (L)", "Medium (M)", "Quartile (Q)", "High (H)"]
        self.ec_menu = ttk.Combobox(left_panel, textvariable=self.ec_var, values=ec_options, state="readonly", width=25)
        self.ec_menu.pack(anchor="w")
        self.ec_menu.bind("<<ComboboxSelected>>", self.on_input_change)

        # Action Buttons
        btn_frame = tk.Frame(left_panel, bg=COLORS["bg_main"])
        btn_frame.pack(fill="x", pady=40)
        
        gen_btn = tk.Button(btn_frame, text="Generate QR Code", command=self.generate_qr, 
                  bg=COLORS["accent"], fg="white", font=FONTS["body_bold"], padx=25, pady=12, bd=0,
                  activebackground=COLORS["accent_hover"], activeforeground="white", cursor="hand2")
        gen_btn.pack(side="left")
        gen_btn.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["accent_hover"]))
        gen_btn.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["accent"]))
        
        self.download_btn = tk.Button(btn_frame, text="Download PNG", command=self.download_qr, 
                                      state="disabled", bg=COLORS["bg_card"], fg=COLORS["text_primary"],
                                      font=FONTS["body_bold"], padx=25, pady=12, bd=0,
                                      activebackground=COLORS["border"], activeforeground=COLORS["text_primary"], cursor="hand2")
        self.download_btn.pack(side="left", padx=15)
        self.download_btn.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["border"]) if e.widget['state'] != 'disabled' else None)
        self.download_btn.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["bg_card"]) if e.widget['state'] != 'disabled' else None)

        # Right Side: Preview
        right_panel = tk.Frame(container, bg=COLORS["bg_card"], highlightthickness=1, highlightbackground=COLORS["border"])
        right_panel.pack(side="right", fill="both", expand=True)
        
        tk.Label(right_panel, text="Preview", font=FONTS["body_bold"], bg=COLORS["bg_card"]).pack(pady=20)
        
        self.preview_label = tk.Label(right_panel, bg=COLORS["bg_card"])
        self.preview_label.pack(expand=True)
        
        self.warning_label = tk.Label(right_panel, text="", font=FONTS["small"], fg=COLORS["error"], bg=COLORS["bg_card"])
        self.warning_label.pack(pady=10)

    def on_input_change(self, event=None):
        """Disable download button when input changes until regenerated."""
        self.download_btn.configure(state="disabled")

    def pick_fg(self):
        color = colorchooser.askcolor(initialcolor=self.fg_color)[1]
        if color:
            self.fg_color = color
            self.fg_btn.configure(bg=color, text=f"Foreground ({color})", 
                                  fg="white" if self.get_luminance(color) < 0.5 else "black")
            self.on_input_change()

    def pick_bg(self):
        color = colorchooser.askcolor(initialcolor=self.bg_color)[1]
        if color:
            self.bg_color = color
            self.bg_btn.configure(bg=color, text=f"Background ({color})",
                                  fg="white" if self.get_luminance(color) < 0.5 else "black")
            self.on_input_change()

    def get_luminance(self, hex_color):
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255

    def generate_qr(self):
        content = self.input_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showerror("Error", "Please enter some content.")
            return

        if self.mode_var.get() == "URL" and not validate_url(content):
            messagebox.showerror("Error", "Please enter a valid URL (include http:// or https://).")
            return

        # Check contrast
        lum_fg = self.get_luminance(self.fg_color)
        lum_bg = self.get_luminance(self.bg_color)
        if abs(lum_fg - lum_bg) < 0.4:
            self.warning_label.configure(text="Warning: Low contrast may affect scannability.")
        else:
            self.warning_label.configure(text="")

        try:
            # Map EC level
            ec_map = {
                "Low (L)": qrcode.constants.ERROR_CORRECT_L,
                "Medium (M)": qrcode.constants.ERROR_CORRECT_M,
                "Quartile (Q)": qrcode.constants.ERROR_CORRECT_Q,
                "High (H)": qrcode.constants.ERROR_CORRECT_H
            }
            ec_level = ec_map.get(self.ec_var.get(), qrcode.constants.ERROR_CORRECT_M)

            qr = qrcode.QRCode(
                version=None, # Auto size
                error_correction=ec_level,
                box_size=10, # Large box size for high res
                border=4,    # Standard quiet zone
            )
            qr.add_data(content)
            qr.make(fit=True)

            self.qr_image = qr.make_image(fill_color=self.fg_color, back_color=self.bg_color).convert('RGB')
            self.current_qr_content = content
            
            # Update Preview (Resize for display only)
            # Use LANCZOS for better scaling quality in preview
            preview_img = self.qr_image.copy()
            preview_img.thumbnail((250, 250), Image.Resampling.LANCZOS)
            self.tk_qr = ImageTk.PhotoImage(preview_img)
            self.preview_label.configure(image=self.tk_qr)
            
            self.download_btn.configure(state="normal")
            
            # Save to Library
            self.db.add_record('qr', f"QR: {content[:20]}...", content, 
                               qr_config=json.dumps({
                                   "fg": self.fg_color, 
                                   "bg": self.bg_color,
                                   "ec": self.ec_var.get()
                               }))
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not generate QR code: {e}")

    def download_qr(self):
        if not self.qr_image: return
        
        # Double check if input matches what was generated
        content = self.input_text.get("1.0", tk.END).strip()
        if content != self.current_qr_content:
            if not messagebox.askyesno("Confirm", "The input has changed since you generated the QR code. Save the previously generated one?"):
                return

        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")])
        if path:
            # Save the original high-res image (self.qr_image) not the preview
            self.qr_image.save(path, "PNG", dpi=(300, 300))
            messagebox.showinfo("Success", f"High-resolution QR code saved to {path}\nManual testing with Google Lens is recommended.")

class LibraryPage(Page):
    def setup_ui(self):
        container = tk.Frame(self, bg=COLORS["bg_main"])
        container.pack(expand=True, fill="both", padx=40, pady=40)

        header = tk.Frame(container, bg=COLORS["bg_main"])
        header.pack(fill="x", pady=(0, 20))
        tk.Label(header, text="My Library", font=FONTS["h2"]).pack(side="left")

        # Search and Filter
        controls = tk.Frame(container, bg=COLORS["bg_main"])
        controls.pack(fill="x", pady=(0, 20))
        
        tk.Label(controls, text="Search", font=FONTS["small"], fg=COLORS["text_secondary"]).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_library())
        self.search_entry = tk.Entry(controls, textvariable=self.search_var, bg=COLORS["bg_card"], 
                                     fg=COLORS["text_primary"], bd=0, highlightthickness=1, 
                                     highlightbackground=COLORS["border"], font=FONTS["body"],
                                     width=30)
        self.search_entry.pack(side="left", padx=10, ipady=5)

        tk.Label(controls, text="Type", font=FONTS["small"], fg=COLORS["text_secondary"]).pack(side="left", padx=(20, 0))
        self.type_filter = tk.StringVar(value="All")
        type_cb = ttk.Combobox(controls, textvariable=self.type_filter, values=["All", "URL", "QR"], width=10)
        type_cb.pack(side="left", padx=10)
        self.type_filter.trace_add("write", lambda *args: self.refresh_library())

        # List Area with Scrollbar
        self.list_container = tk.Frame(container, bg=COLORS["bg_main"])
        self.list_container.pack(expand=True, fill="both")

        self.canvas = tk.Canvas(self.list_container, bg=COLORS["bg_main"], highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.list_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=COLORS["bg_main"])

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw", width=860)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def on_show(self):
        self.refresh_library()

    def refresh_library(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        t_filter = self.type_filter.get().lower()
        if t_filter == "all": t_filter = None
        
        records = self.db.get_all_records(filter_type=t_filter, search_query=self.search_var.get())
        
        if not records:
            tk.Label(self.scrollable_frame, text="No items found in your library.", 
                     font=FONTS["body"], fg=COLORS["text_secondary"]).pack(pady=40)
            return

        for r in records:
            self.create_record_row(r)

    def create_record_row(self, r):
        # r structure: (id, type, title, content, short_url, qr_config, is_favorite, created_at)
        card = tk.Frame(self.scrollable_frame, bg=COLORS["bg_card"], padx=20, pady=15,
                        highlightthickness=1, highlightbackground=COLORS["border"])
        card.pack(fill="x", pady=5)

        icon = "🔗" if r[1] == 'url' else "📱"
        tk.Label(card, text=icon, font=FONTS["h3"], bg=COLORS["bg_card"]).pack(side="left")
        
        info = tk.Frame(card, bg=COLORS["bg_card"])
        info.pack(side="left", padx=20, fill="x", expand=True)
        
        tk.Label(info, text=r[2], font=FONTS["body_bold"], bg=COLORS["bg_card"]).pack(anchor="w")
        tk.Label(info, text=r[3][:60] + ("..." if len(r[3]) > 60 else ""), 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w")

        actions = tk.Frame(card, bg=COLORS["bg_card"])
        actions.pack(side="right")

        fav_icon = "★" if r[6] else "☆"
        tk.Button(actions, text=fav_icon, command=lambda rid=r[0]: self.toggle_fav(rid),
                  bg=COLORS["bg_card"], fg=COLORS["accent"], bd=0, font=FONTS["h3"]).pack(side="left", padx=5)
        
        if r[1] == 'url':
            tk.Button(actions, text="Open", command=lambda u=r[3]: webbrowser.open(u),
                      bg=COLORS["bg_card"], fg=COLORS["text_secondary"], bd=0, font=FONTS["small"]).pack(side="left", padx=5)
        
        tk.Button(actions, text="Delete", command=lambda rid=r[0]: self.delete_rec(rid),
                  bg=COLORS["bg_card"], fg=COLORS["error"], bd=0, font=FONTS["small"]).pack(side="left", padx=5)

    def toggle_fav(self, rid):
        self.db.toggle_favorite(rid)
        self.refresh_library()

    def delete_rec(self, rid):
        if messagebox.askyesno("Confirm", "Are you sure you want to delete this record?"):
            self.db.delete_record(rid)
            self.refresh_library()

class SettingsPage(Page):
    def setup_ui(self):
        self.container = tk.Frame(self, bg=COLORS["bg_main"])
        self.container.pack(expand=True, fill="both", padx=40, pady=40)

        tk.Label(self.container, text="Settings", font=FONTS["h2"]).pack(anchor="w")
        tk.Label(self.container, text="Manage application preferences and API configuration.", 
                 font=FONTS["body"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(5, 30))

        # API Section
        api_card = tk.Frame(self.container, bg=COLORS["bg_card"], padx=35, pady=35,
                            highlightthickness=1, highlightbackground=COLORS["border"])
        api_card.pack(fill="x")

        tk.Label(api_card, text="URL Shortening API", font=FONTS["body_bold"], bg=COLORS["bg_card"], fg=COLORS["text_primary"]).pack(anchor="w")
        
        status_frame = tk.Frame(api_card, bg=COLORS["bg_card"])
        status_frame.pack(fill="x", pady=15)
        tk.Label(status_frame, text="Status:", font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(side="left")
        tk.Label(status_frame, text="Active (spoo.me)", font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["success"]).pack(side="left", padx=10)

        tk.Label(api_card, text="The spoo.me API is currently active. No API key is required for basic shortening.", 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"], wraplength=700, justify="left").pack(anchor="w", pady=(10, 25))

        self.test_btn = tk.Button(api_card, text="Test Connection", command=self.test_api,
                                 bg=COLORS["bg_main"], fg=COLORS["text_primary"], font=FONTS["body"], 
                                 padx=20, pady=10, bd=0, activebackground=COLORS["border"],
                                 activeforeground=COLORS["text_primary"], cursor="hand2")
        self.test_btn.pack(anchor="w")
        self.test_btn.bind("<Enter>", lambda e: e.widget.configure(bg=COLORS["border"]))
        self.test_btn.bind("<Leave>", lambda e: e.widget.configure(bg=COLORS["bg_main"]))

    def test_api(self):
        """Simple test to verify spoo.me API is reachable."""
        def run_test():
            result = shorten_url_api("https://google.com")
            self.app.root.after(0, lambda: self.show_test_result(result))
            
        self.test_btn.configure(state="disabled", text="Testing...")
        threading.Thread(target=run_test, daemon=True).start()

    def show_test_result(self, result):
        self.test_btn.configure(state="normal", text="Test Connection")
        if result["success"]:
            messagebox.showinfo("API Test", "Connection successful! spoo.me is responding.")
        else:
            messagebox.showerror("API Test", f"Connection failed: {result['error']}")

        # Info Section
        info_card = tk.Frame(self.container, bg=COLORS["bg_card"], padx=30, pady=30,
                             highlightthickness=1, highlightbackground=COLORS["border"])
        info_card.pack(fill="x", pady=20)
        
        tk.Label(info_card, text=f"{APP_NAME} v{VERSION}", font=FONTS["body_bold"], bg=COLORS["bg_card"]).pack(anchor="w")
        tk.Label(info_card, text="Developed for student productivity and link management.", 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=5)
        
        tk.Label(info_card, text="© 2026 LinkForge Studio", 
                 font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(anchor="w", pady=(20, 0))

# --- TESTING ---
import unittest
import argparse

class TestLinkForgeQR(unittest.TestCase):
    def test_qr_generation(self):
        """Test the QR generation logic without GUI."""
        content = "https://example.com/test?param=value"
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        self.assertIsNotNone(img)
        self.assertEqual(img.size[0], img.size[1]) # Square aspect ratio

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args, unknown = parser.parse_known_args()
    
    if args.test:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestLinkForgeQR)
        unittest.TextTestRunner().run(suite)
    else:
        root = tk.Tk()
        app = LinkForgeApp(root)
        root.mainloop()
