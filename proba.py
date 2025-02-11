import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import sqlite3
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import threading
import time
import logging
from datetime import datetime, timedelta
from ttkthemes import ThemedTk
import csv
from pathlib import Path
import json
import requests
from decimal import Decimal

# Konfiguracija logginga
logging.basicConfig(
    filename='trading_app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

class PartialCloseDialog(tk.Toplevel):
    def __init__(self, parent, max_quantity):
        super().__init__(parent)
        self.title("Zatvori dio pozicije")
        self.quantity = 0
        self.result = False
        
        # Make dialog modal
        self.transient(parent)
        self.grab_set()
        
        # Center the dialog
        window_width = 300
        window_height = 150
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.geometry(f"{window_width}x{window_height}+{x}+{y}")
        
        self.setup_ui(max_quantity)
        
    def setup_ui(self, max_quantity):
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text=f"Trenutna količina: {max_quantity}").pack(pady=5)
        ttk.Label(main_frame, text="Unesite količinu za zatvaranje:").pack(pady=5)
        
        self.quantity_var = tk.StringVar()
        quantity_entry = ttk.Entry(main_frame, textvariable=self.quantity_var)
        quantity_entry.pack(pady=5)
        
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Zatvori poziciju", 
                  command=lambda: self.validate_and_close(max_quantity)).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Odustani", 
                  command=self.cancel).pack(side=tk.LEFT, padx=5)
        
        quantity_entry.focus_set()
        self.bind('<Return>', lambda e: self.validate_and_close(max_quantity))
        self.bind('<Escape>', lambda e: self.cancel())
        
    def validate_and_close(self, max_quantity):
        try:
            quantity = int(self.quantity_var.get())
            if 0 < quantity <= max_quantity:
                self.quantity = quantity
                self.result = True
                self.destroy()
            else:
                messagebox.showerror("Greška", 
                    f"Unesite ispravnu količinu (1-{max_quantity})")
        except ValueError:
            messagebox.showerror("Greška", "Unesite ispravan broj")
            
    def cancel(self):
        self.result = False
        self.destroy()

