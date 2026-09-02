"""Build the DuckDB warehouse from the CSVs + metrics.sql views."""
import duckdb, pathlib

DB = "pc_portfolio.duckdb"
pathlib.Path(DB).unlink(missing_ok=True)
con = duckdb.connect(DB)
con.execute(pathlib.Path("sql/metrics.sql").read_text())
views = con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1").fetchall()
print("built:", ", ".join(v[0] for v in views))
con.close()
