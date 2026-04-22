每个品种一个 csv，建议列如下：

```csv
symbol,exchange,datetime,open,high,low,close,volume
RB99,SHFE,2020-01-02,3560,3588,3550,3579,123456
RB99,SHFE,2020-01-03,3582,3601,3570,3595,133210
```

说明：
- `symbol`：品种代码，例如 `RB99`
- `exchange`：交易所代码，必须是 vn.py 支持的枚举名之一，如 `SHFE/DCE/CZCE/INE/GFEX/CFFEX`
- `datetime`：日线日期，格式 `YYYY-MM-DD`
- `open/high/low/close/volume`：标准 OHLCV
- 如果没有 `symbol`，脚本会用文件名作为 symbol
- 如果没有 `exchange`，导入 vn.py 数据库时会报错，因为无法映射到交易所枚举