class DatabaseManager:
    def __init__(self, db_path='trading_app.db'):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.connect()
        self.setup_database()

    def connect(self):
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.cursor = self.conn.cursor()
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            raise

    def setup_database(self):
        try:
            # Main portfolio table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    quantity INTEGER NOT NULL,
                    entry_date TEXT NOT NULL,
                    exit_date TEXT,
                    pnl REAL,
                    trade_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT,
                    original_quantity INTEGER
                )
            ''')

            # Trading statistics table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS trading_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total_trades INTEGER,
                    winning_trades INTEGER,
                    losing_trades INTEGER,
                    win_rate REAL,
                    profit_factor REAL,
                    average_win REAL,
                    average_loss REAL,
                    largest_win REAL,
                    largest_loss REAL,
                    total_pnl REAL
                )
            ''')

            # Trade journal table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    entry_date TEXT NOT NULL,
                    notes TEXT,
                    strategy TEXT,
                    market_conditions TEXT,
                    FOREIGN KEY (trade_id) REFERENCES portfolio (id)
                )
            ''')

            self.conn.commit()
        except Exception as e:
            logger.error(f"Database setup error: {e}")
            raise

    def execute_query(self, query, parameters=None):
        try:
            if parameters:
                self.cursor.execute(query, parameters)
            else:
                self.cursor.execute(query)
            self.conn.commit()
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Query execution error: {query} - {e}")
            self.conn.rollback()
            raise

    def close(self):
        if self.conn:
            self.conn.close()

class MarketDataManager:
    def __init__(self):
        self.cache = {}
        self.cache_timeout = 60  # sekunde

    def get_real_time_price(self, symbol):
        try:
            current_time = time.time()
            if symbol in self.cache:
                cached_data = self.cache[symbol]
                if current_time - cached_data['timestamp'] < self.cache_timeout:
                    return cached_data['price']

            ticker = yf.Ticker(symbol)
            data = ticker.history(period='1d')
            if data.empty:
                return None
            
            price = float(data['Close'].iloc[-1])
            self.cache[symbol] = {
                'price': price,
                'timestamp': current_time
            }
            return price
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    def get_historical_data(self, symbol, period='1y', interval='1d'):
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval)
            return data
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()

    def calculate_atr(self, symbol, period=14):
        try:
            data = self.get_historical_data(symbol, period='1mo', interval='1d')
            if data.empty:
                return None
                
            # Izračun True Range
            data['H-L'] = data['High'] - data['Low']
            data['H-PC'] = abs(data['High'] - data['Close'].shift(1))
            data['L-PC'] = abs(data['Low'] - data['Close'].shift(1))
            
            data['TR'] = data[['H-L', 'H-PC', 'L-PC']].max(axis=1)
            data['ATR'] = data['TR'].rolling(window=period).mean()
            
            return data['ATR'].iloc[-1]
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None

class RiskManagement:
    def __init__(self, account_balance, max_risk_per_trade=0.02):
        self.account_balance = account_balance
        self.max_risk_per_trade = max_risk_per_trade

    def calculate_position_size(self, entry_price, stop_loss):
        try:
            risk_amount = self.account_balance * self.max_risk_per_trade
            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share == 0:
                return 0
            position_size = int(risk_amount / risk_per_share)
            return position_size
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return 0

    def calculate_risk_metrics(self, position_size, entry_price, stop_loss, take_profit):
        try:
            risk_per_share = abs(entry_price - stop_loss)
            reward_per_share = abs(take_profit - entry_price)
            total_risk = risk_per_share * position_size
            total_reward = reward_per_share * position_size
            risk_reward_ratio = reward_per_share / risk_per_share if risk_per_share > 0 else 0
            
            return {
                'risk_per_share': risk_per_share,
                'reward_per_share': reward_per_share,
                'total_risk': total_risk,
                'total_reward': total_reward,
                'risk_reward_ratio': risk_reward_ratio,
                'risk_percentage': (total_risk / self.account_balance) * 100
            }
        except Exception as e:
            logger.error(f"Error calculating risk metrics: {e}")
            return None

    def validate_trade(self, entry_price, stop_loss, take_profit):
        """
        Validacija trgovine prema rizik/reward kriterijima
        """
        try:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            
            # Minimalni rizik/reward ratio 1:2
            if reward / risk < 2:
                return False, "Risk/Reward ratio should be at least 1:2"
            
            # Maksimalni rizik po trgovini
            max_risk = self.account_balance * self.max_risk_per_trade
            if risk > max_risk:
                return False, f"Risk exceeds maximum allowed risk of ${max_risk:.2f}"
            
            return True, "Trade validation successful"
        except Exception as e:
            logger.error(f"Error validating trade: {e}")
            return False, f"Error validating trade: {str(e)}"

class PortfolioTracker(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.market_data = MarketDataManager()
        self.setup_ui()
        self.start_auto_refresh()

    def setup_ui(self):
        # Glavni okvir za treeview
        columns = (
            "ID", "Symbol", "Entry Price", "Current Price", "Stop Loss",
            "Take Profit", "Quantity", "PnL", "PnL %", "Status"
        )
        
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        
        # Konfiguracija stupaca
        self.tree.column("ID", width=50, anchor="center")
        for col in columns[1:]:
            self.tree.heading(col, text=col)
            if col in ["Symbol", "Status"]:
                width = 80
            elif col in ["PnL", "PnL %"]:
                width = 120
            else:
                width = 100
            self.tree.column(col, width=width, anchor="center")
        
        # Sakrivanje ID stupca
        self.tree.column("#1", stretch=False, width=0)
        
        # Scrollbars
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        
        # Pakiranje elemenata
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Frame za gumbe
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Gumbi
        ttk.Button(btn_frame, text="Close Position", 
                  command=self.close_position).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Modify Position", 
                  command=self.modify_position).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Add Note", 
                  command=self.add_note).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Export Data", 
                  command=self.export_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", 
                  command=self.refresh).pack(side=tk.LEFT, padx=5)

    def load_positions(self):
        db = DatabaseManager()
        try:
            positions = db.execute_query('''
                SELECT id, symbol, entry_price, stop_loss, take_profit, quantity, 
                       pnl, status, trade_type 
                FROM portfolio 
                WHERE status = 'Open'
                ORDER BY entry_date DESC
            ''')
            
            for position in positions:
                position_id = position[0]
                symbol = position[1]
                entry_price = position[2]
                stop_loss = position[3]
                take_profit = position[4]
                quantity = position[5]
                trade_type = position[8]  # trade_type je na indeksu 8
                current_price = self.market_data.get_real_time_price(symbol)
                
                if current_price:
                    # Izračun PnL-a ovisno o vrsti trgovine (Long/Short)
                    if trade_type == 'Long':
                        pnl = (current_price - entry_price) * quantity
                    else:  # Short
                        pnl = (entry_price - current_price) * quantity
                    
                    pnl_percentage = (pnl / (entry_price * quantity)) * 100
                    
                    # Kreiraj listu vrijednosti za prikaz
                    values = [
                        position_id,           # ID
                        symbol,                # Symbol
                        f"${entry_price:.2f}", # Entry Price
                        f"${current_price:.2f}", # Current Price
                        f"${stop_loss:.2f}" if stop_loss else "N/A",    # Stop Loss
                        f"${take_profit:.2f}" if take_profit else "N/A", # Take Profit
                        quantity,              # Quantity
                        f"${pnl:.2f}",        # PnL
                        f"{pnl_percentage:.2f}%", # PnL %
                        position[7]            # Status
                    ]
                    
                    # Dodaj boju ovisno o PnL
                    tag = 'profit' if pnl > 0 else 'loss'
                    self.tree.insert("", tk.END, values=values, tags=(tag,))
                    
            # Konfiguriraj boje za PnL
            self.tree.tag_configure('profit', foreground='green')
            self.tree.tag_configure('loss', foreground='red')
            
        except Exception as e:
            logger.error(f"Error loading positions: {e}")
            messagebox.showerror("Error", f"Failed to load positions: {str(e)}")
        finally:
            db.close()

    def close_position(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Greška", "Odaberite poziciju za zatvaranje.")
            return

        position = self.tree.item(selected[0])['values']
        position_id = position[0]
        symbol = position[1]
        current_quantity = position[6]  # Quantity je na indeksu 6
        current_price = self.market_data.get_real_time_price(symbol)
        
        if current_price:
            # Pitaj korisnika želi li zatvoriti cijelu ili dio pozicije
            close_type = messagebox.askyesnocancel(
                "Zatvori poziciju",
                f"Želite li zatvoriti cijelu poziciju za {symbol}?\n\n"
                f"Da - Zatvori cijelu poziciju\n"
                f"Ne - Zatvori dio pozicije\n"
                f"Odustani - Prekini operaciju",
                icon='question'
            )
            
            if close_type is None:  # Korisnik je kliknuo Cancel
                return
            
            if close_type:  # Korisnik je odabrao zatvaranje cijele pozicije
                if messagebox.askyesno("Potvrdi zatvaranje",
                                   f"Zatvori cijelu poziciju {symbol} po cijeni ${current_price:.2f}?"):
                    self.execute_close_position(position_id, current_price)
            else:  # Korisnik je odabrao parcijalno zatvaranje
                dialog = PartialCloseDialog(self, current_quantity)
                self.wait_window(dialog)
                
                if dialog.result and dialog.quantity > 0:
                    self.execute_partial_close(position_id, dialog.quantity, current_price)

    def execute_close_position(self, position_id, exit_price):
        db = DatabaseManager()
        try:
            # Dohvati podatke o poziciji
            position = db.execute_query('''
                SELECT entry_price, quantity FROM portfolio WHERE id = ?
            ''', (position_id,))[0]
            
            entry_price, quantity = position
            pnl = (exit_price - entry_price) * quantity
            
            # Ažuriraj poziciju
            db.execute_query('''
                UPDATE portfolio
                SET exit_price = ?, exit_date = ?, pnl = ?, status = 'Closed'
                WHERE id = ?
            ''', (exit_price, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                 pnl, position_id))
            
            self.refresh()
            messagebox.showinfo("Success", f"Position closed with PnL: ${pnl:.2f}")
            
            # Ažuriraj statistiku i zatvorene trgovine
            if hasattr(self.master, 'statistics'):
                self.master.statistics.calculate_statistics()
            if hasattr(self.master, 'closed_trades'):
                self.master.closed_trades.load_closed_trades()
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            messagebox.showerror("Error", "Failed to close position.")
        finally:
            db.close()

    def execute_partial_close(self, position_id, close_quantity, exit_price):
    db = DatabaseManager()
    try:
        # Dohvati originalne podatke pozicije
        position = db.execute_query('''
            SELECT entry_price, quantity, trade_type, symbol, stop_loss, 
                   take_profit, entry_date 
            FROM portfolio 
            WHERE id = ?
        ''', (position_id,))[0]
        
        entry_price, total_quantity, trade_type, symbol, stop_loss, \
        take_profit, entry_date = position
        
        remaining_quantity = total_quantity - close_quantity
        
        # Izračunaj PnL za zatvoreni dio
        pnl = (exit_price - entry_price) * close_quantity if trade_type == 'Long' \
              else (entry_price - exit_price) * close_quantity
        
        if remaining_quantity > 0:
            # Ažuriraj originalnu poziciju s preostalom količinom
            db.execute_query('''
                UPDATE portfolio 
                SET quantity = ?,
                    original_quantity = COALESCE(original_quantity, quantity)
                WHERE id = ?
            ''', (remaining_quantity, position_id))
            
            # Kreiraj novi zapis za zatvoreni dio
            db.execute_query('''
                INSERT INTO portfolio (
                    symbol, entry_price, exit_price, stop_loss, take_profit,
                    quantity, entry_date, exit_date, pnl, trade_type, status,
                    original_quantity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, entry_price, exit_price, stop_loss, take_profit,
                  close_quantity, entry_date, 
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  pnl, trade_type, 'Closed', close_quantity))
        else:
            # Zatvori cijelu poziciju ako je preostala količina 0
            self.execute_close_position(position_id, exit_price)
        
        self.refresh()
        messagebox.showinfo("Uspjeh", 
                          f"Zatvoreno {close_quantity} jedinica s PnL: ${pnl:.2f}")
        
    except Exception as e:
        logger.error(f"Error in partial close: {e}")
        messagebox.showerror("Greška", f"Greška pri zatvaranju pozicije: {str(e)}")
    finally:
        db.close()

