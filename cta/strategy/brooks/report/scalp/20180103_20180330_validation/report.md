# Brooks 纯价格行为日内剥头皮回测报告

## 1. 最终状态和未通过项
- 状态：`INCONCLUSIVE`
- 日均 1% 目标未通过。
- 月均 20% 目标未通过。
- 至少一个必需分组 FAILED 或 INCONCLUSIVE。

## 2. 数据、实际日期和因果审计
```json
{
  "actual_end": "2018-03-30T15:00:00+08:00",
  "actual_start": "2018-01-03T09:01:00+08:00",
  "actual_symbol_ranges": {
    "CU0.SHFE": {
      "first_bar_end": "2018-01-03T09:01:00+08:00",
      "last_bar_end": "2018-03-30T15:00:00+08:00"
    },
    "RB0.SHFE": {
      "first_bar_end": "2018-01-03T09:01:00+08:00",
      "last_bar_end": "2018-03-30T15:00:00+08:00"
    }
  },
  "bar_timestamp_semantics": "source timestamp is bar_end; exact segment-open snapshots are excluded",
  "base_slippage_ticks": {
    "CU1802.SHF": 1.0,
    "CU1803.SHF": 1.0,
    "CU1804.SHF": 1.0,
    "CU1805.SHF": 1.0,
    "RB1805.SHF": 1.0,
    "RB1810.SHF": 1.0
  },
  "boundary_opening_snapshots_dropped": {
    "CU0.SHFE": 62,
    "RB0.SHFE": 62
  },
  "causal_audit_passed": true,
  "chart_source_interval_minutes": 1,
  "excluded_intrasession_roll_sessions": {
    "CU0.SHFE": [
      "20180202:night",
      "20180301:night"
    ],
    "RB0.SHFE": []
  },
  "ledger_reconciliation_passed": true,
  "metadata_manifest": {
    "created_at": "2026-08-09T21:53:04.849361+08:00",
    "files": {
      "contract_daily.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-04-16",
        "min_date": "2018-01-03",
        "rows": 4134,
        "sha256": "8993040b0a9d2c744b2f9c11d1cb3a53cb69b70fb0976631deccf92e56693560",
        "source_sha256": "8993040b0a9d2c744b2f9c11d1cb3a53cb69b70fb0976631deccf92e56693560",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "contract_specs.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-10-15",
        "min_date": "2018-02-09",
        "rows": 126,
        "sha256": "0491115784faa7b73c92d92745c0ab6d35a84e70370f246ae2f9c514171568df",
        "source_sha256": "0491115784faa7b73c92d92745c0ab6d35a84e70370f246ae2f9c514171568df",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "exchange_calendar.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-05-28",
        "min_date": "2017-11-27",
        "rows": 2061,
        "sha256": "2c047739c6230ffd57b315ec98e6f4d0d9a3b3b80a02f351f9f15669e4a4df50",
        "source_sha256": "2c047739c6230ffd57b315ec98e6f4d0d9a3b3b80a02f351f9f15669e4a4df50",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      },
      "fee_margin_schedule.csv": {
        "downloaded_at": "2026-08-09T21:53:04.849361+08:00",
        "max_date": "2026-04-15",
        "min_date": "2018-01-03",
        "rows": 4134,
        "sha256": "51d3aa81c12b0b12a2f1c3f526a2e6b4bbdfcff3988a529dabb11eadd02dbf83",
        "source_sha256": "51d3aa81c12b0b12a2f1c3f526a2e6b4bbdfcff3988a529dabb11eadd02dbf83",
        "source_url_or_file": "SHFE official kx/js + Tushare SHFE calendar/basic mirror"
      }
    },
    "schema_version": 1,
    "source_audit": {
      "limit_derivation": "spec_margin_minus_2pp_then_floor_both_prices_to_tick",
      "path": "shfe_source_audit.jsonl.gz",
      "sha256": "1fdc1a7824f1b33a7a48d0b7ce92941ced0e6f8d7649e1d455e9f0cfaaa4b961",
      "source_files": 4070
    }
  },
  "portfolio_excluded_roll_sessions": [
    "20180202:night",
    "20180301:night"
  ],
  "requested_end": "2018-03-30",
  "requested_start": "2018-01-03",
  "rollover_audit_passed": true,
  "source_files": {
    "CU0.SHFE": {
      "cta/data/origin/minute/CU0.SHF/2017-12-27.parquet": "8eaead82c3bcc5f37d17508bd28dd5283afb7cd801038ff6b3d36445c0e92146",
      "cta/data/origin/minute/CU0.SHF/2017-12-28.parquet": "134cca7231fda4ad5820fb252b3f6f45b4a69af4ef3b3208f9feee16bd8d4a1a",
      "cta/data/origin/minute/CU0.SHF/2017-12-29.parquet": "80f50aaf7f0ccd08beb8e5bdf763af13bf5e5c041c6e761a0d21ef5515c60541",
      "cta/data/origin/minute/CU0.SHF/2018-01-02.parquet": "475df3c5135619c9d654e4730a7c82986dd61cf063003f5aeba06d7f5f9b11eb",
      "cta/data/origin/minute/CU0.SHF/2018-01-03.parquet": "201afc3b4a7ca17e7f8b36504515897a56177427d772d379726c62351e760909",
      "cta/data/origin/minute/CU0.SHF/2018-01-04.parquet": "56dcb1b8e32a2c07e5e24274b463e84302a3c5a518f803dc780add75d4610d39",
      "cta/data/origin/minute/CU0.SHF/2018-01-05.parquet": "e2396f4089d8f3a0d73ddb49cc299f131550ac3d3fe0cd98987109dabcd1efa8",
      "cta/data/origin/minute/CU0.SHF/2018-01-08.parquet": "ebe0f857ada2f2aaf095ed146e6468643bb42f040c7cbffa86920b1b48bba4fa",
      "cta/data/origin/minute/CU0.SHF/2018-01-09.parquet": "abc1588c1814d90a5db81d7be86f6b825677462bb10d81e60f246224091751a9",
      "cta/data/origin/minute/CU0.SHF/2018-01-10.parquet": "c25e364b94b01955d1168053ce1105ce0f715e7e35177f25826bdc23ba9fbfba",
      "cta/data/origin/minute/CU0.SHF/2018-01-11.parquet": "abe61059d91267f2fd168e59787d141e160d7d0f9cae525a9f5371f803190697",
      "cta/data/origin/minute/CU0.SHF/2018-01-12.parquet": "a8d3ab0fab13b1cc6204f25ccd323560095e49016b75883ff02ac8a9c5d6fc7f",
      "cta/data/origin/minute/CU0.SHF/2018-01-15.parquet": "19dc3423f4482a4eb0d3b40b175aaf6bacfc5e3d0a2ee9dabafaab43aad241e1",
      "cta/data/origin/minute/CU0.SHF/2018-01-16.parquet": "70443d99f996e39e5882dd55657331d73be0ddaa60eefad262c2ea4e330146bd",
      "cta/data/origin/minute/CU0.SHF/2018-01-17.parquet": "640b732ba97202f06ded3d27b985891117c727b9a60efeca1e69c678c6a56f53",
      "cta/data/origin/minute/CU0.SHF/2018-01-18.parquet": "6e9a217bac684e38238d5cfe69e65706cbc2eb4521920fa7790ff61e33373439",
      "cta/data/origin/minute/CU0.SHF/2018-01-19.parquet": "0ac8de1644ee1df513636e9a73c68d4dce9b36a4d2038f3c09093b69e643e595",
      "cta/data/origin/minute/CU0.SHF/2018-01-22.parquet": "9305d3c2d0f8bc65e6b0209cf28771dd6849e9b8f40805155a5942e3cadeaae0",
      "cta/data/origin/minute/CU0.SHF/2018-01-23.parquet": "83bd097461eecf3d6e907376a02089fec99e218db7982d1b5d742518cfd6f66d",
      "cta/data/origin/minute/CU0.SHF/2018-01-24.parquet": "8b38f829d8a03e489d77a6420a6b1ad031fe0c2a2c1e5b2b0cc22d015b8b309d",
      "cta/data/origin/minute/CU0.SHF/2018-01-25.parquet": "832b0229ed032cd3711758e1a75381bf77a600eb2614f909127778e206af1026",
      "cta/data/origin/minute/CU0.SHF/2018-01-26.parquet": "e1ea6612c9ba4ace3b2c0bca77d0b3c681f6481fae2df78b0660b8442b47d515",
      "cta/data/origin/minute/CU0.SHF/2018-01-29.parquet": "21b51115d169a3f4acf9aeb906329e192158a393de4f94e0fae4847dd5903f7d",
      "cta/data/origin/minute/CU0.SHF/2018-01-30.parquet": "5b021dbd638d6183822c11472bc65e31acec9c96d087bb869de812d43d965853",
      "cta/data/origin/minute/CU0.SHF/2018-01-31.parquet": "f2b7d6965204ea97af0266e2051fffcc6622fa20210d511fabdc2f8adaad276c",
      "cta/data/origin/minute/CU0.SHF/2018-02-01.parquet": "f94bf0b9b6704c6337958ff555f3fe8a224d0122c9bf101829d9d215721c0c2d",
      "cta/data/origin/minute/CU0.SHF/2018-02-02.parquet": "19120dd1103182038d90fd8d5a892c6a2db889e12fea2a3d5f5a561312211b1c",
      "cta/data/origin/minute/CU0.SHF/2018-02-05.parquet": "6071c38579e89032754701621eb4cb454dda53a35c4e7ab7807602534ae7359c",
      "cta/data/origin/minute/CU0.SHF/2018-02-06.parquet": "843f84c26333856fab3df973335693bafb5bb0d3b8d924fa601eda842b3044bc",
      "cta/data/origin/minute/CU0.SHF/2018-02-07.parquet": "3b29ea4a7500d9c5db91d8e718bbfd9a992bef4ea0c2df1004cfd43ee6e496f1",
      "cta/data/origin/minute/CU0.SHF/2018-02-08.parquet": "a6542de83bd2871e1970f1d83b3ab850429862bc276321f93ff1c09dd060a34a",
      "cta/data/origin/minute/CU0.SHF/2018-02-09.parquet": "a4ae8da6fb776ea4867f6239bde2a6aa1d8d4479032231f3e553584b5b95dcba",
      "cta/data/origin/minute/CU0.SHF/2018-02-12.parquet": "06f01a3bc87f8eb02d3f8909a229112ef8de98a71fbdcf84a366d805faba9130",
      "cta/data/origin/minute/CU0.SHF/2018-02-13.parquet": "9ba1bfb093a276fcb72e0c4fec79667ac0f3c4aa5caab991960b9ebc3d9f02c3",
      "cta/data/origin/minute/CU0.SHF/2018-02-14.parquet": "1b8dd98c98794c750d69bc0cd0256c8d5f5f0a4b1c44181456085095775c92ea",
      "cta/data/origin/minute/CU0.SHF/2018-02-22.parquet": "c9671fd6c9292db70e641bbc03dfa5c2419b032a8e1abadd3f12eb60f0947ad3",
      "cta/data/origin/minute/CU0.SHF/2018-02-23.parquet": "fb80725642b964320ffa7450e31cdac8fddb96880bf681cc7c8435595f1327a5",
      "cta/data/origin/minute/CU0.SHF/2018-02-26.parquet": "74659f04448e7c70c5a664ecb4b1619eacc2d1af949a0996f983287668336530",
      "cta/data/origin/minute/CU0.SHF/2018-02-27.parquet": "f513e1e0b1d3a16ac5e606df40c0e9b485ec011aeaf6ddd0a0b9efea8a5176c2",
      "cta/data/origin/minute/CU0.SHF/2018-02-28.parquet": "16487e0089de89bce1b98c718836388d3af3bd589b7fcec2bc5eeec169d402a0",
      "cta/data/origin/minute/CU0.SHF/2018-03-01.parquet": "92ed926a215b20ecdd3debe219e3e5bac26e9e1329fdad922e78ade469897027",
      "cta/data/origin/minute/CU0.SHF/2018-03-02.parquet": "c5432ccff6c76bf195fd1d5f8cbe26ebd9a53959740965202d88ec69d9b8747c",
      "cta/data/origin/minute/CU0.SHF/2018-03-05.parquet": "81e147ce2ea8fc301dc681d20579b79b23a45bc849c673e237dbe250202c8e7e",
      "cta/data/origin/minute/CU0.SHF/2018-03-06.parquet": "85ef762bd4656e8171ce0e51b9134ef1cde292eda02064267c64490734aef18f",
      "cta/data/origin/minute/CU0.SHF/2018-03-07.parquet": "158433522127578167123faf0f95bed09030e12da4d9c3a21211824a5031e32f",
      "cta/data/origin/minute/CU0.SHF/2018-03-08.parquet": "781162fddd0584e233150df542aed505e81e48bcc24762c0afb838e14d0f5749",
      "cta/data/origin/minute/CU0.SHF/2018-03-09.parquet": "433dcebd0ad80d2a1672e9cd27ba618a0d3ad88fc1383a58ec761ed76d5985af",
      "cta/data/origin/minute/CU0.SHF/2018-03-12.parquet": "0ff658c0c22d6c00ccef9ade8675ac699efabef7a2ca76239a99323b438bc40a",
      "cta/data/origin/minute/CU0.SHF/2018-03-13.parquet": "75a2c66d0fe4ee09b31b282ef88bc8802f63bcda0333e45d10a1c74531b5856a",
      "cta/data/origin/minute/CU0.SHF/2018-03-14.parquet": "f12a6c19fa9e88e4b6b13f7fbc34a12d114001161c11fc90cf49620735ffeae1",
      "cta/data/origin/minute/CU0.SHF/2018-03-15.parquet": "669769384f95dc27e8e414d60eff5318ae99c1dc2ec53236c0899cd9f5933000",
      "cta/data/origin/minute/CU0.SHF/2018-03-16.parquet": "fd7eecd4a38126ac795f600d3add035dc0b83bc35a9ff638c438678354112d34",
      "cta/data/origin/minute/CU0.SHF/2018-03-19.parquet": "0bb6f3ac445915a073b290e83a72cc2e63bd451ff592b445529c480bf8258fb0",
      "cta/data/origin/minute/CU0.SHF/2018-03-20.parquet": "212570de77cf11f511d390c7e6b378439f56125a9f1a222dabcf9fea249e9a2f",
      "cta/data/origin/minute/CU0.SHF/2018-03-21.parquet": "ff12797cd15fc47464f554d3e8aeb3e10feee505e13f6988f97539d87a72ac89",
      "cta/data/origin/minute/CU0.SHF/2018-03-22.parquet": "6de3e86316d9fa039eb28158d3b14eff343115fb254a95e42ef61fb0a932cd89",
      "cta/data/origin/minute/CU0.SHF/2018-03-23.parquet": "4e459f5ab238f62ff196107c0804e019037b86fe2999fde071900de45b91fecb",
      "cta/data/origin/minute/CU0.SHF/2018-03-26.parquet": "67524724c0a8f9bc001df37822892ac56cb6c0862f4b2c10a116599fc30efa72",
      "cta/data/origin/minute/CU0.SHF/2018-03-27.parquet": "d8c5e3e7e14195667c12e4e6fced4d67fb7900ce53bebbb99f4f5c5d4961f165",
      "cta/data/origin/minute/CU0.SHF/2018-03-28.parquet": "11e734137e9adbcde143a4fe047a7108bd7c30e1640afc57119ba17e012d2414",
      "cta/data/origin/minute/CU0.SHF/2018-03-29.parquet": "b4d87fb75f2b01699d76907ca53d17f08aac83f1fe17e31bc148fa90f2b507f2",
      "cta/data/origin/minute/CU0.SHF/2018-03-30.parquet": "ea66ccf5c3644daf6f3343c1943d9de3ad94be5489f140f2adf50762eb364f3d"
    },
    "RB0.SHFE": {
      "cta/data/origin/minute/RB/2017-12-27.parquet": "84acc052530f2e60c69751e7485f62b323a2d272f1abdcaf3a88356326601b3e",
      "cta/data/origin/minute/RB/2017-12-28.parquet": "8b9d7f4754392f0f92fa4f0df5420510999cf2944a139365e5d746d2b6479ba6",
      "cta/data/origin/minute/RB/2017-12-29.parquet": "6deeeee4fcd9f24c8ffa3481a21fe86db6e4d3a4d50e7b3beb37a1265ea08f06",
      "cta/data/origin/minute/RB/2018-01-02.parquet": "f1eb966485583bdf059b13722346d8b9a9dcf5149b7e600aaf926ba9f0810cc1",
      "cta/data/origin/minute/RB/2018-01-03.parquet": "9e1d8d2045270044065879dbd2688dab109241728cd84aa4da812fc2f653d268",
      "cta/data/origin/minute/RB/2018-01-04.parquet": "e1c5f55cae90599a6587f7c3e592c2a0158ad362417bf721f1c7966965b7d756",
      "cta/data/origin/minute/RB/2018-01-05.parquet": "4d101cbb6444e70160a5d779a664d0cc2f439d2044a04b7d5f6b2210ec34e24f",
      "cta/data/origin/minute/RB/2018-01-08.parquet": "2e5d4d68a212550e4ab45da4171fd76b6da41bf9424b0b57f6b01963c9a08842",
      "cta/data/origin/minute/RB/2018-01-09.parquet": "47bdf367f3b4a1bdeae06aa1735db22b4f1d79cca091d62d9995b6312e3944bf",
      "cta/data/origin/minute/RB/2018-01-10.parquet": "f742ee2f4fea387028b05d5fad47eb1eb6818c5e1aa2e88d3c1a5cd0b2331769",
      "cta/data/origin/minute/RB/2018-01-11.parquet": "2109bdfccdead64b36a4de63de72be805790410d7eaa8654146967c025737b1e",
      "cta/data/origin/minute/RB/2018-01-12.parquet": "13fcbd1d1df0241bec6ccc32df4879fea9a881b6ad9e7d056e4250b6a002e47c",
      "cta/data/origin/minute/RB/2018-01-15.parquet": "9e7485547d5eb7c41ab466a2195ffd2074178562e5bddd5351a89454f38d6d2f",
      "cta/data/origin/minute/RB/2018-01-16.parquet": "1290dfbabac7f8e7015e2315c257da5ef5d7d3ac7ecd243dcc76c979db197e53",
      "cta/data/origin/minute/RB/2018-01-17.parquet": "f0de30e9b6db0d507bff030258d31aa07cd69df5d1df639f26a8a663d372a415",
      "cta/data/origin/minute/RB/2018-01-18.parquet": "82b71fe079b46d4aaa429b603622d771ffc9296d89af88f269e15024dafe8133",
      "cta/data/origin/minute/RB/2018-01-19.parquet": "8548067f8074dbe9004f1e785f3b262565fccce61788850a1d28ccd01eb26333",
      "cta/data/origin/minute/RB/2018-01-22.parquet": "42721292be44fcbd886b7b5c3f801c1f60782d9897b88a193e61554992c9cfe4",
      "cta/data/origin/minute/RB/2018-01-23.parquet": "9eecf62b20a67d1bfb05af5d6a364d9113a62a85c957630435811675ea9ae8d9",
      "cta/data/origin/minute/RB/2018-01-24.parquet": "02536e5d42876b3c8c08485298d9447895117dfffef0437008f1267cb6c1e6af",
      "cta/data/origin/minute/RB/2018-01-25.parquet": "6e18c2c01837b65407ee3d40376980c0effa0c4e3bc91958bc3a3cdfe76b1d31",
      "cta/data/origin/minute/RB/2018-01-26.parquet": "661bc7adc2c3abd61ef00b863c6971e271af344e1b73cab640af60c0804c95c1",
      "cta/data/origin/minute/RB/2018-01-29.parquet": "343dd4f454102294d6c0a5c81ee5bab3072ccc0625db334ad5fdf3a8803d09f9",
      "cta/data/origin/minute/RB/2018-01-30.parquet": "0f67816c085bdaebb3373c6f225735c7fe6682c7790a246d211646c78d1e6e20",
      "cta/data/origin/minute/RB/2018-01-31.parquet": "2ab887062de09c67129a9dc223646d5778b119e0652714f6f60e5464b74a9f3f",
      "cta/data/origin/minute/RB/2018-02-01.parquet": "419b421aa1552eb24c4274eb7fe8ae3fc320c8b80e744193d6c95eb59c9d02b3",
      "cta/data/origin/minute/RB/2018-02-02.parquet": "683cc1f16cbef02540f6b45d0f7b6e139d80f0bfb7abe9977f5b2fe0175b0aa3",
      "cta/data/origin/minute/RB/2018-02-05.parquet": "4473c166df51271990a310d16c6896ac38b562c7d1b8676b3481b9c758a304da",
      "cta/data/origin/minute/RB/2018-02-06.parquet": "4dd5c52c5c94988dec6a44637ce1a6e12b02cb4748ac346750b4916bf6c2219b",
      "cta/data/origin/minute/RB/2018-02-07.parquet": "6d38df5a90143407328aede9a1cbfbff69b0de8bc2a81a33cb24ffe603e014e6",
      "cta/data/origin/minute/RB/2018-02-08.parquet": "bc8dd70bb6c2ca6c73786ac54d347777d0c802b350c478c23841a2dfac83a294",
      "cta/data/origin/minute/RB/2018-02-09.parquet": "15cf40305c625fc72779a0664579f8a76fbc171f2d15b98dbd4bd48ff4ad1236",
      "cta/data/origin/minute/RB/2018-02-12.parquet": "4ed3608ea9a721600786adbc553cb0d077e087b79a05fdc522cdde6c47918c1b",
      "cta/data/origin/minute/RB/2018-02-13.parquet": "8157847f1c39796e15ae8293a57a805af658a3e03686dbaef86e96966e757a29",
      "cta/data/origin/minute/RB/2018-02-14.parquet": "9f23156d4f93aaac2eb859745f9f4b88be7aaf3bbc34be1418c351d375b7f5fe",
      "cta/data/origin/minute/RB/2018-02-22.parquet": "8456480c7eb80c8ca8f3486d91656265f2e1c8c9535a42b21785ad5930a34fc8",
      "cta/data/origin/minute/RB/2018-02-23.parquet": "5b9d0b0403315a06604a1b9ed401c0ade0e7baff1cd0b7e1498e191a67404b8a",
      "cta/data/origin/minute/RB/2018-02-26.parquet": "1cc6c81be912a512d1eb8924ace71b162a2a5e37b41739269e812921fb286c9a",
      "cta/data/origin/minute/RB/2018-02-27.parquet": "da7b279ecb10c563b568d73e4e0535860b014ecf6050816308b4dd5d0c99d0b1",
      "cta/data/origin/minute/RB/2018-02-28.parquet": "e1d47fbcb67447fa4228bc573d6bd360bf443d2cbd02b22b08a265f500f3fd4b",
      "cta/data/origin/minute/RB/2018-03-01.parquet": "87bcb5e9d9d3c3ecca1239288c97e90dd7817b43d574f20bfae78f98b3fc4814",
      "cta/data/origin/minute/RB/2018-03-02.parquet": "564f0f5cf31a7e6c4447af1b2bc7e60b07c9703199793214be32969ca633f4dc",
      "cta/data/origin/minute/RB/2018-03-05.parquet": "515bb526df271b39716b2ccfd9d5e77c4116fc4e78214520efc69e1a34122674",
      "cta/data/origin/minute/RB/2018-03-06.parquet": "1fa6e2e31acabae4a143d456bc88e89b580efe5839027aa32823f41859465a58",
      "cta/data/origin/minute/RB/2018-03-07.parquet": "264bc80aa492d7c8ffb95a0874605bb98b88a987c422c49f3366cdae0bcc0d8b",
      "cta/data/origin/minute/RB/2018-03-08.parquet": "4c0a8748588172814f806138c7b9518198a294132f30d31a73aca2f07b0e30e1",
      "cta/data/origin/minute/RB/2018-03-09.parquet": "f056eee10ed08b31a8bf2f5dc0a05c204fd96bb9131a2a0137d69a5aa11e7a2b",
      "cta/data/origin/minute/RB/2018-03-12.parquet": "de2881f2a60807918901fa910a0b3fb325e1432be3119a72375d882f74889548",
      "cta/data/origin/minute/RB/2018-03-13.parquet": "c1a35ffb60d15e1c9d286aeb9ea94175c6835d8d7c98c4dc52a6c2c8e0b16b04",
      "cta/data/origin/minute/RB/2018-03-14.parquet": "53b3af5106755f0d312d7890a7bc9b4c35cf8cbed7713611ee2fd938093a2f3d",
      "cta/data/origin/minute/RB/2018-03-15.parquet": "7940fe69c3dca86e59f35dcc5fc91063def566df52df08db1dbaa5181326b3a2",
      "cta/data/origin/minute/RB/2018-03-16.parquet": "dd3f57f9331b27bc2205e6d2abdaae45c373f82ab9f7ebe2a2482c6d34d4cea3",
      "cta/data/origin/minute/RB/2018-03-19.parquet": "4ee3b98a622ba6658ac0c14b61fb09c14d6a623eb9d9ae4b3da1fa0168ebd98a",
      "cta/data/origin/minute/RB/2018-03-20.parquet": "57b1e172df368bff8d43397790164d17bd4764174753065faa2b57a72915b181",
      "cta/data/origin/minute/RB/2018-03-21.parquet": "f4e4e7774ca9c4ea9e000c5216a8839aa4420ded3179d6895b972f99e67c86fe",
      "cta/data/origin/minute/RB/2018-03-22.parquet": "f811ec568197f61f7a5caefa9cc2aa8f3fba99a14687c107c789efeed4f70928",
      "cta/data/origin/minute/RB/2018-03-23.parquet": "198f52502fd9e3ef0d2021cd3771541247735f4ca7e50889164c746716d0efc7",
      "cta/data/origin/minute/RB/2018-03-26.parquet": "597f42810f163f500abe17e0882efaa3aa4f36aea022f299d82b421c247361cc",
      "cta/data/origin/minute/RB/2018-03-27.parquet": "023a431a48054b020d56208701db1e2819495eb54dabe93c4c898ed1b282c393",
      "cta/data/origin/minute/RB/2018-03-28.parquet": "1ee6e7938e822d449d5f266a9ab66acb8db8ef22d7a4396ac374529418d720e2",
      "cta/data/origin/minute/RB/2018-03-29.parquet": "b1ed537ec7c7c37fa4bfd9a6f48b9a432ab6a29503fce6162913ed6c24cb5bbc",
      "cta/data/origin/minute/RB/2018-03-30.parquet": "16db1b21ec3ad54c448d2bf209e9003e81ac7b926afd792cbd4558026536eee8"
    }
  },
  "source_hash_stability_passed": true,
  "symbols": [
    "RB0.SHFE",
    "CU0.SHFE"
  ],
  "vendor_midnight_timestamps_shifted": {
    "CU0.SHFE": 678,
    "RB0.SHFE": 0
  },
  "vendor_no_night_rows_dropped": {
    "CU0.SHFE": 428,
    "RB0.SHFE": 242
  },
  "vendor_zero_placeholders_dropped": {
    "CU0.SHFE": 0,
    "RB0.SHFE": 0
  }
}
```

