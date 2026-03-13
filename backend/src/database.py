"""DuckDB in-memory database — loads Excel / CSV files from the Input directory."""

import os
import re
import shutil
import tempfile

import duckdb
import pandas as pd

from .config import DATA_FILE_DIR, STATE_DIR


class Database:
    def __init__(self, data_path: str | None = None):
        self.data_path = data_path or str(DATA_FILE_DIR)
        self.con = duckdb.connect(database=":memory:")
        self.load_files()

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def clean_table_name(filename: str) -> str:
        name = filename.split(".")[0].lower()
        name = re.sub(r"[^a-z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name)
        return name.strip("_")

    def _shadow_dir(self) -> str:
        d = os.path.join(str(STATE_DIR), "excel_shadow")
        os.makedirs(d, exist_ok=True)
        return d

    def _read_excel_via_shadow(self, file_path: str, file_name: str) -> dict[str, pd.DataFrame]:
        suffix = os.path.splitext(file_name)[1] or ".xlsx"
        fd, shadow = tempfile.mkstemp(prefix="shadow_", suffix=suffix, dir=self._shadow_dir())
        os.close(fd)
        try:
            shutil.copy2(file_path, shadow)
            return pd.read_excel(shadow, sheet_name=None, engine="openpyxl")
        finally:
            try:
                os.remove(shadow)
            except Exception:
                pass

    def _read_excel(self, file_path: str, file_name: str) -> dict[str, pd.DataFrame]:
        ext = os.path.splitext(file_name)[1].lower()
        if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
            try:
                return pd.read_excel(file_path, sheet_name=None, engine="openpyxl")
            except PermissionError:
                return self._read_excel_via_shadow(file_path, file_name)
            except Exception:
                try:
                    return pd.read_excel(file_path, sheet_name=None)
                except PermissionError:
                    return self._read_excel_via_shadow(file_path, file_name)
        if ext == ".xls":
            return pd.read_excel(file_path, sheet_name=None, engine="xlrd")
        raise ValueError(f"Unsupported Excel extension: {file_name}")

    # ── loader ────────────────────────────────────────────────────────────────
    def load_files(self):
        if not os.path.isdir(self.data_path):
            print(f"[WARN] Data directory not found: {self.data_path}")
            return

        for fname in os.listdir(self.data_path):
            fpath = os.path.join(self.data_path, fname)
            if not os.path.isfile(fpath) or fname.startswith("~$"):
                continue
            # Skip JSON files — they are playbook data, not tabular data
            if fname.lower().endswith(".json"):
                continue

            table_name = self.clean_table_name(fname)
            try:
                if fname.lower().endswith(".csv"):
                    self.con.execute(
                        f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM read_csv_auto(?)',
                        [fpath],
                    )
                    print(f"[OK] Loaded CSV: {fname} -> '{table_name}'")

                elif fname.lower().endswith((".xlsx", ".xls", ".xlsm")):
                    if os.path.getsize(fpath) == 0:
                        continue
                    sheet_dfs = self._read_excel(fpath, fname)
                    for sheet_name, df in sheet_dfs.items():
                        if df.empty:
                            continue
                        
                        for col in df.columns:
                            if "date" in str(col).lower():
                                df[col] = pd.to_datetime(df[col], errors="coerce")
                        
                        clean_sheet = self.clean_table_name(sheet_name)
                        final_table_name = clean_sheet if clean_sheet else table_name
                        
                        self.con.register("temp_df", df)
                        try:
                            self.con.execute(
                                f'CREATE OR REPLACE TABLE "{final_table_name}" AS SELECT * FROM temp_df'
                            )
                        finally:
                            try:
                                self.con.unregister("temp_df")
                            except Exception:
                                pass
                        print(f"[OK] Loaded Excel: {fname} (Sheet: {sheet_name}) -> '{final_table_name}'")
            except Exception as e:
                print(f"[ERROR] Loading {fname}: {e}")

    # ── public API ────────────────────────────────────────────────────────────
    def execute(self, query: str) -> pd.DataFrame:
        return self.con.execute(query).fetchdf()

    def list_tables(self) -> list[str]:
        return [t[0] for t in self.con.execute("SHOW TABLES").fetchall()]

    def get_schema(self, table_name: str) -> list[dict]:
        try:
            rows = self.con.execute(f'DESCRIBE "{table_name}"').fetchall()
            return [{"name": r[0], "type": r[1]} for r in rows]
        except Exception:
            return []

    def get_all_schemas(self) -> dict[str, list[dict]]:
        return {t: self.get_schema(t) for t in self.list_tables()}