def modify_position(self):
    selected = self.tree.selection()
    if not selected:
        messagebox.showwarning("Selection Error", "Please select a position to modify.")
        return

    position = self.tree.item(selected[0])['values']
    position_id = position[0]
    
    modify_window = tk.Toplevel(self)
    modify_window.title("Modify Position")
    modify_window.geometry("300x200")
    
    ttk.Label(modify_window, text="Stop Loss:").pack(pady=5)
    stop_loss_entry = ttk.Entry(modify_window)
    stop_loss_entry.insert(0, str(position[4]))
    stop_loss_entry.pack(pady=5)
    
    ttk.Label(modify_window, text="Take Profit:").pack(pady=5)
    take_profit_entry = ttk.Entry(modify_window)
    take_profit_entry.insert(0, str(position[5]))
    take_profit_entry.pack(pady=5)
    
    def save_modifications():
        try:
            db = DatabaseManager()
            db.execute_query('''
                UPDATE portfolio
                SET stop_loss = ?, take_profit = ?
                WHERE id = ?
            ''', (float(stop_loss_entry.get()), 
                 float(take_profit_entry.get()), position_id))
            modify_window.destroy()
            self.refresh()
            messagebox.showinfo("Success", "Position updated successfully!")
        except Exception as e:
            logger.error(f"Error modifying position: {e}")
            messagebox.showerror("Error", "Failed to modify position.")
        finally:
            db.close()
    
    ttk.Button(modify_window, text="Save", 
              command=save_modifications).pack(pady=20)

def add_note(self):
    selected = self.tree.selection()
    if not selected:
        messagebox.showwarning("Selection Error", 
                             "Please select a position to add a note.")
        return

    position_id = self.tree.item(selected[0])['values'][0]
    note = simpledialog.askstring("Add Note", "Enter note:")
    
    if note:
        db = DatabaseManager()
        try:
            db.execute_query('''
                INSERT INTO trade_journal (trade_id, entry_date, notes)
                VALUES (?, ?, ?)
            ''', (position_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note))
            messagebox.showinfo("Success", "Note added successfully!")
        except Exception as e:
            logger.error(f"Error adding note: {e}")
            messagebox.showerror("Error", "Failed to add note.")
        finally:
            db.close()

def export_data(self):
    file_path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv"), 
                  ("Excel files", "*.xlsx"), 
                  ("All files", "*.*")]
    )
    if not file_path:
        return
        
    db = DatabaseManager()
    try:
        data = db.execute_query('''
            SELECT * FROM portfolio
            ORDER BY entry_date DESC
        ''')
        
        df = pd.DataFrame(data, columns=[
            'ID', 'Symbol', 'Entry Price', 'Exit Price', 'Stop Loss',
            'Take Profit', 'Quantity', 'Entry Date', 'Exit Date', 'PnL',
            'Trade Type', 'Status', 'Notes'
        ])
        
        if file_path.endswith('.csv'):
            df.to_csv(file_path, index=False)
        else:
            df.to_excel(file_path, index=False)
            
        messagebox.showinfo("Success", "Data exported successfully!")
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        messagebox.showerror("Error", "Failed to export data.")
    finally:
        db.close()