## 3. RB 单品种结果
```json
{
  "equity_metrics": {
    "average_daily_return": 1.7814089877901195e-05,
    "average_monthly_return": 0.0003440183333333439,
    "daily_count": 58,
    "es99": 0.0006788400000000694,
    "max_drawdown": 0.0006788400000000694,
    "var99": 0.0002919012000000296,
    "worst_daily_return": -0.0006788400000000694
  },
  "final_equity": 200206.411,
  "risk_score": {
    "bootstrap_loss_probability": null,
    "items": [
      {
        "maximum": 10,
        "name": "max_drawdown",
        "passed": true,
        "points": 10
      },
      {
        "maximum": 8,
        "name": "margin_survival",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 7,
        "name": "bootstrap_survival",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "worst_day",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 6,
        "name": "var99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 6,
        "name": "es99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 8,
        "name": "fee_stress",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 8,
        "name": "slippage_stress",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 4,
        "name": "gap_limit_stress",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 5,
        "name": "risk_budgets",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "circuit_breakers",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "session_flat",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 4,
        "name": "positive_years",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 3,
        "name": "positive_quarters",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "rb_cu_survival",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 4,
        "name": "causal_audit",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 3,
        "name": "rollover_audit",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 3,
        "name": "ledger_audit",
        "passed": true,
        "points": 3
      }
    ],
    "score": 90,
    "status": "PASSED",
    "vetoes": []
  },
  "status": "INCONCLUSIVE",
  "trade_metrics": {
    "average_holding_1m_bars": 12.0,
    "average_mae_r": -0.20553626584187418,
    "average_mfe_r": 0.5036457453307166,
    "expected_pnl": 103.20550000000001,
    "expected_r": 0.3651042571477513,
    "loss_count": 1,
    "net_average_payoff": 2.520321430675859,
    "net_pnl": 206.41100000000003,
    "net_win_rate": 0.5,
    "profit_factor": 2.520321430675859,
    "sample_count": 2,
    "scratch_count": 0,
    "total_fee": 23.589000000000002,
    "total_slippage": 60.0,
    "wilson_high": 0.9054687942657693,
    "wilson_low": 0.09453120573423071,
    "win_count": 1
  }
}
```

