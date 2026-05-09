import pandas as pd

pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)

# === 日线 ===
df_day = pd.read_parquet("cta/feature/feature/day/CU0.parquet")
print(f"【日线】形状: {df_day.shape}")
print(f"列名: {list(df_day.columns)}\n")
print(df_day.head(3))
print(f"\nNaN比例:\n{df_day.isnull().mean().sort_values(ascending=False).head(10)}")

# === 分钟线 ===
df_min = pd.read_parquet("cta/feature/feature/minute/CU0.parquet")
print(f"\n【分钟线】形状: {df_min.shape}")
print(f"列名: {list(df_min.columns)}\n")
print(df_min.head(3))
print(f"\nNaN比例:\n{df_min.isnull().mean().sort_values(ascending=False).head(10)}")