def refresh(self):
    for item in self.tree.get_children():
        self.tree.delete(item)
    self.load_positions()

def start_auto_refresh(self):
    self.refresh()
    self.after(30000, self.start_auto_refresh)  # Osvježi svakih 30 sekundi

class PositionCalculator(ttk.Frame):
    def __init__(self, parent, account_balance=10000.0, risk_percentage=2.0, portfolio_tracker=None):
        super().__init__(parent)
        self.account_balance = account_balance
        self.risk_percentage = risk_percentage
        self.portfolio_tracker = portfolio_tracker
        self.market_data = MarketDataManager()
        self.risk_manager = RiskManagement(account_balance)
        self.setup_ui()

    def setup_ui(self):
        # Okvir za unos simbola
        symbol_frame = ttk.LabelFrame(self, text="Trade Entry", padding=10)
        symbol_frame.pack(fill=tk.X, padx=5, pady=5)

        # Unos simbola
        ttk.Label(symbol_frame, text="Symbol:").grid(row=0, column=0, padx=5, pady=5)
        self.symbol_var = tk.StringVar()
        self.symbol_entry = ttk.Entry(symbol_frame, textvariable=self.symbol_var)
        self.symbol_entry.grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(symbol_frame, text="Fetch Price", 
                  command=self.fetch_price).grid(row=0, column=2, padx=5, pady=5)

        # Unos cijena
        self.setup_price_entries(symbol_frame)

        # Odabir vrste trgovine
        self.trade_type = tk.StringVar(value="Long")
        ttk.Radiobutton(symbol_frame, text="Long", 
                       variable=self.trade_type, 
                       value="Long").grid(row=4, column=0)
        ttk.Radiobutton(symbol_frame, text="Short", 
                       variable=self.trade_type, 
                       value="Short").grid(row=4, column=1)

        # Okvir za upravljanje rizikom
        risk_frame = ttk.LabelFrame(self, text="Risk Management", padding=10)
        risk_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.setup_risk_settings(risk_frame)

        # Okvir za rezultate
        self.setup_results_frame()

        # Okvir za grafikon
        self.setup_chart_frame()

    def setup_price_entries(self, parent_frame):
        # Ulazna cijena
        self.entry_price_var = tk.StringVar()
        ttk.Label(parent_frame, text="Entry Price:").grid(row=1, column=0, padx=5, pady=5)
        self.entry_price_entry = ttk.Entry(parent_frame, textvariable=self.entry_price_var)
        self.entry_price_entry.grid(row=1, column=1, padx=5, pady=5)

        # Stop Loss okvir
        stop_loss_frame = ttk.LabelFrame(parent_frame, text="Stop Loss Settings", padding=5)
        stop_loss_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        # Vrsta Stop Loss-a
        self.stop_loss_type = tk.StringVar(value="Fixed")
        ttk.Radiobutton(stop_loss_frame, text="Fixed", 
                       variable=self.stop_loss_type, 
                       value="Fixed",
                       command=self.update_stop_loss_mode).grid(row=0, column=0)
        ttk.Radiobutton(stop_loss_frame, text="ATR Based", 
                       variable=self.stop_loss_type, 
                       value="ATR",
                       command=self.update_stop_loss_mode).grid(row=0, column=1)

        # Fiksni Stop Loss
        self.stop_loss_var = tk.StringVar()
        self.stop_loss_label = ttk.Label(stop_loss_frame, text="Stop Loss:")
        self.stop_loss_label.grid(row=1, column=0)
        self.stop_loss_entry = ttk.Entry(stop_loss_frame, textvariable=self.stop_loss_var)
        self.stop_loss_entry.grid(row=1, column=1)

        # ATR množitelj
        self.atr_multiplier_var = tk.StringVar(value="2.0")
        self.atr_multiplier_label = ttk.Label(stop_loss_frame, text="ATR Multiplier:")
        self.atr_multiplier_label.grid(row=2, column=0)
        self.atr_multiplier_entry = ttk.Entry(stop_loss_frame, 
                                           textvariable=self.atr_multiplier_var)
        self.atr_multiplier_entry.grid(row=2, column=1)
        
        # ATR vrijednost
        self.atr_value_label = ttk.Label(stop_loss_frame, text="Current ATR: N/A")
        self.atr_value_label.grid(row=3, column=0, columnspan=2)

        # Početno sakrij ATR kontrole
        self.atr_multiplier_label.grid_remove()
        self.atr_multiplier_entry.grid_remove()
        self.atr_value_label.grid_remove()

        # Take Profit
        self.take_profit_var = tk.StringVar()
        ttk.Label(parent_frame, text="Take Profit:").grid(row=3, column=0, padx=5, pady=5)
        self.take_profit_entry = ttk.Entry(parent_frame, textvariable=self.take_profit_var)
        self.take_profit_entry.grid(row=3, column=1, padx=5, pady=5)

    def setup_risk_settings(self, risk_frame):
        # Risk postotak slider
        ttk.Label(risk_frame, text="Risk %:").grid(row=0, column=0, padx=5, pady=5)
        self.risk_slider = ttk.Scale(risk_frame, from_=0.1, to=5.0, orient="horizontal")
        self.risk_slider.set(self.risk_percentage)
        self.risk_slider.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # Gumb za izračun
        ttk.Button(risk_frame, text="Calculate Position", 
                  command=self.calculate_position).grid(row=1, column=0, columnspan=2, pady=10)

    def setup_results_frame(self):
        results_frame = ttk.LabelFrame(self, text="Position Details", padding=10)
        results_frame.pack(fill=tk.X, padx=5, pady=5)

        self.position_size_label = ttk.Label(results_frame, text="Position Size: ")
        self.position_size_label.pack(pady=2)

        self.risk_amount_label = ttk.Label(results_frame, text="Risk Amount: ")
        self.risk_amount_label.pack(pady=2)

        self.reward_amount_label = ttk.Label(results_frame, text="Potential Reward: ")
        self.reward_amount_label.pack(pady=2)

        self.risk_reward_label = ttk.Label(results_frame, text="Risk/Reward Ratio: ")
        self.risk_reward_label.pack(pady=2)

    def setup_chart_frame(self):
        chart_frame = ttk.LabelFrame(self, text="Price Chart", padding=10)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.fig, self.ax = plt.subplots(figsize=(8, 4))
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, chart_frame)
        self.toolbar.update()

    def update_stop_loss_mode(self):
        if self.stop_loss_type.get() == "ATR":
            self.stop_loss_label.configure(text="ATR Stop Loss:")
            self.atr_multiplier_label.grid()
            self.atr_multiplier_entry.grid()
            self.atr_value_label.grid()
            if self.symbol_var.get():
                self.calculate_atr_stop_loss()
        else:
            self.stop_loss_label.configure(text="Stop Loss:")
            self.atr_multiplier_label.grid_remove()
            self.atr_multiplier_entry.grid_remove()
            self.atr_value_label.grid_remove()

    def calculate_atr_stop_loss(self):
        symbol = self.symbol_var.get().upper()
        if not symbol:
            return
            
        try:
            atr = self.market_data.calculate_atr(symbol)
            if atr is not None:
                self.atr_value_label.configure(text=f"Current ATR: ${atr:.2f}")
                
                if self.stop_loss_type.get() == "ATR":
                    current_price = float(self.entry_price_var.get())
                    multiplier = float(self.atr_multiplier_var.get())
                    
                    if self.trade_type.get() == "Long":
                        stop_loss = current_price - (atr * multiplier)
                    else:
                        stop_loss = current_price + (atr * multiplier)
                        
                    self.stop_loss_var.set(f"{stop_loss:.2f}")
            else:
                self.atr_value_label.configure(text="ATR: Not Available")
        except Exception as e:
            logger.error(f"Error calculating ATR stop loss: {e}")
            messagebox.showerror("Error", "Failed to calculate ATR stop loss.")

    def fetch_price(self):
        symbol = self.symbol_var.get().upper()
        if not symbol:
            messagebox.showwarning("Input Error", "Please enter a symbol.")
            return
                
        current_price = self.market_data.get_real_time_price(symbol)
        if current_price is not None:
            self.entry_price_var.set(f"{current_price:.2f}")
            self.update_chart(symbol)
            if self.stop_loss_type.get() == "ATR":
                self.calculate_atr_stop_loss()
        else:
            messagebox.showerror("Error", f"Failed to fetch price for {symbol}")

    def update_chart(self, symbol):
        try:
            data = self.market_data.get_historical_data(symbol, period='6mo')
            if not data.empty:
                self.ax.clear()
                self.ax.plot(data.index, data['Close'], label='Price')
                
                # Dodaj pokretne prosjeke
                data['MA20'] = data['Close'].rolling(window=20).mean()
                data['MA50'] = data['Close'].rolling(window=50).mean()
                self.ax.plot(data.index, data['MA20'], label='20 MA', alpha=0.7)
                self.ax.plot(data.index, data['MA50'], label='50 MA', alpha=0.7)

                self.ax.set_title(f"{symbol} Price History")
                self.ax.set_xlabel("Date")
                self.ax.set_ylabel("Price ($)")
                self.ax.legend()
                self.ax.grid(True)
                self.fig.autofmt_xdate()
                self.canvas.draw()
        except Exception as e:
            logger.error(f"Error updating chart: {e}")

    def calculate_position(self):
    try:
        entry_price = float(self.entry_price_var.get())
        stop_loss = float(self.stop_loss_var.get())
        take_profit = float(self.take_profit_var.get())
        
        # Validacija ulaznih podataka
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            raise ValueError("All prices must be greater than 0")
            
        position_size = self.risk_manager.calculate_position_size(entry_price, stop_loss)
        risk_metrics = self.risk_manager.calculate_risk_metrics(
            position_size, entry_price, stop_loss, take_profit)
        
        if risk_metrics:
            self.position_size_label.config(text=f"Position Size: {position_size} shares")
            self.risk_amount_label.config(text=f"Risk Amount: ${risk_metrics['total_risk']:.2f}")
            self.reward_amount_label.config(
                text=f"Potential Reward: ${risk_metrics['total_reward']:.2f}")
            self.risk_reward_label.config(
                text=f"Risk/Reward Ratio: {risk_metrics['risk_reward_ratio']:.2f}")
            
            # Provjera rizik/reward ratia
            if risk_metrics['risk_reward_ratio'] < 1:
                messagebox.showwarning("Risk Warning", 
                    "Risk/Reward ratio is less than 1:1. Consider adjusting your entry points.")
            
            if messagebox.askyesno("Confirm Trade", "Would you like to save this trade?"):
                self.save_trade(position_size)
                
    except ValueError as e:
        messagebox.showwarning("Input Error", str(e))
    except Exception as e:
        logger.error(f"Error calculating position: {e}")
        messagebox.showerror("Error", "An error occurred while calculating position.")                

