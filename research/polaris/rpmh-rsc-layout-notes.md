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

## Exact addressing arithmetic (verbatim from rpmh-rsc.c)

```c
tcs_reg_addr(reg, tcs_id)      = drv->tcs_base + 672 * tcs_id + reg
tcs_cmd_addr(reg, tcs_id, cmd) = tcs_reg_addr(reg, tcs_id) + 20 * cmd   /* ver 2.7 */
```

- `drv->tcs_base` is the start of the TCS area of that DRV (base + 0x2A0 in the older layout).
- TCS stride is **672 (0x2A0)** for both versions.
- Command stride is **20 (0x14)** for 2.7, **24 (0x18)** for 3.0.

Writing one active vote:

```c
cmd_msgid = 8;                                  /* CMD_MSGID_LEN */
if (wait_for_compl) cmd_msgid |= 0x100;         /* RESP_REQ */
if (!is_read)       cmd_msgid |= 0x10000;       /* WRITE */

write(CMD_MSGID, tcs, cmd, cmd_msgid);
write(CMD_ADDR,  tcs, cmd, addr);               /* e.g. 0x30000 for cx.lvl */
write(CMD_DATA,  tcs, cmd, data);               /* level code from cmd_db aux data */
write(CMD_ENABLE, tcs, cmd_enable | BIT(cmd));  /* this is the trigger */
```

Then `IRQ_STATUS` (0x04) bit for that TCS sets when the RSC finished, and
`CMD_WAIT_FOR_CMPL` (0x10) tracks outstanding commands.

## Open question before the first vote

The `=50` dump does not fit this map yet:

```
0x000  IRQ_ENABLE  = 0x00A200A8   (plausible: several TCS enabled)
0x004  IRQ_STATUS  = 0
0x010  WAIT_FOR_CMPL = 0
0x01C  CMD_ENABLE  = 0
0x2A0..0x308       = 0x00000004 throughout
```

Under this layout `0x2BC` is CMD_ENABLE of TCS0 and must read 0, but the dump shows 4 there.
So either the DRV base is not at offset 0 within the RSC block, or this RSC uses a different
version than 2.7, or the TCS area starts elsewhere.

**Resolve that with reads alone before writing anything:** read `DRV_SOLVER_CONFIG` (0x04),
`DRV_PRNT_CHLD_CONFIG` (0x0C) and the DRV id at 0x00 of candidate bases 0x0, 0x1000, 0x2000,
0x3000 to find where a DRV actually begins. A vote written to a wrong base is a whole-device
power event, not a GPU one.