## 4. CU 单品种结果
```json
{
  "equity_metrics": {
    "average_daily_return": 0.0,
    "average_monthly_return": 0.0,
    "daily_count": 58,
    "es99": 0.0,
    "max_drawdown": 0.0,
    "var99": 0.0,
    "worst_daily_return": 0.0
  },
  "final_equity": 200000.0,
  "risk_score": {
    "bootstrap_loss_probability": null,
    "items": [
      {
        "maximum": 10,
        "name": "max_drawdown",
        "passed": true,
        "points": 10
      },
      {
        "maximum": 8,
        "name": "margin_survival",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 7,
        "name": "bootstrap_survival",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "worst_day",
        "passed": true,
        "points": 8
      },
      {
        "maximum": 6,
        "name": "var99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 6,
        "name": "es99",
        "passed": true,
        "points": 6
      },
      {
        "maximum": 8,
        "name": "fee_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 8,
        "name": "slippage_stress",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 4,
        "name": "gap_limit_stress",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 5,
        "name": "risk_budgets",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "circuit_breakers",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 5,
        "name": "session_flat",
        "passed": true,
        "points": 5
      },
      {
        "maximum": 4,
        "name": "positive_years",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "positive_quarters",
        "passed": false,
        "points": 0
      },
      {
        "maximum": 3,
        "name": "rb_cu_survival",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 4,
        "name": "causal_audit",
        "passed": true,
        "points": 4
      },
      {
        "maximum": 3,
        "name": "rollover_audit",
        "passed": true,
        "points": 3
      },
      {
        "maximum": 3,
        "name": "ledger_audit",
        "passed": true,
        "points": 3
      }
    ],
    "score": 70,
    "status": "FAILED",
    "vetoes": []
  },
  "status": "INCONCLUSIVE",
  "trade_metrics": {
    "average_holding_1m_bars": 0.0,
    "average_mae_r": 0.0,
    "average_mfe_r": 0.0,
    "expected_pnl": 0.0,
    "expected_r": 0.0,
    "loss_count": 0,
    "net_average_payoff": 0.0,
    "net_pnl": 0.0,
    "net_win_rate": 0.0,
    "profit_factor": 0.0,
    "sample_count": 0,
    "scratch_count": 0,
    "total_fee": 0.0,
    "total_slippage": 0.0,
    "wilson_high": 0.0,
    "wilson_low": 0.0,
    "win_count": 0
  }
}
```