def save_trade(self, position_size):
    db = DatabaseManager()
    try:
        # Validacija podataka prije spremanja
        symbol = self.symbol_var.get().strip().upper()
        entry_price = float(self.entry_price_var.get())
        stop_loss = float(self.stop_loss_var.get())
        take_profit = float(self.take_profit_var.get())
        
        if not symbol or entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            raise ValueError("Invalid trade parameters")
        
        trade_data = {
            'symbol': symbol,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'quantity': position_size,
            'entry_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'trade_type': self.trade_type.get(),
            'status': 'Open'
        }
        
        # Debug ispis
        print("Attempting to save trade with data:")
        for key, value in trade_data.items():
            print(f"{key}: {value}")
        
        db.execute_query('''
            INSERT INTO portfolio (
                symbol, entry_price, stop_loss, take_profit, quantity,
                entry_date, trade_type, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_data['symbol'], trade_data['entry_price'], trade_data['stop_loss'],
            trade_data['take_profit'], trade_data['quantity'], trade_data['entry_date'],
            trade_data['trade_type'], trade_data['status']
        ))
        
        messagebox.showinfo("Success", f"Trade saved successfully!\nSymbol: {symbol}\nQuantity: {position_size}")
        
        # Osvježi portfolio tracker ako postoji
        if self.portfolio_tracker:
            self.portfolio_tracker.refresh()
            
    except ValueError as ve:
        logger.error(f"Validation error in save_trade: {ve}")
        messagebox.showerror("Validation Error", str(ve))
    except Exception as e:
        logger.error(f"Error in save_trade: {e}", exc_info=True)
        messagebox.showerror("Error", f"Failed to save trade: {str(e)}")
    finally:
        db.close()

def test_save_trade(self):
    """Metoda za testiranje spremanja trgovine"""
    try:
        # Postavi test podatke
        self.symbol_var.set("AAPL")
        self.entry_price_var.set("150.0")
        self.stop_loss_var.set("145.0")
        self.take_profit_var.set("160.0")
        self.trade_type.set("Long")
        
        # Pokušaj spremanja
        self.calculate_position()
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        print(f"Test failed: {e}")

def clear_fields(self):
    """Očisti sva polja za unos"""
    self.symbol_var.set("")
    self.entry_price_var.set("")
    self.stop_loss_var.set("")
    self.take_profit_var.set("")
    self.trade_type.set("Long")
    
    # Očisti labele s rezultatima
    self.position_size_label.config(text="Position Size: ")
    self.risk_amount_label.config(text="Risk Amount: ")
    self.reward_amount_label.config(text="Potential Reward: ")
    self.risk_reward_label.config(text="Risk/Reward Ratio: ")
    
    # Očisti graf
    self.ax.clear()
    self.canvas.draw()

class ClosedTradesTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_ui()
        self.load_closed_trades()

    def setup_ui(self):
        # Kreiranje Treeview-a za zatvorene trgovine
        columns = ("ID", "Symbol", "Entry Price", "Exit Price", "Stop Loss",
                  "Take Profit", "Quantity", "PnL", "Trade Type", "Entry Date", "Exit Date")
        
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        
        # Konfiguracija stupaca
        for col in columns:
            self.tree.heading(col, text=col)
            width = 100 if col not in ["Symbol", "Trade Type"] else 80
            self.tree.column(col, width=width, anchor="center")
        
        # Scrollbars
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        
        # Pakiranje elemenata
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Gumb za osvježavanje
        ttk.Button(self, text="Refresh", 
                  command=self.load_closed_trades).pack(pady=5)

        # Gumb za izvoz podataka
        ttk.Button(self, text="Export to CSV", 
                  command=self.export_closed_trades).pack(pady=5)

    def load_closed_trades(self):
        self.tree.delete(*self.tree.get_children())
        db = DatabaseManager()
        try:
            trades = db.execute_query('''
                SELECT id, symbol, entry_price, exit_price, stop_loss,
                       take_profit, quantity, pnl, trade_type, entry_date, exit_date
                FROM portfolio 
                WHERE status = 'Closed'
                ORDER BY exit_date DESC
            ''')
            
            for trade in trades:
                # Formatiraj PnL s bojom
                pnl = trade[7]
                pnl_formatted = f"${pnl:.2f}"
                
                values = list(trade)
                values[7] = pnl_formatted
                
                # Umetni s tagovima za boju
                tag = 'profit' if pnl > 0 else 'loss'
                self.tree.insert("", tk.END, values=values, tags=(tag,))
            
            # Konfiguriraj boje tagova
            self.tree.tag_configure('profit', foreground='green')
            self.tree.tag_configure('loss', foreground='red')
            
        except Exception as e:
            logger.error(f"Error loading closed trades: {e}")
            messagebox.showerror("Error", "Failed to load closed trades.")
        finally:
            db.close()

    def export_closed_trades(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), 
                      ("Excel files", "*.xlsx")]
        )
        if not file_path:
            return
            
        db = DatabaseManager()
        try:
            trades = db.execute_query('''
                SELECT * FROM portfolio 
                WHERE status = 'Closed'
                ORDER BY exit_date DESC
            ''')
            
            df = pd.DataFrame(trades, columns=[
                'ID', 'Symbol', 'Entry Price', 'Exit Price', 'Stop Loss',
                'Take Profit', 'Quantity', 'Entry Date', 'Exit Date', 'PnL',
                'Trade Type', 'Status', 'Notes'
            ])
            
            if file_path.endswith('.csv'):
                df.to_csv(file_path, index=False)
            else:
                df.to_excel(file_path, index=False)
                
            messagebox.showinfo("Success", "Closed trades exported successfully!")
        except Exception as e:
            logger.error(f"Error exporting closed trades: {e}")
            messagebox.showerror("Error", "Failed to export closed trades.")
        finally:
            db.close()

class StatisticsTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_ui()
        self.calculate_statistics()

    def setup_ui(self):
        # Glavni okvir za statistiku
        stats_frame = ttk.LabelFrame(self, text="Trading Performance", padding=10)
        stats_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Ukupna statistika
        overall_frame = ttk.LabelFrame(stats_frame, text="Overall Statistics", padding=5)
        overall_frame.pack(fill=tk.X, padx=5, pady=5)

        self.total_trades_label = ttk.Label(overall_frame, text="Total Trades: ")
        self.total_trades_label.pack(pady=2)

        self.winning_trades_label = ttk.Label(overall_frame, text="Winning Trades: ")
        self.winning_trades_label.pack(pady=2)

        self.losing_trades_label = ttk.Label(overall_frame, text="Losing Trades: ")
        self.losing_trades_label.pack(pady=2)

        self.win_rate_label = ttk.Label(overall_frame, text="Win Rate: ")
        self.win_rate_label.pack(pady=2)

        # Statistika profita
        profit_frame = ttk.LabelFrame(stats_frame, text="Profit Statistics", padding=5)
        profit_frame.pack(fill=tk.X, padx=5, pady=5)

        self.total_pnl_label = ttk.Label(profit_frame, text="Total P&L: ")
        self.total_pnl_label.pack(pady=2)

        self.profit_factor_label = ttk.Label(profit_frame, text="Profit Factor: ")
        self.profit_factor_label.pack(pady=2)

        self.avg_win_label = ttk.Label(profit_frame, text="Average Win: ")
        self.avg_win_label.pack(pady=2)

        self.avg_loss_label = ttk.Label(profit_frame, text="Average Loss: ")
        self.avg_loss_label.pack(pady=2)

        self.largest_win_label = ttk.Label(profit_frame, text="Largest Win: ")
        self.largest_win_label.pack(pady=2)

        self.largest_loss_label = ttk.Label(profit_frame, text="Largest Loss: ")
        self.largest_loss_label.pack(pady=2)

        # Rizične statistike
        risk_frame = ttk.LabelFrame(stats_frame, text="Risk Metrics", padding=5)
        risk_frame.pack(fill=tk.X, padx=5, pady=5)

        self.avg_rr_label = ttk.Label(risk_frame, text="Average R:R Ratio: ")
        self.avg_rr_label.pack(pady=2)

        self.avg_hold_time_label = ttk.Label(risk_frame, text="Average Hold Time: ")
        self.avg_hold_time_label.pack(pady=2)

        # Gumb za osvježavanje
        ttk.Button(self, text="Refresh Statistics", 
                  command=self.calculate_statistics).pack(pady=10)

    def calculate_statistics(self):
        db = DatabaseManager()
        try:
            trades = db.execute_query('''
                SELECT pnl, entry_date, exit_date, entry_price, exit_price,
                       stop_loss, take_profit
                FROM portfolio 
                WHERE status = 'Closed'
            ''')
            
            if not trades:
                return
            
            # Osnovni izračuni
            pnls = [t[0] for t in trades]
            profits = [p for p in pnls if p > 0]
            losses = [abs(l) for l in pnls if l < 0]
            
            total_trades = len(trades)
            winning_trades = len(profits)
            losing_trades = len(losses)
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            # Izračuni profita
            total_pnl = sum(pnls)
            profit_factor = sum(profits) / sum(losses) if losses and sum(losses) > 0 else float('inf')
            
            avg_win = sum(profits) / len(profits) if profits else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            
            largest_win = max(profits) if profits else 0
            largest_loss = max(losses) if losses else 0
            
            # Izračun prosječnog R:R omjera
            rr_ratios = []
            for trade in trades:
                entry = trade[3]
                stop = trade[5]
                take = trade[6]
                if entry and stop and take:
                    risk = abs(entry - stop)
                    reward = abs(take - entry)
                    if risk > 0:
                        rr_ratios.append(reward/risk)
            
            avg_rr = sum(rr_ratios) / len(rr_ratios) if rr_ratios else 0
            
            # Izračun prosječnog vremena držanja
            hold_times = []
            for trade in trades:
                entry_date = datetime.strptime(trade[1], "%Y-%m-%d %H:%M:%S")
                exit_date = datetime.strptime(trade[2], "%Y-%m-%d %H:%M:%S")
                hold_time = exit_date - entry_date
                hold_times.append(hold_time.total_seconds())
            
            avg_hold_time = timedelta(seconds=sum(hold_times)/len(hold_times)) if hold_times else timedelta()
            
            # Ažuriranje labela
            self.total_trades_label.config(text=f"Total Trades: {total_trades}")
            self.winning_trades_label.config(text=f"Winning Trades: {winning_trades}")
            self.losing_trades_label.config(text=f"Losing Trades: {losing_trades}")
            self.win_rate_label.config(text=f"Win Rate: {win_rate:.2f}%")
            
            self.total_pnl_label.config(text=f"Total P&L: ${total_pnl:.2f}")
            self.profit_factor_label.config(text=f"Profit Factor: {profit_factor:.2f}")
            self.avg_win_label.config(text=f"Average Win: ${avg_win:.2f}")
            self.avg_loss_label.config(text=f"Average Loss: ${avg_loss:.2f}")
            self.largest_win_label.config(text=f"Largest Win: ${largest_win:.2f}")
            self.largest_loss_label.config(text=f"Largest Loss: ${largest_loss:.2f}")
            
            self.avg_rr_label.config(text=f"Average R:R Ratio: {avg_rr:.2f}")
            self.avg_hold_time_label.config(text=f"Average Hold Time: {str(avg_hold_time).split('.')[0]}")
            
        except Exception as e:
            logger.error(f"Error calculating statistics: {e}")
            messagebox.showerror("Error", "Failed to calculate statistics.")
        ```python
        finally:
            db.close()

class TradingApp(ThemedTk):
    def __init__(self):
        super().__init__()
        
        # Osnovna konfiguracija prozora
        self.title("Trading Application Pro")
        self.geometry("1200x800")
        self.set_theme("arc")  # Moderni theme
        
        # Inicijalizacija varijabli računa
        self.account_balance = tk.DoubleVar(value=10000.0)
        self.risk_percentage = tk.DoubleVar(value=2.0)
        
        # Postavi menu
        self.setup_menu()
        
        # Postavi glavno sučelje
        self.setup_main_interface()
        
        # Logiranje pokretanja aplikacije
        logger.info("Trading Application started successfully")

    def setup_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        
        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Settings", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Export Data", command=self.export_all_data)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        
        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Refresh All", command=self.refresh_all)
        
        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Documentation", command=self.show_documentation)
        help_menu.add_command(label="About", command=self.show_about)

    def setup_main_interface(self):
        # Kreiraj notebook za tabove
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Inicijalizacija komponenti
        self.portfolio_tracker = PortfolioTracker(self.notebook)
        self.position_calculator = PositionCalculator(
            self.notebook,
            self.account_balance.get(),
            self.risk_percentage.get(),
            self.portfolio_tracker
        )
        self.closed_trades = ClosedTradesTab(self.notebook)
        self.statistics = StatisticsTab(self.notebook)
        
        # Dodaj tabove
        self.notebook.add(self.position_calculator, text="Position Calculator")
        self.notebook.add(self.portfolio_tracker, text="Portfolio")
        self.notebook.add(self.closed_trades, text="Closed Trades")
        self.notebook.add(self.statistics, text="Statistics")
        
        # Status bar
        self.status_bar = ttk.Label(
            self, 
            text=f"Account Balance: ${self.account_balance.get():,.2f}", 
            relief=tk.SUNKEN, 
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def open_settings(self):
        settings_window = tk.Toplevel(self)
        settings_window.title("Settings")
        settings_window.geometry("300x200")
        
        ttk.Label(settings_window, text="Account Balance:").pack(pady=5)
        balance_entry = ttk.Entry(settings_window)
        balance_entry.insert(0, str(self.account_balance.get()))
        balance_entry.pack(pady=5)
        
        ttk.Label(settings_window, text="Risk Percentage:").pack(pady=5)
        risk_entry = ttk.Entry(settings_window)
        risk_entry.insert(0, str(self.risk_percentage.get()))
        risk_entry.pack(pady=5)
        
        def save_settings():
            try:
                new_balance = float(balance_entry.get())
                new_risk = float(risk_entry.get())
                
                if new_balance <= 0:
                    raise ValueError("Account balance must be greater than 0")
                if new_risk <= 0 or new_risk > 100:
                    raise ValueError("Risk percentage must be between 0 and 100")
                    
                self.account_balance.set(new_balance)
                self.risk_percentage.set(new_risk)
                self.status_bar.config(text=f"Account Balance: ${new_balance:,.2f}")
                
                self.position_calculator.risk_manager = RiskManagement(
                    new_balance, new_risk/100)
                
                settings_window.destroy()
                messagebox.showinfo("Success", "Settings saved successfully!")
                
            except ValueError as e:
                messagebox.showerror("Error", str(e))
        
        ttk.Button(settings_window, text="Save", 
                  command=save_settings).pack(pady=20)

    def export_all_data(self):
        try:
            export_dir = filedialog.askdirectory(
                title="Select Directory to Export Data"
            )
            if not export_dir:
                return
                
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Export open positions
            self.portfolio_tracker.export_data(
                f"{export_dir}/open_positions_{timestamp}.csv"
            )
            
            # Export closed trades
            self.closed_trades.export_closed_trades(
                f"{export_dir}/closed_trades_{timestamp}.csv"
            )
            
            messagebox.showinfo("Success", "All data exported successfully!")
            
        except Exception as e:
            logger.error(f"Error exporting all data: {e}")
            messagebox.showerror("Error", "Failed to export data.")

    def refresh_all(self):
        try:
            self.portfolio_tracker.refresh()
            self.closed_trades.load_closed_trades()
            self.statistics.calculate_statistics()
            messagebox.showinfo("Success", "All data refreshed!")
        except Exception as e:
            logger.error(f"Error refreshing all data: {e}")
            messagebox.showerror("Error", "Failed to refresh data.")

    def show_documentation(self):
        doc_text = """
        Trading Application Pro Documentation
        
        1. Position Calculator
           - Enter symbol and fetch current price
           - Set stop loss and take profit levels
           - Calculate position size based on risk
           
        2. Portfolio Tracker
           - View all open positions
           - Monitor real-time P&L
           - Close or modify positions
           
        3. Closed Trades
           - View history of all closed trades
           - Export trade history
           
        4. Statistics
           - Monitor trading performance
           - View key metrics and ratios
        """
        
        doc_window = tk.Toplevel(self)
        doc_window.title("Documentation")
        doc_window.geometry("600x400")
        
        text_widget = tk.Text(doc_window, wrap=tk.WORD, padx=10, pady=10)
        text_widget.insert(tk.END, doc_text)
        text_widget.config(state=tk.DISABLED)
        text_widget.pack(fill=tk.BOTH, expand=True)

    def show_about(self):
        about_text = """
        Trading Application Pro
        Version 1.0.0
        
        A professional trading tool for position sizing,
        portfolio management, and trade analysis.
        
        Created by: Garrincha077
        Last Updated: 2025-02-10
        """
        messagebox.showinfo("About", about_text)

def main():
    try:
        # Provjera i kreiranje direktorija za logove ako ne postoji
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # Konfiguracija logginga
        logging.basicConfig(
            filename=log_dir / "trading_app.log",
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Inicijalizacija baze podataka
        db = DatabaseManager()
        db.setup_database()
        db.close()
        
        # Pokretanje aplikacije
        app = TradingApp()
        app.mainloop()
        
    except Exception as e:
        logger.critical(f"Application failed to start: {e}", exc_info=True)
        messagebox.showerror(
            "Critical Error",
            "Application failed to start. Please check the log file."
        )

if __name__ == "__main__":
    main()
