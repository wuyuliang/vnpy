"""
cta.data_code

商品期货数据下载与清洗脚本集合（代码目录）。
原始与清洗后的数据仍落盘到 cta/data/ 下（只读/追加）。

入口:
    - cta.data_code.download_all       —— 按 research_rank 批量下载
    - cta.data_code.futures_downloader —— 可复用下载器（离线批量 + 在线单次）
"""
