# Bill of materials

Single source of truth is **`bom.csv`** (diffable, scriptable); the human-readable rendered BOM is
[`docs/hardware-bom.md`](../../docs/hardware-bom.md). **TODO — migrate the doc into the CSV.**

## Schema (`bom.csv`)

| column | meaning |
|---|---|
| `ref` | stable line id (e.g. `PW-01`) |
| `subsystem` | module ID prefix — `CH`/`DT`/`PW`/`CMP`/`CTL`/`SEN`/`SHL`/`MST` |
| `category` | `COTS` / `printed` / `machined` |
| `part` | name |
| `description` | short description |
| `qty` | quantity |
| `unit` | ea / m / etc. |
| `vendor` | supplier |
| `vendor_pn` | vendor part number |
| `unit_price_usd` | indicative unit price (USD) |
| `url` | product link |
| `alt_source` | alternate supplier/part |
| `cad_ref` | matching CAD component/module |
| `notes` | anything else |

Prices are **indicative and dated** (USD). A small build script sums extended price and flags
missing prices; add a `prices as of <date>` stamp when generating the rendered table.