## 5. RB+CU 共享账户结果
```json
{
  "accepted_candidate_count": 2,
  "baselines": {
    "always_in_only": {
      "status": "NOT_ENABLED_IN_FROZEN_RULE_RUN"
    },
    "brooks_v3_existing": {
      "status": "NOT_COMPARABLE_OLD_ACCOUNTING"
    },
    "no_trade": {
      "net_pnl": 0.0,
      "status": "REFERENCE"
    },
    "scalp_rule_full": {
      "net_pnl": 206.41100000000003
    }
  },
  "candidate_count": 4,
  "config_sha256": "12105f911baf4d1e19f73fc26221dfffa4d0691b4e20b606412e05b4d8e7b4c5",
  "daily_target_passed": false,
  "independent_symbol_results": {
    "CU0.SHFE": {
      "equity_metrics": {
        "average_daily_return": 0.0,
        "average_monthly_return": 0.0,
        "daily_count": 58,
        "es99": 0.0,
        "max_drawdown": 0.0,
        "var99": 0.0,
        "worst_daily_return": 0.0
      },
      "final_equity": 200000.0,
      "risk_score": {
        "bootstrap_loss_probability": null,
        "items": [
          {
            "maximum": 10,
            "name": "max_drawdown",
            "passed": true,
            "points": 10
          },
          {
            "maximum": 8,
            "name": "margin_survival",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 7,
            "name": "bootstrap_survival",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "worst_day",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 6,
            "name": "var99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 6,
            "name": "es99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 8,
            "name": "fee_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "slippage_stress",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 4,
            "name": "gap_limit_stress",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 5,
            "name": "risk_budgets",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "circuit_breakers",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "session_flat",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 4,
            "name": "positive_years",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "positive_quarters",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "rb_cu_survival",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 4,
            "name": "causal_audit",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 3,
            "name": "rollover_audit",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 3,
            "name": "ledger_audit",
            "passed": true,
            "points": 3
          }
        ],
        "score": 70,
        "status": "FAILED",
        "vetoes": []
      },
      "status": "INCONCLUSIVE",
      "trade_metrics": {
        "average_holding_1m_bars": 0.0,
        "average_mae_r": 0.0,
        "average_mfe_r": 0.0,
        "expected_pnl": 0.0,
        "expected_r": 0.0,
        "loss_count": 0,
        "net_average_payoff": 0.0,
        "net_pnl": 0.0,
        "net_win_rate": 0.0,
        "profit_factor": 0.0,
        "sample_count": 0,
        "scratch_count": 0,
        "total_fee": 0.0,
        "total_slippage": 0.0,
        "wilson_high": 0.0,
        "wilson_low": 0.0,
        "win_count": 0
      }
    },
    "RB0.SHFE": {
      "equity_metrics": {
        "average_daily_return": 1.7814089877901195e-05,
        "average_monthly_return": 0.0003440183333333439,
        "daily_count": 58,
        "es99": 0.0006788400000000694,
        "max_drawdown": 0.0006788400000000694,
        "var99": 0.0002919012000000296,
        "worst_daily_return": -0.0006788400000000694
      },
      "final_equity": 200206.411,
      "risk_score": {
        "bootstrap_loss_probability": null,
        "items": [
          {
            "maximum": 10,
            "name": "max_drawdown",
            "passed": true,
            "points": 10
          },
          {
            "maximum": 8,
            "name": "margin_survival",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 7,
            "name": "bootstrap_survival",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 8,
            "name": "worst_day",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 6,
            "name": "var99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 6,
            "name": "es99",
            "passed": true,
            "points": 6
          },
          {
            "maximum": 8,
            "name": "fee_stress",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 8,
            "name": "slippage_stress",
            "passed": true,
            "points": 8
          },
          {
            "maximum": 4,
            "name": "gap_limit_stress",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 5,
            "name": "risk_budgets",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "circuit_breakers",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 5,
            "name": "session_flat",
            "passed": true,
            "points": 5
          },
          {
            "maximum": 4,
            "name": "positive_years",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 3,
            "name": "positive_quarters",
            "passed": false,
            "points": 0
          },
          {
            "maximum": 3,
            "name": "rb_cu_survival",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 4,
            "name": "causal_audit",
            "passed": true,
            "points": 4
          },
          {
            "maximum": 3,
            "name": "rollover_audit",
            "passed": true,
            "points": 3
          },
          {
            "maximum": 3,
            "name": "ledger_audit",
            "passed": true,
            "points": 3
          }
        ],
        "score": 90,
        "status": "PASSED",
        "vetoes": []
      },
      "status": "INCONCLUSIVE",
      "trade_metrics": {
        "average_holding_1m_bars": 12.0,
        "average_mae_r": -0.20553626584187418,
        "average_mfe_r": 0.5036457453307166,
        "expected_pnl": 103.20550000000001,
        "expected_r": 0.3651042571477513,
        "loss_count": 1,
        "net_average_payoff": 2.520321430675859,
        "net_pnl": 206.41100000000003,
        "net_win_rate": 0.5,
        "profit_factor": 2.520321430675859,
        "sample_count": 2,
        "scratch_count": 0,
        "total_fee": 23.589000000000002,
        "total_slippage": 60.0,
        "wilson_high": 0.9054687942657693,
        "wilson_low": 0.09453120573423071,
        "win_count": 1
      }
    }
  },
  "monthly_target_passed": false,
  "portfolio_metrics": {
    "average_daily_return": 1.7814089877901195e-05,
    "average_monthly_return": 0.0003440183333333439,
    "daily_count": 58,
    "es99": 0.0006788400000000694,
    "max_drawdown": 0.0006788400000000694,
    "var99": 0.0002919012000000296,
    "worst_daily_return": -0.0006788400000000694
  },
  "portfolio_status": "INCONCLUSIVE",
  "requested_symbols": [
    "RB0.SHFE",
    "CU0.SHFE"
  ],
  "research_only": true,
  "status": "INCONCLUSIVE",
  "strategy_version": "scalp-12105f911baf",
  "trade_count": 2,
  "trade_metrics": {
    "average_holding_1m_bars": 12.0,
    "average_mae_r": -0.20553626584187418,
    "average_mfe_r": 0.5036457453307166,
    "expected_pnl": 103.20550000000001,
    "expected_r": 0.3651042571477513,
    "loss_count": 1,
    "net_average_payoff": 2.520321430675859,
    "net_pnl": 206.41100000000003,
    "net_win_rate": 0.5,
    "profit_factor": 2.520321430675859,
    "sample_count": 2,
    "scratch_count": 0,
    "total_fee": 23.589000000000002,
    "total_slippage": 60.0,
    "wilson_high": 0.9054687942657693,
    "wilson_low": 0.09453120573423071,
    "win_count": 1
  }
}
```

