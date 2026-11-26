# RPMh RSC register layout (from Linux drivers/soc/qcom/rpmh-rsc.c)

Two versions exist; SDM845 uses **ver 2.7** (`drv->ver.major < 3`).

| Symbol | ver 2.7 | ver 3.0 |
|---|---|---|
| `RSC_DRV_IRQ_ENABLE` | 0x00 | 0x00 |
| `RSC_DRV_IRQ_STATUS` | 0x04 | 0x04 |
| `RSC_DRV_IRQ_CLEAR` | 0x08 | 0x08 |
| `RSC_DRV_CMD_WAIT_FOR_CMPL` | 0x10 | 0x20 |
| `RSC_DRV_CONTROL` | 0x14 | 0x24 |
| `RSC_DRV_STATUS` | 0x18 | 0x28 |
| `RSC_DRV_CMD_ENABLE` | 0x1C | 0x2C |
| `RSC_DRV_TCS_OFFSET` | **0x2A0** | **0x2A0** |
| `RSC_DRV_CMD_OFFSET` | **0x14** | **0x18** |
| `RSC_DRV_CMD_MSGID` | 0x30 | 0x34 |
| `RSC_DRV_CMD_ADDR` | 0x34 | 0x38 |
| `RSC_DRV_CMD_DATA` | 0x38 | 0x3C |
| `RSC_DRV_CMD_STATUS` | 0x3C | 0x40 |
| `RSC_DRV_CMD_RESP_DATA` | 0x40 | 0x44 |

Command encoding (`__tcs_buffer_write`):

```
msgid  = CMD_MSGID_LEN (8)
msgid |= wait_for_compl ? CMD_MSGID_RESP_REQ (0x100) : 0
msgid |= is_read ? 0 : CMD_MSGID_WRITE (0x10000)
```

Limits: `MAX_CMDS_PER_TCS = 16`, `MAX_TCS_PER_TYPE = 3`, `TCS_TYPE_NR = 4`
(ACTIVE / SLEEP / WAKE / CONTROL).

## Observed on this device (`=50`, 0x0B220000, readable from XNU)

```
000  00a200a8 00000000 00000000 00000000   <- IRQ_ENABLE = 0x00A200A8, others zero
010  00000000 ...                          <- WAIT_FOR_CMPL / CONTROL zero
01c  00000000                              <- CMD_ENABLE zero: nothing in flight
110-308  repeated 0x00000004
```

Nothing is pending, which matches the GPU being off. The repeated `4` from 0x110 to 0x308 is
not yet explained; it sits where the TCS arrays begin at 0x2A0, so the first thing to check
next time is whether a read of an idle TCS command slot returns a fixed pattern here.