## 6. Setup、方向、Session 和年度分组
详见 `group_metrics.csv`。

## 7. 风险和一票否决项
```json
{
  "bootstrap_loss_probability": null,
  "items": [
    {
      "maximum": 10,
      "name": "max_drawdown",
      "passed": true,
      "points": 10
    },
    {
      "maximum": 8,
      "name": "margin_survival",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 7,
      "name": "bootstrap_survival",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 8,
      "name": "worst_day",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 6,
      "name": "var99",
      "passed": true,
      "points": 6
    },
    {
      "maximum": 6,
      "name": "es99",
      "passed": true,
      "points": 6
    },
    {
      "maximum": 8,
      "name": "fee_stress",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 8,
      "name": "slippage_stress",
      "passed": true,
      "points": 8
    },
    {
      "maximum": 4,
      "name": "gap_limit_stress",
      "passed": true,
      "points": 4
    },
    {
      "maximum": 5,
      "name": "risk_budgets",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 5,
      "name": "circuit_breakers",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 5,
      "name": "session_flat",
      "passed": true,
      "points": 5
    },
    {
      "maximum": 4,
      "name": "positive_years",
      "passed": true,
      "points": 4
    },
    {
      "maximum": 3,
      "name": "positive_quarters",
      "passed": false,
      "points": 0
    },
    {
      "maximum": 3,
      "name": "rb_cu_survival",
      "passed": true,
      "points": 3
    },
    {
      "maximum": 4,
      "name": "causal_audit",
      "passed": true,
      "points": 4
    },
    {
      "maximum": 3,
      "name": "rollover_audit",
      "passed": true,
      "points": 3
    },
    {
      "maximum": 3,
      "name": "ledger_audit",
      "passed": true,
      "points": 3
    }
  ],
  "score": 90,
  "status": "PASSED",
  "symbols": {
    "CU0.SHFE": {
      "bootstrap_loss_probability": null,
      "items": [
        {
          "maximum": 10,
          "name": "max_drawdown",
          "passed": true,
          "points": 10
        },
        {
          "maximum": 8,
          "name": "margin_survival",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 7,
          "name": "bootstrap_survival",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "worst_day",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 6,
          "name": "var99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 6,
          "name": "es99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 8,
          "name": "fee_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "slippage_stress",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 4,
          "name": "gap_limit_stress",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 5,
          "name": "risk_budgets",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "circuit_breakers",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "session_flat",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 4,
          "name": "positive_years",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "positive_quarters",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "rb_cu_survival",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 4,
          "name": "causal_audit",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 3,
          "name": "rollover_audit",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 3,
          "name": "ledger_audit",
          "passed": true,
          "points": 3
        }
      ],
      "score": 70,
      "status": "FAILED",
      "vetoes": []
    },
    "RB0.SHFE": {
      "bootstrap_loss_probability": null,
      "items": [
        {
          "maximum": 10,
          "name": "max_drawdown",
          "passed": true,
          "points": 10
        },
        {
          "maximum": 8,
          "name": "margin_survival",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 7,
          "name": "bootstrap_survival",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 8,
          "name": "worst_day",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 6,
          "name": "var99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 6,
          "name": "es99",
          "passed": true,
          "points": 6
        },
        {
          "maximum": 8,
          "name": "fee_stress",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 8,
          "name": "slippage_stress",
          "passed": true,
          "points": 8
        },
        {
          "maximum": 4,
          "name": "gap_limit_stress",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 5,
          "name": "risk_budgets",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "circuit_breakers",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 5,
          "name": "session_flat",
          "passed": true,
          "points": 5
        },
        {
          "maximum": 4,
          "name": "positive_years",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 3,
          "name": "positive_quarters",
          "passed": false,
          "points": 0
        },
        {
          "maximum": 3,
          "name": "rb_cu_survival",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 4,
          "name": "causal_audit",
          "passed": true,
          "points": 4
        },
        {
          "maximum": 3,
          "name": "rollover_audit",
          "passed": true,
          "points": 3
        },
        {
          "maximum": 3,
          "name": "ledger_audit",
          "passed": true,
          "points": 3
        }
      ],
      "score": 90,
      "status": "PASSED",
      "vetoes": []
    }
  },
  "vetoes": []
}
```

## 8. 压力测试
```json
[
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.0434279044546472,
    "net_pnl": 182.822,
    "profit_factor": 2.2064591912152887,
    "scenario": "2x_fee",
    "trade_count": 2
  },
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.043463890583717336,
    "net_pnl": 86.41100000000003,
    "profit_factor": 1.400481072262801,
    "scenario": "3x_slippage",
    "trade_count": 2
  },
  {
    "forced_flat_failures": 0,
    "margin_breaches": 0,
    "max_margin_usage": 0.021405999999999998,
    "net_pnl": 0.0,
    "profit_factor": 0.0,
    "scenario": "gap_limit",
    "trade_count": 0
  }
]
```

## 9. 日均 1% 和月均 20% 目标检查
目标仅作为研究门槛，不构成收益承诺。详见 `summary.json`。

## 10. 局限和下一步
- 日均 1% 与月均 20% 是研究目标，不是收益承诺。
- 任一冻结分组样本少于 100 笔时结论保持 INCONCLUSIVE。
